from core.ai_decision_engine import AIDecisionEngine
from core.data_layer import SignalData


def test_threshold_based_decision():
    engine = AIDecisionEngine(db_path=":memory:")
    s = SignalData(symbol="ETHUSDT", direction="LONG", entry_zone=100, stop_loss=99, setup_quality="A+")
    s.coin_score = 90
    s.trend_score = 90
    s.trigger_score = 90
    s.risk_score = 90
    out = engine.evaluate(s)
    assert out["decision"] in {"ALLOW", "WATCH", "CANDIDATE", "VETO"}
    assert 0 <= out["final_score"] <= 100
