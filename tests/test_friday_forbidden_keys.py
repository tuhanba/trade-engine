"""P0-3 regression: autonomous actors cannot flip live-control / risk-shield keys.

Directive Section 12/18: Friday must not enable live trading or bypass risk
shields; "no component may bypass" the live gate. EXECUTION_MODE and
BYPASS_LIVE_RISK_SHIELDS live in config._DYNAMIC_PARAMS_MAP (so Friday's
_execute_decisions *could* apply them) — the guard must reject them while still
allowing genuine tunables like TRADE_THRESHOLD.
"""
import config


def test_policy_blocks_live_control_keys():
    for k in ["EXECUTION_MODE", "LIVE_TRADING_ENABLED", "DRY_RUN",
              "CONFIRM_LIVE_TRADING", "USE_BINANCE_PRIVATE_API",
              "BYPASS_LIVE_RISK_SHIELDS"]:
        assert config.is_autonomous_forbidden(k) is True
        assert config.is_autonomous_forbidden(k.lower()) is True   # case-insensitive


def test_policy_allows_tunable_keys():
    for k in ["TRADE_THRESHOLD", "RISK_PCT", "MAX_OPEN_TRADES", "TELEGRAM_THRESHOLD"]:
        assert config.is_autonomous_forbidden(k) is False


def test_friday_blocks_forbidden_keys(monkeypatch):
    from core.friday_ceo import FridayCeo
    from core import friday_decisions as fd

    monkeypatch.setattr(fd, "log_decision", lambda *a, **k: None, raising=False)

    ceo = object.__new__(FridayCeo)         # bypass heavy __init__
    ceo.db_path = ":memory:"

    msgs = ceo._execute_decisions(
        {"parameters": {"EXECUTION_MODE": "live", "BYPASS_LIVE_RISK_SHIELDS": "true"}}
    )

    # both keys rejected (⛔ ... reddedildi), neither applied (⚙️ ... →)
    assert any("EXECUTION_MODE" in m and "reddedildi" in m for m in msgs)
    assert any("BYPASS_LIVE_RISK_SHIELDS" in m and "reddedildi" in m for m in msgs)
    assert not any("→" in m for m in msgs)


def test_friday_still_applies_tunable_threshold(monkeypatch):
    from core.friday_ceo import FridayCeo
    from core import friday_decisions as fd
    import database

    monkeypatch.setattr(fd, "log_decision", lambda *a, **k: None, raising=False)
    monkeypatch.setattr(database, "get_state", lambda k: "60.0", raising=False)

    ceo = object.__new__(FridayCeo)
    ceo.db_path = ":memory:"
    applied = {}
    monkeypatch.setattr(
        ceo, "_apply_param_with_clamp",
        lambda db_key, v, **kw: (applied.__setitem__(db_key, v), v)[1],
        raising=False,
    )

    msgs = ceo._execute_decisions({"parameters": {"TRADE_THRESHOLD": "65"}})
    assert applied.get("trade_threshold") == 65.0     # genuine tunable still applied
    assert any("TRADE_THRESHOLD" in m and "→" in m for m in msgs)
