"""P0-2 regression: self-healing Optuna loop is report-first / gate-bound.

Directive Section 20: no autonomous parameter write without backtest proof.
The old loop wrote rsi_limit/cvd_filter_val directly via update_system_state,
bypassing param_gate. core.self_healing.evaluate_self_healing must:
  - apply NOTHING by default (report-first),
  - when auto_apply=True, write ONLY param_gate-approved changes,
  - fail safe (apply nothing) on any error or missing baseline.
"""
import pytest
import core.self_healing as sh


@pytest.fixture(autouse=True)
def _no_audit(monkeypatch):
    # keep tests hermetic — the audit log is best-effort and touches the DB
    monkeypatch.setattr(sh, "_audit", lambda *a, **k: None)


@pytest.fixture
def record_writes(monkeypatch):
    writes = []
    import database
    monkeypatch.setattr(database, "update_system_state", lambda k, v: writes.append((k, v)))
    return writes


def test_default_flag_is_false():
    import config
    assert getattr(config, "SELF_HEALING_AUTO_APPLY", None) is False


def test_report_first_by_default_writes_nothing(record_writes):
    res = sh.evaluate_self_healing({"rsi_limit": 55.0, "cvd_filter_val": 0.12}, auto_apply=False)
    assert res["applied"] == {}
    assert res["auto_apply"] is False
    assert record_writes == []          # nothing written to system_state


def test_none_reads_config_flag(monkeypatch, record_writes):
    import config
    monkeypatch.setattr(config, "SELF_HEALING_AUTO_APPLY", False, raising=False)
    res = sh.evaluate_self_healing({"rsi_limit": 55.0}, auto_apply=None)
    assert res["applied"] == {}
    assert record_writes == []


def test_applies_only_gate_approved(monkeypatch, record_writes):
    import database
    import core.param_gate as pg
    monkeypatch.setattr(database, "get_system_state", lambda k, default=None: "50.0")

    def fake_gate(key, old, new, days=30, environment=None):
        if key == "rsi_limit":
            return True, {"reason": "ok"}
        return False, {"reason": "expectancy dustu"}

    monkeypatch.setattr(pg, "validate_param_change", fake_gate)
    res = sh.evaluate_self_healing({"rsi_limit": 55.0, "cvd_filter_val": 0.12}, auto_apply=True)
    assert res["applied"] == {"rsi_limit": 55.0}        # only the approved key
    assert ("rsi_limit", "55.0") in record_writes
    assert all(k != "cvd_filter_val" for k, _ in record_writes)


def test_small_step_applied_value_is_written(monkeypatch, record_writes):
    import database
    import core.param_gate as pg
    monkeypatch.setattr(database, "get_system_state", lambda k, default=None: "50.0")
    # insufficient data -> gate approves a CAPPED value via report["applied_value"]
    monkeypatch.setattr(pg, "validate_param_change",
                        lambda *a, **k: (True, {"applied_value": 51.0, "insufficient_data": True}))
    res = sh.evaluate_self_healing({"rsi_limit": 80.0}, auto_apply=True)
    assert res["applied"] == {"rsi_limit": 51.0}        # capped value, not the raw 80.0
    assert ("rsi_limit", "51.0") in record_writes


def test_failsafe_on_gate_error(monkeypatch, record_writes):
    import database
    import core.param_gate as pg
    monkeypatch.setattr(database, "get_system_state", lambda k, default=None: "50.0")

    def boom(*a, **k):
        raise RuntimeError("gate exploded")

    monkeypatch.setattr(pg, "validate_param_change", boom)
    res = sh.evaluate_self_healing({"rsi_limit": 55.0}, auto_apply=True)
    assert res["applied"] == {}            # nothing applied on error
    assert record_writes == []


def test_no_baseline_skips_apply(monkeypatch, record_writes):
    import database
    monkeypatch.setattr(database, "get_system_state", lambda k, default=None: None)
    res = sh.evaluate_self_healing({"rsi_limit": 55.0}, auto_apply=True)
    assert res["applied"] == {}            # can't validate without baseline -> skip
    assert record_writes == []
