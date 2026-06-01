import unittest
from unittest.mock import MagicMock, patch
import io
import sys
import os

# Add root folder to path to import core modules
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from core.data_layer import SignalData
from core.signal_visualizer import generate_chart_bytes
from core.live_execution import LiveExecutionEngine
from core.ai_decision_engine import classify_signal, AIDecisionResult, TechnicalAgent, SentimentAgent, OrderFlowAgent

class TestAdvancedFeatures(unittest.TestCase):

    # -------------------------------------------------------------------------
    # Feature 1: Telegram Signal Chart Visualizer tests
    # -------------------------------------------------------------------------
    @patch('matplotlib.pyplot.savefig')
    def test_generate_chart_bytes_success(self, mock_savefig):
        # Mock savefig to write dummy bytes
        def side_effect(buf, *args, **kwargs):
            buf.write(b"MOCK_PNG_DATA")
        mock_savefig.side_effect = side_effect

        mock_client = MagicMock()
        mock_client.futures_klines.return_value = [
            [1716300000000 + i * 300000, "100", "105", "95", str(100 + i % 5), "1000", 
             1716300300000, "100000", 100, "500", "50000", "0"]
            for i in range(40)
        ]

        png_bytes = generate_chart_bytes(
            symbol="BTCUSDT",
            entry=100.0,
            sl=95.0,
            tp1=105.0,
            tp2=110.0,
            tp3=115.0,
            direction="LONG",
            client=mock_client
        )
        self.assertIsNotNone(png_bytes)
        self.assertEqual(png_bytes, b"MOCK_PNG_DATA")
        mock_client.futures_klines.assert_called_once_with(symbol="BTCUSDT", interval="5m", limit=40)

    # -------------------------------------------------------------------------
    # Feature 2: Limit Chase (Slippage-Reducing Order Follower) tests
    # -------------------------------------------------------------------------
    @patch('core.live_execution.LiveExecutionEngine._init_client')
    def test_limit_chase_full_immediate_fill(self, mock_init_client):
        # Setup engine with mock client
        engine = LiveExecutionEngine()
        engine.client = MagicMock()
        
        # Format methods
        engine.exchange_info = {
            "BTCUSDT": {
                "price_precision": 2,
                "quantity_precision": 3,
                "tick_size": 0.01,
                "step_size": 0.001
            }
        }
        
        # Mock order book: bid 99.0, ask 100.0
        engine.client.futures_order_book.return_value = {
            "bids": [["99.00", "1.0"]],
            "asks": [["100.00", "1.0"]]
        }
        
        # Mock order placement: first LIMIT order is placed and filled immediately
        engine.client.futures_create_order.return_value = {"orderId": 12345}
        engine.client.futures_get_order.return_value = {
            "status": "FILLED",
            "executedQty": "0.050",
            "avgPrice": "99.00"
        }
        
        res = engine._execute_chase_limit_order("BTCUSDT", "BUY", "0.050", 100.0, 0.15)
        self.assertIsNotNone(res)
        self.assertEqual(res["avgPrice"], 99.0)
        self.assertEqual(res["executedQty"], 0.05)
        self.assertEqual(res["orderId"], 12345)
        self.assertEqual(len(res["orderIds"]), 1)

    @patch('core.live_execution.LiveExecutionEngine._init_client')
    @patch('time.sleep') # speed up test execution
    def test_limit_chase_partial_fill_chase_then_market_fallback(self, mock_sleep, mock_init_client):
        engine = LiveExecutionEngine()
        engine.client = MagicMock()
        
        engine.exchange_info = {
            "BTCUSDT": {
                "price_precision": 2,
                "quantity_precision": 3,
                "tick_size": 0.01,
                "step_size": 0.001
            }
        }
        
        # Mock order book changes:
        # First check: bid 99.0, ask 100.0 (Target: 99.0, within bound 100.15)
        # Second check: bid 100.5, ask 101.0 (exceeds bound 100.15)
        engine.client.futures_order_book.side_effect = [
            {"bids": [["99.00", "1.0"]], "asks": [["100.00", "1.0"]]},
            {"bids": [["100.50", "1.0"]], "asks": [["101.00", "1.0"]]}
        ]
        
        # Create orders sequence:
        # 1. LIMIT order for 0.050 @ 99.00 (orderId: 111)
        # 2. MARKET order for remaining 0.030 (orderId: 222)
        engine.client.futures_create_order.side_effect = [
            {"orderId": 111}, # Limit order
            {"orderId": 222, "executedQty": "0.030", "avgPrice": "100.50"} # Market fallback
        ]
        
        # Get order status sequence:
        # 1. For Limit Order (111): status is PARTIALLY_FILLED, executedQty: 0.020, avgPrice: 99.00
        # 2. For final status query after cancel: executedQty: 0.020, avgPrice: 99.00
        engine.client.futures_get_order.side_effect = [
            {"status": "PARTIALLY_FILLED", "executedQty": "0.020", "avgPrice": "99.00"}, # first status query
            {"status": "CANCELED", "executedQty": "0.020", "avgPrice": "99.00"} # after cancel query
        ]
        
        res = engine._execute_chase_limit_order("BTCUSDT", "BUY", "0.050", 100.0, 0.15)
        self.assertIsNotNone(res)
        # Total filled = 0.02 (limit) + 0.03 (market) = 0.05
        # Total spend = (0.02 * 99.0) + (0.03 * 100.5) = 1.98 + 3.015 = 4.995
        # Avg price = 4.995 / 0.05 = 99.90
        self.assertAlmostEqual(res["avgPrice"], 99.90, places=4)
        self.assertEqual(res["executedQty"], 0.05)
        self.assertEqual(res["orderIds"], [111, 222])
        engine.client.futures_cancel_order.assert_called_once_with(symbol="BTCUSDT", orderId=111)

    # -------------------------------------------------------------------------
    # Feature 3: Multi-Agent Consensus tests
    # -------------------------------------------------------------------------
    @patch('database.get_open_trades')
    @patch('core.ai_decision_engine._get_ghost_manager')
    def test_multi_agent_consensus_veto(self, mock_get_ghost, mock_get_open_trades):
        mock_get_open_trades.return_value = []
        # Setup mock ghost manager stats
        mock_ghost = MagicMock()
        mock_ghost.get_symbol_ghost_stats.return_value = {"total": 0, "ghost_winrate": 0.0, "tp_hits": 0, "sl_hits": 0}
        mock_get_ghost.return_value = mock_ghost
        
        # Test Case 1: SentimentAgent VETOs (e.g. Extreme Fear on LONG)
        signal = SignalData(
            symbol="BTCUSDT",
            direction="LONG",
            entry_price=100.0,
            stop_loss=90.0,
            tp1=120.0,
            score=80.0 # High base score
        )
        # Context with extreme fear
        context = {"fng_value": 15}
        
        result = classify_signal(signal, context)
        self.assertEqual(result.decision, "VETO")
        self.assertIn("Sentiment:", result.reason)

        # Test Case 2: TechnicalAgent VETOs (e.g. Trend conflict)
        signal = SignalData(
            symbol="BTCUSDT",
            direction="LONG",
            entry_price=100.0,
            stop_loss=90.0,
            tp1=120.0,
            score=45.0 # Low base score
        )
        # Trend conflict
        context = {"market_trend": "bearish", "fng_value": 50}
        result = classify_signal(signal, context)
        self.assertEqual(result.decision, "VETO")
        self.assertIn("Tech: Trend conflict", result.reason)

    @patch('database.get_open_trades')
    @patch('core.ai_decision_engine._get_ghost_manager')
    def test_multi_agent_consensus_allow(self, mock_get_ghost, mock_get_open_trades):
        mock_get_open_trades.return_value = []
        mock_ghost = MagicMock()
        mock_ghost.get_symbol_ghost_stats.return_value = {"total": 0, "ghost_winrate": 0.0, "tp_hits": 0, "sl_hits": 0}
        mock_get_ghost.return_value = mock_ghost

        # All agents agree and ALLOW
        signal = SignalData(
            symbol="BTCUSDT",
            direction="LONG",
            entry_price=100.0,
            stop_loss=90.0,
            tp1=120.0,
            score=95.0
        )
        context = {
            "market_trend": "bullish",
            "fng_value": 60,
            "cvd_delta": 1000.0,
            "open_interest_trend": "rising",
            "orderbook_ratio": 1.5
        }
        
        result = classify_signal(signal, context)
        # Verify that score is high enough and decision is ALLOW
        self.assertEqual(result.decision, "ALLOW")
        self.assertTrue(result.score_adjusted > 70.0)

    # -------------------------------------------------------------------------
    # Gating and Macro Filter tests (Faz C2)
    # -------------------------------------------------------------------------
    def test_model_gating_mechanism(self):
        from core.ml_signal_scorer import MLSignalScorer
        scorer = MLSignalScorer()
        
        # Setup mock training data to bypass real DB
        scorer._load_training_data = MagicMock(return_value=[
            [35, 1.5, 50, 50, 1, 0.01, 1.0, 10.0, "NEUTRAL", "LONG", "OFF", 0, 0, "BTCUSDT", "WIN" if i % 2 == 0 else "LOSS", "2026-06-01T00:00:00", 0, 0, "NONE", 0.0, 0.0, 0.0]
            for i in range(40) # > MIN_TRAIN_SAMPLES (30)
        ])
        
        # Test case 1: Initial training setting self.cv_accuracy
        scorer.trained = False
        scorer.model = None
        scorer.cv_accuracy = 0.0
        
        import numpy as np
        # Mock StratifiedKFold and cross_val_score to return high accuracy
        with patch('sklearn.model_selection.cross_val_score') as mock_cv:
            mock_cv.return_value = np.array([0.85, 0.85, 0.85])
            # Mock fit & save
            scorer._save_model = MagicMock()
            success = scorer.train()
            self.assertTrue(success)
            self.assertEqual(scorer.cv_accuracy, 0.85)
            self.assertTrue(scorer.trained)
            
        # Test case 2: Retraining with degradation (ROC-AUC drops to 0.70 < 0.85 * 0.97)
        with patch('sklearn.model_selection.cross_val_score') as mock_cv:
            mock_cv.return_value = np.array([0.70, 0.70, 0.70])
            success = scorer.train()
            # Must return False (rejected) and keep old cv_accuracy
            self.assertFalse(success)
            self.assertEqual(scorer.cv_accuracy, 0.85)

        # Test case 3: Retraining with acceptable change (ROC-AUC 0.84 >= 0.85 * 0.97)
        with patch('sklearn.model_selection.cross_val_score') as mock_cv:
            mock_cv.return_value = np.array([0.84, 0.84, 0.84])
            success = scorer.train()
            self.assertTrue(success)
            self.assertEqual(scorer.cv_accuracy, 0.84)

    @patch('database.get_open_trades')
    @patch('core.ai_decision_engine._get_ghost_manager')
    def test_macro_funding_and_oi_spike_veto(self, mock_get_ghost, mock_get_open_trades):
        mock_get_open_trades.return_value = []
        mock_ghost = MagicMock()
        mock_ghost.get_symbol_ghost_stats.return_value = {"total": 0, "ghost_winrate": 0.0, "tp_hits": 0, "sl_hits": 0}
        mock_get_ghost.return_value = mock_ghost

        # Test Case 1: Extreme 8-hour positive funding rate on LONG -> VETO
        signal = SignalData(
            symbol="BTCUSDT",
            side="LONG",
            direction="LONG",
            entry_price=100.0,
            stop_loss=90.0,
            tp1=120.0,
            score=85.0
        )
        context = {
            "funding_rate_8h": 0.00035, # EXTREME_GREED (>=0.0003)
            "fng_value": 50
        }
        result = classify_signal(signal, context)
        self.assertEqual(result.decision, "VETO")
        self.assertIn("Extreme 8h positive funding rate", result.reason)

        # Test Case 2: Extreme 8-hour negative funding rate on SHORT -> VETO
        signal = SignalData(
            symbol="BTCUSDT",
            side="SHORT",
            direction="SHORT",
            entry_price=100.0,
            stop_loss=110.0,
            tp1=80.0,
            score=85.0
        )
        context = {
            "funding_rate_8h": -0.00035, # EXTREME_FEAR (<= -0.0003)
            "fng_value": 50
        }
        result = classify_signal(signal, context)
        self.assertEqual(result.decision, "VETO")
        self.assertIn("Extreme 8h negative funding rate", result.reason)

        # Test Case 3: Extreme Open Interest Spike (>= 12%) -> VETO
        signal = SignalData(
            symbol="BTCUSDT",
            side="LONG",
            direction="LONG",
            entry_price=100.0,
            stop_loss=90.0,
            tp1=120.0,
            score=90.0
        )
        context = {
            "oi_change_pct": 13.5, # > 12.0
            "fng_value": 50
        }
        result = classify_signal(signal, context)
        self.assertEqual(result.decision, "VETO")
        self.assertIn("Extreme Open Interest Spike", result.reason)

        # Test Case 4: OI Spike (> 8%) with opposite CVD divergence -> VETO/WATCH (points deducted)
        signal = SignalData(
            symbol="BTCUSDT",
            side="LONG",
            direction="LONG",
            entry_price=100.0,
            stop_loss=90.0,
            tp1=120.0,
            score=75.0
        )
        context = {
            "oi_change_pct": 9.0, # > 8.0
            "cvd_delta": -500.0, # opposite to direction (LONG)
            "fng_value": 50
        }
        result = classify_signal(signal, context)
        self.assertIn(result.decision, ["WATCH", "VETO"])

if __name__ == '__main__':
    unittest.main()
