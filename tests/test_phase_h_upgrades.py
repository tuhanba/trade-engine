import pytest
import pandas as pd
from unittest.mock import MagicMock, patch
from core.trigger_engine import TriggerEngine
from core.cvd_engine import CVDEngine
from core.ai_decision_engine import AIDecisionEngine, SignalData, OrderFlowAgent, classify_signal
from core.risk_engine import RiskEngine, calculate_kelly_risk_pct
from execution_engine import ExecutionEngine

@pytest.fixture
def mock_client():
    return MagicMock()

def test_sfp_detection(mock_client):
    df = pd.DataFrame({
        "open": [100.0] * 50,
        "high": [101.0] * 50,
        "low": [99.0] * 50,
        "close": [100.0] * 50,
        "volume": [100.0] * 50,
        "tbbav": [50.0] * 50
    })
    
    df.loc[20, "low"] = 95.0
    for w in range(15, 26):
        if w != 20:
            df.loc[w, "low"] = 99.0

    df.loc[49, "low"] = 94.0
    df.loc[49, "close"] = 96.0

    engine = TriggerEngine(mock_client)
    is_sfp, level = engine._detect_sfp(df, "LONG")
    
    assert is_sfp is True
    assert level == 95.0

    df.loc[25, "high"] = 105.0
    for w in range(20, 31):
        if w != 25:
            df.loc[w, "high"] = 101.0
            
    df.loc[49, "high"] = 106.0
    df.loc[49, "close"] = 104.0
    
    is_sfp, level = engine._detect_sfp(df, "SHORT")
    assert is_sfp is True
    assert level == 105.0


def test_sfp_boost_classify_signal():
    sig = SignalData(
        symbol="BTCUSDT",
        side="LONG",
        entry_price=100.0,
        stop_loss=95.0,
        tp1=110.0,
        score=75.0,
        setup_quality="A",
        market_regime="NEUTRAL"
    )
    
    with patch("core.ai_decision_engine.TechnicalAgent.evaluate", return_value=("ALLOW", 75.0, "OK")), \
         patch("core.ai_decision_engine.SentimentAgent.evaluate", return_value=("ALLOW", 75.0, "OK")), \
         patch("core.ai_decision_engine.OrderFlowAgent.evaluate", return_value=("ALLOW", 75.0, "OK")):
         
         res_normal = classify_signal(sig, {"market_regime": "NEUTRAL"})
         assert res_normal.score_adjusted == 75.0
         assert sig.setup_quality == "A"
         
         sig.is_sfp = True
         res_sfp = classify_signal(sig, {"market_regime": "NEUTRAL"})
         assert res_sfp.score_adjusted == 87.0
         assert sig.setup_quality == "A+"


def test_mtf_cvd_absorption(mock_client):
    klines_mock = []
    for i in range(60):
        klines_mock.append([
            i, 100.0, 100.0, 100.0, 100.0, 100.0,
            i+1, 100.0, 10, 60.0, 60.0, 0
        ])
    mock_client.futures_klines.return_value = klines_mock
    
    cvd_engine = CVDEngine(mock_client)
    res = cvd_engine.analyze_mtf_cvd("BTCUSDT", "LONG")
    
    assert "cvd_absorption" in res
    assert "mtf_signals" in res
    assert res["buy_ratio"] == 0.6
    
    agent = OrderFlowAgent()
    sig = SignalData(symbol="BTCUSDT", side="LONG", score=70.0)
    
    with patch("core.cvd_engine.CVDEngine.analyze_mtf_cvd", return_value={
        "cvd_absorption": "BULLISH_ABSORPTION",
        "cvd_signal": "BULLISH",
        "cvd_score_bonus": 1.5,
        "cvd_divergence": False,
        "cvd_slope": 0.2,
        "buy_ratio": 0.6,
        "avg_buy_ratio": 0.6,
        "cvd_value": 1000.0
    }):
        vote, score, reasons = agent.evaluate(sig, {"client": mock_client})
        assert "Bullish CVD Absorption" in reasons
        assert score > 70.0


def test_progressive_drawdown_scaling(mock_client):
    risk_eng = RiskEngine(mock_client)
    
    with patch("sqlite3.connect") as mock_conn:
        mock_cursor = MagicMock()
        mock_conn.return_value.cursor.return_value = mock_cursor
        mock_conn.return_value.execute.return_value = mock_cursor
        
        mock_cursor.fetchone.side_effect = [
            (0.0,),  # daily_pnl
            (0.0,),  # weekly_pnl
        ]
        
        row_bal = MagicMock()
        row_bal.__getitem__.side_effect = lambda key: 2000.0 if key == "balance_after" else None
        
        row_perf = MagicMock()
        row_perf.__getitem__.side_effect = lambda key: 0.0 if key == "slippage" else (100 if key == "latency_ms" else None)
        
        mock_cursor.fetchall.side_effect = [
            [row_bal], # balance ledger
            [row_perf] # recent_perf
        ]
        
        with patch("database.get_open_trades", return_value=[]), \
             patch("database.get_system_state", return_value="-"), \
             patch("database.get_market_regime", return_value="NEUTRAL"), \
             patch("core.risk_engine.calculate_kelly_risk_pct", return_value=1.0), \
             patch("config.PROGRESSIVE_DRAWDOWN_ENABLED", True, create=True):
             
             res = risk_eng.calculate("BTCUSDT", "LONG", 100.0, "A", 1900.0, 75.0)
             assert res["valid"] is True
             assert 0.54 <= res["risk_pct"] <= 0.57


def test_kelly_safety_cap(mock_client):
    with patch("database.get_conn") as mock_conn:
        mock_cursor = MagicMock()
        mock_conn.return_value.__enter__.return_value = mock_cursor
        mock_cursor.execute.return_value = mock_cursor
        
        mock_cursor.fetchone.return_value = (10, 8, 10.0, 10.0)
        
        row_loss = MagicMock()
        row_loss.__getitem__.return_value = -50.0
        mock_cursor.fetchall.return_value = [row_loss, row_loss]
        
        capped_risk = calculate_kelly_risk_pct("BTCUSDT", 2.0, 1.0)
        assert capped_risk == 1.2


def test_spread_slippage_check_paper(mock_client):
    exec_eng = ExecutionEngine()
    
    sig = SignalData(
        symbol="BTCUSDT",
        side="LONG",
        entry_price=100.0,
        stop_loss=95.0,
        tp1=110.0,
        score=75.0
    )
    
    with patch("core.market_data.get_cached_ticker", return_value={"bid": 100.0, "ask": 100.2}), \
         patch("database.get_open_trades", return_value=[]), \
         patch("database.get_dashboard_stats", return_value={"balance": 2000.0}), \
         patch("config.PAPER_SPREAD_CHECK_ENABLED", True, create=True):
         
         res = exec_eng.open_paper_trade(sig)
         assert res is None
