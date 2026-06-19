"""P0-4 regression: AI-evaluation fallback is mode-aware and fail-safe.

Directive Section 7.4:
  - PAPER mode on AI failure  -> decision WATCH, reason "ai_fallback_paper"
  - LIVE  mode on AI failure  -> decision VETO,  reason "ai_error_live_veto"
The old fallback returned "ALLOW if score>=30" regardless of mode, which could
let a live trade open from a failed evaluation. It must NEVER ALLOW on error.
"""
import core.ai_decision_engine as aide
from core.ai_decision_engine import AIDecisionEngine
from types import SimpleNamespace


def _force_mode(monkeypatch, mode):
    """Make config.__getattr__('EXECUTION_MODE') resolve to `mode`."""
    import config
    orig = config.__dict__["__getattr__"]
    monkeypatch.setattr(
        config, "__getattr__",
        lambda name: mode if name == "EXECUTION_MODE" else orig(name),
        raising=False,
    )


def _force_failure(monkeypatch):
    def _raise(*a, **k):
        raise RuntimeError("forced AI failure")
    monkeypatch.setattr(aide, "classify_signal", _raise)


def test_fallback_paper_returns_watch(monkeypatch):
    _force_failure(monkeypatch)
    _force_mode(monkeypatch, "paper")
    sig = SimpleNamespace(score=90.0)              # high score must NOT cause ALLOW
    res = AIDecisionEngine(db_path=":memory:").decide(sig)
    assert res["decision"] == "WATCH"
    assert res["reason"] == "ai_fallback_paper"


def test_fallback_live_returns_veto(monkeypatch):
    _force_failure(monkeypatch)
    _force_mode(monkeypatch, "live")
    sig = SimpleNamespace(score=90.0)
    res = AIDecisionEngine(db_path=":memory:").decide(sig)
    assert res["decision"] == "VETO"
    assert res["reason"] == "ai_error_live_veto"


def test_fallback_never_allows_regardless_of_score(monkeypatch):
    _force_failure(monkeypatch)
    for mode in ("paper", "live"):
        _force_mode(monkeypatch, mode)
        for sc in (0.0, 35.0, 100.0):
            res = AIDecisionEngine(db_path=":memory:").decide(SimpleNamespace(score=sc))
            assert res["decision"] != "ALLOW", f"mode={mode} score={sc} must never ALLOW"


def test_fallback_contract_keys_present(monkeypatch):
    _force_failure(monkeypatch)
    _force_mode(monkeypatch, "paper")
    res = AIDecisionEngine(db_path=":memory:").decide(SimpleNamespace(score=42.0))
    required = {"decision", "reason", "final_score", "confidence", "ai_score", "agent_data"}
    assert required <= res.keys()
    assert isinstance(res["agent_data"], dict)
