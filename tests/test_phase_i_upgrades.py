import pytest
import sqlite3
import os
import json
from unittest.mock import MagicMock, patch
from core.ai_decision_engine import classify_signal, SignalData, AIDecisionResult
from core.weight_tuner import tune_agent_weights

@pytest.fixture
def mock_client():
    return MagicMock()

def test_oi_squeeze_threshold_boost():
    # Base SignalData Setup
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
    
    # 1. Normal context (no OI spike)
    context_normal = {
        "oi_change_pct": 0.0,
        "fng_value": 50,
        "macro_sentiment": "neutral",
        "market_regime": "NEUTRAL"
    }
    
    # 2. Squeeze context with moderate OI spike (10.0% >= 8.0%)
    context_squeeze = {
        "oi_change_pct": 10.0,
        "fng_value": 50,
        "macro_sentiment": "neutral",
        "market_regime": "NEUTRAL"
    }

    # 3. Extreme squeeze context with extreme OI spike (15.0% >= 14.4%)
    context_extreme_squeeze = {
        "oi_change_pct": 15.0,
        "fng_value": 50,
        "macro_sentiment": "neutral",
        "market_regime": "NEUTRAL"
    }
    
    with patch("core.ai_decision_engine.TechnicalAgent.evaluate", return_value=("ALLOW", 80.0, "OK")), \
         patch("core.ai_decision_engine.SentimentAgent.evaluate", return_value=("ALLOW", 80.0, "OK")), \
         patch("core.ai_decision_engine.OrderFlowAgent.evaluate", return_value=("ALLOW", 80.0, "OK")), \
         patch("database.get_market_regime", return_value="NEUTRAL"), \
         patch("database.get_open_trades", return_value=[]), \
         patch("database.get_system_state", return_value="-"), \
         patch("websocket_events.event_manager.broadcast_agent_votes"), \
         patch("core.ai_decision_engine.GhostMemoryManager.get_symbol_ghost_stats", return_value={"total": 0, "tp_hits": 0, "sl_hits": 0, "ghost_winrate": 0.0}), \
         patch("core.ai_decision_engine.GhostMemoryManager.get_direction_bias", return_value={}), \
         patch("core.ai_decision_engine.GhostMemoryManager.get_score_multiplier", return_value=1.0):
             
         # A. Normal threshold 55.0, no boost. Score 80.0 >= 55.0 -> ALLOW
         with patch("config.TRADE_THRESHOLD", 55.0):
             res_normal = classify_signal(sig, context_normal)
             assert res_normal.decision == "ALLOW"
             
         # B. Normal threshold 78.0, boost +5.0 -> 83.0. Score 80.0 < 83.0 -> WATCH
         with patch("config.TRADE_THRESHOLD", 78.0):
             res_squeeze = classify_signal(sig, context_squeeze)
             assert res_squeeze.decision == "WATCH"
             
         # C. Normal threshold 72.0, boost +10.0 -> 82.0. Score 80.0 < 82.0 -> WATCH
         with patch("config.TRADE_THRESHOLD", 72.0):
             res_extreme = classify_signal(sig, context_extreme_squeeze)
             assert res_extreme.decision == "WATCH"


def test_weight_auto_tuner_optimization():
    db_file = "test_tuning.db"
    if os.path.exists(db_file):
        os.remove(db_file)
        
    try:
        # 1. Create a dummy test database schema
        conn = sqlite3.connect(db_file)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS signal_candidates (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                symbol          TEXT,
                side            TEXT,
                metadata        TEXT,
                market_regime   TEXT,
                status          TEXT,
                created_at      TEXT,
                linked_trade_id INTEGER
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS system_state (
                key             TEXT PRIMARY KEY,
                value           TEXT,
                updated_at      TEXT
            )
        """)
        
        # 2. Insert dummy resolved signals
        # Group 1: Choppy market candidates
        # Candidate 1: tech=80, flow=90, sent=40. Outcome: WIN
        conn.execute("""
            INSERT INTO signal_candidates (symbol, side, metadata, market_regime, status, created_at)
            VALUES ('BTCUSDT', 'LONG', ?, 'CHOPPY', 'TP_HIT', datetime('now'))
        """, (json.dumps({"tech_score": 80.0, "flow_score": 90.0, "sent_score": 40.0}),))
        
        # Candidate 2: tech=30, flow=20, sent=80. Outcome: LOSS
        conn.execute("""
            INSERT INTO signal_candidates (symbol, side, metadata, market_regime, status, created_at)
            VALUES ('BTCUSDT', 'LONG', ?, 'CHOPPY', 'SL_HIT', datetime('now'))
        """, (json.dumps({"tech_score": 30.0, "flow_score": 20.0, "sent_score": 80.0}),))
        
        conn.commit()
        conn.close()
        
        # 3. Run the weight tuner
        tuned = tune_agent_weights(db_path=db_file)
        
        # 4. Verify results
        assert "choppy" in tuned
        assert "w_flow" in tuned["choppy"]
        assert "w_tech" in tuned["choppy"]
        assert "w_sent" in tuned["choppy"]
        
        # Verify that weights were written to system_state
        with sqlite3.connect(db_file) as conn:
            conn.row_factory = sqlite3.Row
            wt = conn.execute("SELECT value FROM system_state WHERE key='weight_tech_choppy'").fetchone()
            wf = conn.execute("SELECT value FROM system_state WHERE key='weight_flow_choppy'").fetchone()
            ws = conn.execute("SELECT value FROM system_state WHERE key='weight_sent_choppy'").fetchone()
            
            assert wt is not None
            assert wf is not None
            assert ws is not None
            
            assert float(wt["value"]) + float(wf["value"]) + float(ws["value"]) == pytest.approx(1.0, 0.01)
        
    finally:
        import gc
        import time
        gc.collect()
        for _ in range(10):
            try:
                if os.path.exists(db_file):
                    os.remove(db_file)
                for ext in ["-wal", "-shm"]:
                    extra_file = db_file + ext
                    if os.path.exists(extra_file):
                        os.remove(extra_file)
                break
            except PermissionError:
                time.sleep(0.1)


def test_dynamic_weights_in_decision_engine():
    sig = SignalData(
        symbol="BTCUSDT",
        side="LONG",
        entry_price=100.0,
        stop_loss=95.0,
        tp1=110.0,
        score=70.0,
        setup_quality="A",
        market_regime="NEUTRAL"
    )
    
    context = {
        "oi_change_pct": 0.0,
        "fng_value": 50,
        "macro_sentiment": "neutral",
        "market_regime": "NEUTRAL"
    }
    
    # Mocking Technical, Sentiment, and OrderFlow agent evaluations to yield specific scores
    with patch("core.ai_decision_engine.TechnicalAgent.evaluate", return_value=("ALLOW", 80.0, "OK")), \
         patch("core.ai_decision_engine.SentimentAgent.evaluate", return_value=("ALLOW", 40.0, "OK")), \
         patch("core.ai_decision_engine.OrderFlowAgent.evaluate", return_value=("ALLOW", 90.0, "OK")), \
         patch("database.get_market_regime", return_value="NEUTRAL"), \
         patch("database.get_open_trades", return_value=[]), \
         patch("websocket_events.event_manager.broadcast_agent_votes"), \
         patch("core.ai_decision_engine.GhostMemoryManager.get_symbol_ghost_stats", return_value={"total": 0, "tp_hits": 0, "sl_hits": 0, "ghost_winrate": 0.0}), \
         patch("core.ai_decision_engine.GhostMemoryManager.get_direction_bias", return_value={}), \
         patch("core.ai_decision_engine.GhostMemoryManager.get_score_multiplier", return_value=1.0), \
         patch("database.get_system_state") as mock_get_state:
             
         # Mocking custom auto-tuned weights in system_state for NEUTRAL regime
         # w_tech = 0.50, w_flow = 0.40, w_sent = 0.10
         # Expected adjusted score: 80 * 0.50 + 90 * 0.40 + 40 * 0.10 = 40.0 + 36.0 + 4.0 = 80.0
         mock_get_state.side_effect = lambda key, default="-": {
             "weight_tech_neutral": "0.50",
             "weight_flow_neutral": "0.40",
             "weight_sent_neutral": "0.10"
         }.get(key, default)
         
         res = classify_signal(sig, context)
         assert res.score_adjusted == 80.0
