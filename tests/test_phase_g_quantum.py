"""
tests/test_phase_g_quantum.py - Unit tests for Phase G Quantum & Hedge Fund Automation Layer features.
"""

import os
import sys
import unittest
import numpy as np
from typing import Optional
from unittest.mock import MagicMock, patch

sys.path.insert(0, '.')

import config
import database
from core.online_learning import update_online_model, predict_online_probability, get_learner
from core.portfolio_risk import calculate_max_correlation, calculate_portfolio_var, calculate_sharpe_sortino_ratios
from core.voice_generator import generate_voice_briefing
from core.friday_ceo import FridayCeo


class TestPhaseGQuantum(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        database.init_db()

    def setUp(self):
        with database.get_conn() as conn:
            conn.execute("DELETE FROM trades")
            conn.execute("DELETE FROM pattern_memory")

    def test_sgd_online_learning_incremental_update(self):
        """Verify that SGD Online model can fit and predict probabilities incrementally."""
        learner = get_learner()
        # Mock features dictionary
        feat_dict = {
            "symbol": "BTCUSDT",
            "adx": 30.0,
            "rv": 1.2,
            "rsi5": 65.0,
            "rsi1": 60.0,
            "funding_favorable": 1,
            "bb_width_pct": 2.5,
            "ob_ratio": 1.5,
            "volume_m": 12.0,
            "btc_trend": "BULLISH",
            "direction": "LONG",
            "session": "LONDON",
            "hold_minutes": 45,
            "partial_exit": 0,
            "bb_width_chg": 0.5,
            "momentum_3c": 0.2,
            "prev_result": "WIN",
            "funding_rate": 0.0001,
            "cvd_value": 15000.0,
            "oi_change_pct": 1.5
        }
        
        # Train model with a few mock outcomes
        for i in range(5):
            update_online_model(feat_dict, 1) # WIN
            update_online_model(feat_dict, 0) # LOSS
            
        prob = predict_online_probability(feat_dict)
        self.assertTrue(0.0 <= prob <= 1.0)
        self.assertTrue(learner.trained)
        self.assertGreater(learner.n_samples, 0)

    @patch('core.portfolio_risk.get_klines')
    def test_pearson_correlation_blocker(self, mock_klines):
        """Verify that Pearson correlation is computed accurately based on return series."""
        # Setup mock returns
        # Simulating returns for BTCUSDT and ETHUSDT that are 100% correlated
        klines_btc = [[0, 0, 0, 0, 100 + i] for i in range(20)]
        klines_eth = [[0, 0, 0, 0, 10 + i/10] for i in range(20)]
        
        mock_klines.side_effect = lambda symbol, interval, limit: klines_btc if symbol == "BTCUSDT" else klines_eth
        
        corr = calculate_max_correlation(["BTCUSDT"], "ETHUSDT")
        # Since closes are perfectly linear positive returns, correlation should be 1.0
        self.assertAlmostEqual(corr, 1.0, places=2)

    @patch('core.portfolio_risk.get_klines')
    def test_portfolio_var_calculation(self, mock_klines):
        """Verify portfolio VaR calculates volatility risks correctly."""
        # Simulating price returns
        klines_btc = [[0, 0, 0, 0, 100 + (i % 2) * 5] for i in range(30)]
        klines_eth = [[0, 0, 0, 0, 50 - (i % 2) * 2] for i in range(30)]
        
        mock_klines.side_effect = lambda symbol, interval, limit: klines_btc if symbol == "BTCUSDT" else klines_eth
        
        open_pos = [{"symbol": "BTCUSDT", "qty": 1.0, "entry_price": 100.0}]
        new_pos = {"symbol": "ETHUSDT", "qty": 2.0, "entry_price": 50.0}
        
        var_val = calculate_portfolio_var(open_pos, new_pos, balance=10000.0)
        self.assertTrue(var_val >= 0.0)

    def test_sharpe_sortino_calculation(self):
        """Verify Sharpe & Sortino ratios are calculated correctly from database history."""
        # No trades -> returns 0
        ratios_empty = calculate_sharpe_sortino_ratios("paper")
        self.assertEqual(ratios_empty["sharpe_ratio"], 0.0)
        self.assertEqual(ratios_empty["sortino_ratio"], 0.0)
        
        # Insert mock daily closed trades
        with database.get_conn() as conn:
            # Day 1: +$100
            conn.execute("""
                INSERT INTO trades (id, symbol, direction, status, entry, sl, net_pnl, is_valid_for_stats, environment, open_time, close_time)
                VALUES (401, 'BTCUSDT', 'LONG', 'closed', 50000.0, 49000.0, 100.0, 1, 'paper', '2026-06-01 00:00:00', '2026-06-01 22:00:00')
            """)
            # Day 2: +$200
            conn.execute("""
                INSERT INTO trades (id, symbol, direction, status, entry, sl, net_pnl, is_valid_for_stats, environment, open_time, close_time)
                VALUES (402, 'ETHUSDT', 'LONG', 'closed', 3000.0, 2900.0, 200.0, 1, 'paper', '2026-06-02 00:00:00', '2026-06-02 22:00:00')
            """)
            # Day 3: -$50
            conn.execute("""
                INSERT INTO trades (id, symbol, direction, status, entry, sl, net_pnl, is_valid_for_stats, environment, open_time, close_time)
                VALUES (403, 'SOLUSDT', 'SHORT', 'closed', 150.0, 155.0, -50.0, 1, 'paper', '2026-06-03 00:00:00', '2026-06-03 22:00:00')
            """)
            
        ratios = calculate_sharpe_sortino_ratios("paper")
        # With mostly positive daily returns, Sharpe and Sortino ratios should be positive
        self.assertGreater(ratios["sharpe_ratio"], 0.0)
        self.assertGreater(ratios["sortino_ratio"], 0.0)

    @patch('core.voice_generator._generate_tts_async')
    def test_voice_note_briefing_generation(self, mock_tts):
        """Verify edge-tts voice briefs output structure."""
        mock_tts.return_value = None
        tmp_voice_path = "static/voice/test_friday_brief.ogg"
        
        res = generate_voice_briefing("Merhaba boss, piyasa harika görünüyor.", tmp_voice_path)
        self.assertTrue(res)
        mock_tts.assert_called_once()

    @patch('core.online_learning.SGDOnlineLearner.predict_proba')
    @patch('core.portfolio_risk.get_klines')
    def test_ml_online_gating(self, mock_klines, mock_predict):
        """Verify that online learner win probability below threshold rejects paper trades."""
        from core.data_layer import SignalData
        from execution_engine import ExecutionEngine
        
        mock_klines.return_value = [[0, 0, 0, 0, 100, 10, 0, 0, 0, 0, 0, 0] for _ in range(50)]
        
        # Setup mock learner to be trained and return low probability
        from core.online_learning import get_learner
        learner = get_learner()
        learner.trained = True
        mock_predict.return_value = 0.30  # 30% win prob (low, < 45% threshold)
        
        engine = ExecutionEngine()
        sig = SignalData(symbol="BTCUSDT", side="LONG", entry_price=50000.0, stop_loss=49500.0, leverage=10)
        
        # Should return None (rejected)
        trade_id = engine.open_paper_trade(sig)
        self.assertIsNone(trade_id)
        
        # Setup mock learner to return high probability
        mock_predict.return_value = 0.70  # 70% win prob (high)
        # Mock database insertion to avoid database lock issues in tests
        with patch('database.create_trade', return_value=123):
            # Because it is high probability, it should proceed
            with patch('execution_engine.get_current_price', return_value=50000.0):
                trade_id = engine.open_paper_trade(sig)
                self.assertIsNotNone(trade_id)

    @patch('core.redis_feature_store.set_features')
    def test_redis_features_caching(self, mock_set_features):
        """Verify that TriggerEngine.analyze calls set_features to cache features."""
        from core.trigger_engine import TriggerEngine
        
        mock_client = MagicMock()
        mock_client.futures_klines.return_value = [[0, 0, 102 + i, 98 + i, 100 + i, 10, 0, 0, 0, 0, 0, 0] for i in range(150)]
        mock_client.futures_ticker.return_value = {"quoteVolume": "5000000"}
        mock_client.futures_order_book.return_value = {"bids": [["99", "1"]], "asks": [["101", "1"]]}
        
        engine = TriggerEngine(mock_client)
        with patch.object(engine, '_rsi', return_value=55.0), \
             patch('core.ml_signal_scorer.score_signal', return_value=80.0), \
             patch('core.cvd_engine.CVDEngine.analyze', return_value={"cvd_slope": 1.0, "cvd_signal": "BULLISH", "cvd_score_bonus": 1.0, "cvd_value": 100.0}), \
             patch('core.oi_tracker.OITracker.analyze', return_value={"oi_signal": "BULLISH", "oi_score_bonus": 1.0, "oi_change_pct": 1.0}), \
             patch('core.macro_filter.MacroFilter.get_24h_funding_trend', return_value={"bias": "NEUTRAL", "avg_rate": 0.0001}), \
             patch('core.macro_filter.MacroFilter.get_8h_funding_average', return_value={"bias": "NEUTRAL", "avg_rate": 0.0001}):
            res = engine.analyze("BTCUSDT", "LONG", btc_trend="BULLISH")
            
        mock_set_features.assert_called_once()

    @patch('core.portfolio_risk.calculate_sharpe_sortino_ratios')
    def test_dynamic_risk_sizing_via_ratios(self, mock_ratios):
        """Verify that dynamic risk is scaled according to Sharpe & Sortino ratios."""
        from core.data_layer import SignalData
        from core.accounting import build_trade_from_signal
        
        sig = SignalData(symbol="BTCUSDT", side="LONG", entry_price=50000.0, stop_loss=49000.0, risk_pct=1.0, final_score=75.0)
        
        # Test Case 1: Excellent performance (Sharpe/Sortino = 2.5) -> risk boosted (1.2x)
        mock_ratios.return_value = {"sharpe_ratio": 2.5, "sortino_ratio": 2.5}
        trade = build_trade_from_signal(sig, 10000.0)
        self.assertAlmostEqual(trade.risk_pct, 1.2, places=2)
        
        # Test Case 2: Poor performance (Sharpe/Sortino = -0.5) -> risk scaled down (0.5x)
        mock_ratios.return_value = {"sharpe_ratio": -0.5, "sortino_ratio": -0.5}
        trade = build_trade_from_signal(sig, 10000.0)
        self.assertAlmostEqual(trade.risk_pct, 0.5, places=2)
        
        # Test Case 3: Low performance (Sharpe/Sortino = 0.3) -> risk scaled down (0.75x)
        mock_ratios.return_value = {"sharpe_ratio": 0.3, "sortino_ratio": 0.3}
        trade = build_trade_from_signal(sig, 10000.0)
        self.assertAlmostEqual(trade.risk_pct, 0.75, places=2)

    def test_sgd_online_model_backup_and_rollback(self):
        """Verify SGD online learning model backup and rollback functionality."""
        from core.online_learning import get_learner, backup_online_model, rollback_online_model
        
        learner = get_learner()
        learner.trained = True
        original_samples = learner.n_samples
        
        # Perform backup
        backup_file = backup_online_model()
        self.assertTrue(backup_file.startswith("sgd_online_model_"))
        self.assertTrue(backup_file.endswith(".pkl"))
        
        # Modify model state (increment sample count)
        learner.n_samples = original_samples + 999
        self.assertEqual(learner.n_samples, original_samples + 999)
        
        # Perform rollback
        success = rollback_online_model(backup_file)
        self.assertTrue(success)
        self.assertEqual(learner.n_samples, original_samples)
        
        # Clean up backup file
        backup_dir = os.path.join("core", "backups")
        backup_path = os.path.join(backup_dir, backup_file)
        if os.path.exists(backup_path):
            os.remove(backup_path)

    def test_redis_in_memory_buffering_fallback(self):
        """Verify that redis_state operates in-memory when Redis is offline."""
        from core import redis_state
        
        # Temporarily force redis availability to False to simulate offline
        original_available = redis_state._available
        redis_state._available = False
        
        try:
            # Set key with TTL
            success = redis_state.set("test_offline_key", {"status": "all_good"}, ttl=10)
            self.assertTrue(success)
            
            # Check existence and get value
            self.assertTrue(redis_state.exists("test_offline_key"))
            val = redis_state.get("test_offline_key")
            self.assertEqual(val, {"status": "all_good"})
            
            # Delete key
            success_del = redis_state.delete("test_offline_key")
            self.assertTrue(success_del)
            self.assertFalse(redis_state.exists("test_offline_key"))
        finally:
            # Restore original availability
            redis_state._available = original_available


if __name__ == "__main__":
    unittest.main()
