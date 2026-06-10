import unittest
from unittest.mock import MagicMock, patch
import pandas as pd
import sys
sys.path.insert(0, '.')

import config
from core.trigger_engine import TriggerEngine

@patch('core.trigger_engine.BAD_HOURS_UTC', [])
@patch('core.trigger_engine.GOOD_HOURS_UTC', list(range(24)))
@patch.object(TriggerEngine, '_rsi', return_value=55.0)
@patch('core.ml_signal_scorer.score_signal', return_value=80.0)
class TestScalpFilters(unittest.TestCase):
    def setUp(self):
        self.mock_client = MagicMock()
        self.engine = TriggerEngine(self.mock_client)
        self.engine.get_candles = MagicMock()

    def setup_mock_candles(self, direction):
        closes5 = []
        closes1 = []
        if direction == "LONG":
            for i in range(150):
                closes5.append(90.0 + (i / 150.0) * 10.0)
            for i in range(100):
                closes1.append(99.0 + (i / 100.0) * 1.0)
        else:
            for i in range(150):
                closes5.append(110.0 - (i / 150.0) * 10.0)
            for i in range(100):
                closes1.append(101.0 - (i / 100.0) * 1.0)
                
        df5 = pd.DataFrame({
            "open": closes5,
            "high": [c + 0.5 for c in closes5],
            "low": [c - 0.5 for c in closes5],
            "close": closes5,
            "volume": [1000.0] * 150,
        })
        df1 = pd.DataFrame({
            "open": closes1,
            "high": [c + 0.1 for c in closes1],
            "low": [c - 0.1 for c in closes1],
            "close": closes1,
            "volume": [200.0] * 100,
        })
        
        def get_candles_side_effect(symbol, interval, limit):
            if interval == "5m":
                return df5
            elif interval == "1m":
                return df1
            return pd.DataFrame()
        self.engine.get_candles.side_effect = get_candles_side_effect

    @patch('core.market_data.get_cached_ticker', return_value=None)
    @patch('database.get_recent_trades', return_value=[])
    def test_long_massive_ask_wall_rejection(self, mock_trades, mock_ticker, mock_score, mock_rsi):
        """Test that a LONG scalp signal is rejected when a massive passive ask wall is within 0.2% of entry."""
        self.setup_mock_candles("LONG")
        # Entry price is 100.0 (c1 = 100.0)
        # Limit price range: entry to entry * 1.002 = 100.0 to 100.2
        # Populate 50 levels of asks and bids to represent production depth
        asks = [["100.05", "0.1"]] * 50
        # Set level 3 to a massive sell wall within 0.2% (100.10 <= 100.20)
        asks[3] = ["100.10", "10.0"] # 1001.0 notional
        
        bids = [["99.90", "0.1"]] * 50
        
        self.mock_client.futures_order_book.return_value = {
            "bids": bids,
            "asks": asks,
        }
        self.mock_client.futures_ticker.return_value = {"quoteVolume": "10000000"}
        
        # Mock macro filter avg_rate check to not fail
        with patch('core.macro_filter.MacroFilter.get_24h_funding_trend', return_value={"avg_rate": 0.0, "bias": "NEUTRAL"}), \
             patch('core.macro_filter.MacroFilter.get_8h_funding_average', return_value={"avg_rate": 0.0, "bias": "NEUTRAL"}), \
             patch('core.cvd_engine.CVDEngine.analyze', return_value={"cvd_signal": "NEUTRAL", "cvd_score_bonus": 0.0}), \
             patch('core.oi_tracker.OITracker.analyze', return_value={"oi_signal": "NEUTRAL", "oi_score_bonus": 0.0}), \
             patch.object(config, 'HUMAN_MODE', False), \
             patch.object(config, 'MIN_ADX_5M', 0.0), \
             patch.object(config, 'ORDER_BOOK_WALL_FILTER_MODE', 'hard'), \
             patch.object(config, 'SCALP_OB_WALL_MULTIPLIER', 5.0), \
             patch.object(config, 'SCALP_OB_WALL_PCT', 0.002):
             
             res = self.engine.analyze("BTCUSDT", "LONG", btc_trend="NEUTRAL", trend_confluence=2)
             self.assertEqual(res["quality"], "D")
             self.assertEqual(res["reject_reason"], "passive_sell_wall_within_threshold")

    @patch('core.market_data.get_cached_ticker', return_value=None)
    @patch('database.get_recent_trades', return_value=[])
    def test_long_normal_ask_depth_passes(self, mock_trades, mock_ticker, mock_score, mock_rsi):
        """Test that a LONG scalp signal passes when there is no massive ask wall within 0.2%."""
        self.setup_mock_candles("LONG")
        asks = [["100.05", "0.1"]] * 50
        bids = [["99.90", "0.1"]] * 50
        
        self.mock_client.futures_order_book.return_value = {
            "bids": bids,
            "asks": asks,
        }
        self.mock_client.futures_ticker.return_value = {"quoteVolume": "10000000"}
        
        with patch('core.macro_filter.MacroFilter.get_24h_funding_trend', return_value={"avg_rate": 0.0, "bias": "NEUTRAL"}), \
             patch('core.macro_filter.MacroFilter.get_8h_funding_average', return_value={"avg_rate": 0.0, "bias": "NEUTRAL"}), \
             patch('core.cvd_engine.CVDEngine.analyze', return_value={"cvd_signal": "NEUTRAL", "cvd_score_bonus": 0.0}), \
             patch('core.oi_tracker.OITracker.analyze', return_value={"oi_signal": "NEUTRAL", "oi_score_bonus": 0.0}), \
             patch.object(config, 'HUMAN_MODE', False), \
             patch.object(config, 'MIN_ADX_5M', 0.0), \
             patch.object(config, 'SCALP_OB_WALL_MULTIPLIER', 5.0), \
             patch.object(config, 'SCALP_OB_WALL_PCT', 0.002):
             
             res = self.engine.analyze("BTCUSDT", "LONG", btc_trend="NEUTRAL", trend_confluence=2)
             self.assertNotEqual(res["quality"], "D")
             self.assertNotIn("reject_reason", res)

    @patch('core.market_data.get_cached_ticker', return_value=None)
    @patch('database.get_recent_trades', return_value=[])
    def test_short_massive_bid_wall_rejection(self, mock_trades, mock_ticker, mock_score, mock_rsi):
        """Test that a SHORT scalp signal is rejected when a massive passive bid wall is within 0.2% of entry."""
        self.setup_mock_candles("SHORT")
        # Entry price is 100.0 (c1 = 100.0)
        # Limit price range: entry * 0.998 to entry = 99.8 to 100.0
        # Populate 50 levels of asks and bids
        bids = [["99.95", "0.1"]] * 50
        # Set level 3 to a massive buy wall (99.90 >= 99.80)
        bids[3] = ["99.90", "10.0"] # 999.0 notional
        
        asks = [["100.05", "0.1"]] * 50
        
        self.mock_client.futures_order_book.return_value = {
            "bids": bids,
            "asks": asks,
        }
        self.mock_client.futures_ticker.return_value = {"quoteVolume": "10000000"}
        
        with patch('core.macro_filter.MacroFilter.get_24h_funding_trend', return_value={"avg_rate": 0.0, "bias": "NEUTRAL"}), \
             patch('core.macro_filter.MacroFilter.get_8h_funding_average', return_value={"avg_rate": 0.0, "bias": "NEUTRAL"}), \
             patch('core.cvd_engine.CVDEngine.analyze', return_value={"cvd_signal": "NEUTRAL", "cvd_score_bonus": 0.0}), \
             patch('core.oi_tracker.OITracker.analyze', return_value={"oi_signal": "NEUTRAL", "oi_score_bonus": 0.0}), \
             patch.object(config, 'HUMAN_MODE', False), \
             patch.object(config, 'MIN_ADX_5M', 0.0), \
             patch.object(config, 'ORDER_BOOK_WALL_FILTER_MODE', 'hard'), \
             patch.object(config, 'SCALP_OB_WALL_MULTIPLIER', 5.0), \
             patch.object(config, 'SCALP_OB_WALL_PCT', 0.002):
             
             res = self.engine.analyze("BTCUSDT", "SHORT", btc_trend="NEUTRAL", trend_confluence=2)
             self.assertEqual(res["quality"], "D")
             self.assertEqual(res["reject_reason"], "passive_buy_wall_within_threshold")

    @patch('core.market_data.get_cached_ticker', return_value=None)
    @patch('database.get_recent_trades', return_value=[])
    def test_long_bearish_cvd_divergence_rejection(self, mock_trades, mock_ticker, mock_score, mock_rsi):
        """Test that a LONG scalp signal is rejected when CVDEngine shows BEARISH divergence."""
        self.setup_mock_candles("LONG")
        self.mock_client.futures_order_book.return_value = {"bids": [["99.0", "0.1"]] * 50, "asks": [["101.0", "0.1"]] * 50}
        self.mock_client.futures_ticker.return_value = {"quoteVolume": "10000000"}
        
        with patch('core.macro_filter.MacroFilter.get_24h_funding_trend', return_value={"avg_rate": 0.0, "bias": "NEUTRAL"}), \
             patch('core.macro_filter.MacroFilter.get_8h_funding_average', return_value={"avg_rate": 0.0, "bias": "NEUTRAL"}), \
             patch('core.cvd_engine.CVDEngine.analyze', return_value={"cvd_signal": "BEARISH", "cvd_score_bonus": -1.0}), \
             patch('core.oi_tracker.OITracker.analyze', return_value={"oi_signal": "NEUTRAL", "oi_score_bonus": 0.0}), \
             patch.object(config, 'HUMAN_MODE', False), \
             patch.object(config, 'MIN_ADX_5M', 0.0), \
             patch.object(config, 'SCALP_CVD_DIVERGENCE_FILTER_ENABLED', True):
             
             res = self.engine.analyze("BTCUSDT", "LONG", btc_trend="NEUTRAL", trend_confluence=2)
             self.assertEqual(res["quality"], "D")
             self.assertEqual(res["reject_reason"], "bearish_cvd_divergence")

    @patch('core.market_data.get_cached_ticker', return_value=None)
    @patch('database.get_recent_trades', return_value=[])
    def test_short_bullish_cvd_divergence_rejection(self, mock_trades, mock_ticker, mock_score, mock_rsi):
        """Test that a SHORT scalp signal is rejected when CVDEngine shows BULLISH divergence."""
        self.setup_mock_candles("SHORT")
        self.mock_client.futures_order_book.return_value = {"bids": [["99.0", "0.1"]] * 50, "asks": [["101.0", "0.1"]] * 50}
        self.mock_client.futures_ticker.return_value = {"quoteVolume": "10000000"}
        
        with patch('core.macro_filter.MacroFilter.get_24h_funding_trend', return_value={"avg_rate": 0.0, "bias": "NEUTRAL"}), \
             patch('core.macro_filter.MacroFilter.get_8h_funding_average', return_value={"avg_rate": 0.0, "bias": "NEUTRAL"}), \
             patch('core.cvd_engine.CVDEngine.analyze', return_value={"cvd_signal": "BULLISH", "cvd_score_bonus": -1.0}), \
             patch('core.oi_tracker.OITracker.analyze', return_value={"oi_signal": "NEUTRAL", "oi_score_bonus": 0.0}), \
             patch.object(config, 'HUMAN_MODE', False), \
             patch.object(config, 'MIN_ADX_5M', 0.0), \
             patch.object(config, 'SCALP_CVD_DIVERGENCE_FILTER_ENABLED', True):
             
             res = self.engine.analyze("BTCUSDT", "SHORT", btc_trend="NEUTRAL", trend_confluence=2)
             self.assertEqual(res["quality"], "D")
             self.assertEqual(res["reject_reason"], "bullish_cvd_divergence")

    @patch('core.market_data.get_cached_ticker', return_value=None)
    @patch('database.get_recent_trades', return_value=[])
    def test_human_mode_bypasses_filters(self, mock_trades, mock_ticker, mock_score, mock_rsi):
        """Test that in human mode (HUMAN_MODE=True), orderbook walls and CVD divergence vetoes are bypassed."""
        self.setup_mock_candles("LONG")
        asks = [["100.05", "0.1"]] * 50
        asks[3] = ["100.10", "10.0"] # massive wall
        bids = [["99.90", "0.1"]] * 50
        
        self.mock_client.futures_order_book.return_value = {
            "bids": bids,
            "asks": asks,
        }
        self.mock_client.futures_ticker.return_value = {"quoteVolume": "10000000"}
        
        with patch('core.macro_filter.MacroFilter.get_24h_funding_trend', return_value={"avg_rate": 0.0, "bias": "NEUTRAL"}), \
             patch('core.macro_filter.MacroFilter.get_8h_funding_average', return_value={"avg_rate": 0.0, "bias": "NEUTRAL"}), \
             patch('core.cvd_engine.CVDEngine.analyze', return_value={"cvd_signal": "BEARISH", "cvd_score_bonus": -1.0}), \
             patch('core.oi_tracker.OITracker.analyze', return_value={"oi_signal": "NEUTRAL", "oi_score_bonus": 0.0}), \
             patch.object(config, 'HUMAN_MODE', True), \
             patch.object(config, 'MIN_ADX_5M', 0.0), \
             patch.object(config, 'SCALP_OB_WALL_MULTIPLIER', 5.0), \
             patch.object(config, 'SCALP_OB_WALL_PCT', 0.002):
             
             res = self.engine.analyze("BTCUSDT", "LONG", btc_trend="NEUTRAL", trend_confluence=2)
             reject_reason = res.get("reject_reason", "")
             self.assertNotEqual(reject_reason, "passive_sell_wall_within_threshold")
             self.assertNotEqual(reject_reason, "bearish_cvd_divergence")

if __name__ == '__main__':
    unittest.main()
