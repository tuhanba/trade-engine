from core.ai_decision_engine import AIDecisionEngine


def test_adaptive_engine_learns_without_crash():
    engine = AIDecisionEngine(db_path=":memory:")
    engine.learn_from_trade("ETHUSDT", "WIN", 10.0, "A+")
    engine.learn_from_trade("ETHUSDT", "LOSS", -5.0, "A+")
    assert engine.params["risk_pct"] > 0
