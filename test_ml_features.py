import unittest
import os
import json
import sqlite3
from datetime import datetime, timezone
import database
from core.data_layer import SignalData
from core.ml_signal_scorer import score_signal, train_model
from execution_engine import ExecutionEngine

class TestMLFeatures(unittest.TestCase):
    def setUp(self):
        database.migrate_db()

    def test_metadata_propagation(self):
        # 1. Create a mock signal with advanced features
        sig_meta = {
            "adx": 30.0,
            "rv": 1.5,
            "rsi5": 60.0,
            "rsi1": 55.0,
            "ml_score": 75.0,
            "funding_favorable": 1,
            "bb_width_pct": 0.05,
            "ob_ratio": 1.2,
            "volume_m": 50.0,
            "btc_trend": "BULLISH",
            "session": "LONDON",
            "bb_width_chg": 0.01,
            "momentum_3c": 1.5,
            "prev_result": "WIN",
            "funding_rate": 0.0001,
            "cvd_value": 1000.0,
            "oi_change_pct": 3.5
        }
        sig = SignalData(
            symbol="BTCUSDT",
            side="LONG",
            entry_price=60000.0,
            stop_loss=59000.0,
            tp1=62000.0,
            setup_quality="A",
            final_score=80.0,
            metadata=sig_meta
        )

        # 2. Process signal to open a paper trade
        engine = ExecutionEngine()
        trade_id = engine.process_signal(sig)
        self.assertIsNotNone(trade_id)

        # Check DB to verify metadata matches sig_meta
        trade = database.get_trade_by_id(trade_id)
        self.assertIsNotNone(trade)
        
        meta = json.loads(trade.get("metadata", "{}"))
        self.assertEqual(meta.get("funding_rate"), 0.0001)
        self.assertEqual(meta.get("cvd_value"), 1000.0)
        self.assertEqual(meta.get("oi_change_pct"), 3.5)

        # 3. Test upsert_pattern_memory directly
        database.upsert_pattern_memory(
            pattern_hash="test_hash_val",
            outcome=1,
            r_multiple=1.5,
            features=sig_meta
        )
        
        # Fetch from DB to verify columns are saved
        conn = database.get_connection()
        conn.row_factory = sqlite3.Row
        row = conn.execute(
            "SELECT funding_rate, cvd_value, oi_change_pct FROM pattern_memory WHERE pattern_hash = 'test_hash_val'"
        ).fetchone()
        conn.close()
        self.assertIsNotNone(row)
        self.assertEqual(row["funding_rate"], 0.0001)
        self.assertEqual(row["cvd_value"], 1000.0)
        self.assertEqual(row["oi_change_pct"], 3.5)

    def test_ml_scorer_pipeline(self):
        # Verify that score_signal runs with 20 features
        test_sig = {
            "symbol": "BTCUSDT",
            "adx15": 25.0,
            "rv": 1.2,
            "rsi5": 50.0,
            "rsi1": 50.0,
            "funding_favorable": 1,
            "btc_trend": "BULLISH",
            "direction": "LONG",
            "momentum_3c": 1.0,
            "ob_ratio": 1.1,
            "bb_width": 0.03,
            "bb_width_chg": 0.005,
            "session": "LONDON",
            "prev_result": "NONE",
            "volume_m": 45.0,
            "funding_rate": 0.0002,
            "cvd_value": -500.0,
            "oi_change_pct": 2.5
        }
        score = score_signal(test_sig)
        self.assertTrue(0 <= score <= 100)

if __name__ == "__main__":
    unittest.main()
