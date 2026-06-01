import time
import sys
import unittest
from unittest.mock import MagicMock, patch

sys.path.insert(0, '.')

import config
from core.market_data import set_cached_price, set_cached_ticker, get_current_price
from execution_engine import _get_price
from core.live_execution import LiveExecutionEngine
from core.data_layer import SignalData

class TestSpeedAndSlippageOpt(unittest.TestCase):
    def setUp(self):
        # Setup config defaults
        config.BINANCE_API_KEY = "mock_key"
        config.BINANCE_API_SECRET = "mock_secret"
        config.EXECUTION_MODE = "live"
        config.DRY_RUN = False
        config.MAX_SPREAD_PCT = 0.15

    def test_price_lookup_speed(self):
        print("\n--- Running Price Lookup Speed Test ---")
        
        # Seed cache
        set_cached_price("BTCUSDT", 60000.0)
        
        # Benchmark cached lookup
        t0 = time.perf_counter()
        price_cached = _get_price(None, "BTCUSDT")
        t1 = time.perf_counter()
        
        cached_duration_ms = (t1 - t0) * 1000
        print(f"Cached price lookup returned: {price_cached} in {cached_duration_ms:.4f} ms")
        self.assertEqual(price_cached, 60000.0)
        self.assertLess(cached_duration_ms, 1.0, "Cached lookup took longer than 1ms!")

    @patch('core.live_execution.Client')
    def test_slippage_guard_tight_spread(self, MockClient):
        print("\n--- Running Slippage Guard Tight Spread Test ---")
        
        # Mock Client responses
        mock_client_inst = MagicMock()
        MockClient.return_value = mock_client_inst
        mock_client_inst.futures_account.return_value = {"totalWalletBalance": "1000.0"}
        mock_client_inst.futures_exchange_info.return_value = {
            'symbols': [{
                'symbol': 'BTCUSDT',
                'pricePrecision': 2,
                'quantityPrecision': 3,
                'filters': [
                    {'filterType': 'PRICE_FILTER', 'tickSize': '0.01'},
                    {'filterType': 'LOT_SIZE', 'stepSize': '0.001'}
                ]
            }]
        }
        
        # Seed cache with tight spread (0.05%)
        set_cached_ticker("BTCUSDT", {
            's': 'BTCUSDT',
            'last': 60000.0,
            'bid': 59985.0,
            'ask': 60015.0  # Spread = 30 / 59985 = 0.05%
        })
        
        # Instantiate engine (reloads info and balance)
        engine = LiveExecutionEngine()
        
        # Check Slippage Guard
        sig = SignalData()
        sig.symbol = "BTCUSDT"
        sig.direction = "LONG"
        sig.side = "LONG"
        sig.entry_price = 60000.0
        sig.stop_loss = 59000.0
        sig.tp1 = 61000.0
        sig.max_loss = 10.0
        sig.risk_pct = 1.0
        
        # Mock order placement to avoid real API errors after passing the guard
        mock_client_inst.futures_create_order.return_value = {"orderId": 12345, "avgPrice": 60000.0}
        
        # Set config to allow live execution
        with patch('config.is_live_trading_allowed', return_value=True):
            trade_id = engine.open_live_trade(sig)
            
        print(f"Tight spread trade placement returned: {trade_id}")
        self.assertIsNotNone(trade_id, "Order was incorrectly rejected by Slippage Guard under tight spread!")

    @patch('core.live_execution.Client')
    def test_slippage_guard_wide_spread(self, MockClient):
        print("\n--- Running Slippage Guard Wide Spread Test ---")
        
        mock_client_inst = MagicMock()
        MockClient.return_value = mock_client_inst
        mock_client_inst.futures_account.return_value = {"totalWalletBalance": "1000.0"}
        
        # Seed cache with wide spread (0.50% > 0.15%)
        set_cached_ticker("BTCUSDT", {
            's': 'BTCUSDT',
            'last': 60000.0,
            'bid': 59800.0,
            'ask': 60100.0  # Spread = 300 / 59800 = 0.50%
        })
        
        engine = LiveExecutionEngine()
        
        sig = SignalData()
        sig.symbol = "BTCUSDT"
        sig.direction = "LONG"
        sig.side = "LONG"
        sig.entry_price = 60000.0
        sig.stop_loss = 59000.0
        sig.tp1 = 61000.0
        
        with patch('config.is_live_trading_allowed', return_value=True):
            trade_id = engine.open_live_trade(sig)
            
        print(f"Wide spread trade placement returned: {trade_id} (Expected: None)")
        self.assertIsNone(trade_id, "Order should have been rejected by Slippage Guard under wide spread!")

if __name__ == "__main__":
    unittest.main()
