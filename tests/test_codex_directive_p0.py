from __future__ import annotations

from types import SimpleNamespace


def test_risk_calculate_accepts_atr_pct_and_returns_contract():
    from core.risk_engine import RiskEngine

    result = RiskEngine(None).calculate(
        "BTCUSDT",
        "LONG",
        100.0,
        "Z",
        1000.0,
        atr_pct=0.01,
    )

    required = {
        "valid",
        "reason",
        "risk_pct",
        "risk_usd",
        "entry",
        "stop",
        "tp1",
        "tp2",
        "tp3",
        "leverage",
        "position_size",
    }
    assert required <= result.keys()
    assert result["valid"] is False
    assert result["reason"] == result["risk_reject_reason"]


def test_ai_decide_alias_and_fallback_contract(monkeypatch):
    from core import ai_decision_engine
    from core.ai_decision_engine import AIDecisionEngine

    def _raise(*args, **kwargs):
        raise RuntimeError("forced")

    monkeypatch.setattr(ai_decision_engine, "classify_signal", _raise)
    signal = SimpleNamespace(score=42.0, trigger_score=4.0, trend_score=4.0, risk_score=4.0)

    result = AIDecisionEngine(db_path=":memory:").decide(signal)

    assert result["decision"] in {"ALLOW", "WATCH", "VETO"}
    assert {"decision", "reason", "final_score", "confidence", "agent_data"} <= result.keys()
    assert isinstance(result["agent_data"], dict)


def test_why_no_trade_summary_uses_terminal_signal_events(test_db):
    from scripts.why_no_trade import collect

    test_db.save_signal_event("sig-1", "SCANNED", symbol="BTCUSDT", reason="scanner_pass")
    test_db.save_signal_event("sig-1", "TREND_REJECTED", symbol="BTCUSDT", reject_reason="weak_trend")
    test_db.save_signal_event("sig-2", "SCANNED", symbol="ETHUSDT", reason="scanner_pass")
    test_db.save_signal_event("sig-2", "RISK_REJECTED", symbol="ETHUSDT", reject_reason="low_rr")
    test_db.save_signal_event("sig-3", "PAPER_OPENED", symbol="SOLUSDT", reason="trade_id=1")

    result = collect(limit=10)

    assert result["summary"]["TREND_REJECTED"] == 1
    assert result["summary"]["RISK_REJECTED"] == 1
    assert result["summary"]["PAPER_OPENED"] == 1


def test_paper_execution_without_private_client(test_db, monkeypatch):
    import config
    import execution_engine
    from core.data_layer import SignalData
    from execution_engine import ExecutionEngine

    config.EXECUTION_MODE = "paper"
    config.LIVE_TRADING_ENABLED = False
    config.CONFIRM_LIVE_TRADING = False
    config.DRY_RUN = True
    monkeypatch.setattr(execution_engine, "get_current_price", lambda symbol: 100.0)

    signal = SignalData(
        symbol="BTCUSDT",
        side="LONG",
        entry_price=100.0,
        stop_loss=99.0,
        tp1=102.0,
        tp2=104.0,
        tp3=108.0,
        score=75.0,
        final_score=75.0,
        leverage=5,
        risk_pct=1.0,
        source="force_test",
        metadata={},
    )

    trade_id = ExecutionEngine().process_signal(signal)

    assert trade_id
    trade = test_db.get_trade_by_id(trade_id)
    assert trade["symbol"] == "BTCUSDT"
    assert trade["environment"] == "paper"
    test_db.update_trade(trade_id, {"status": "closed"})
