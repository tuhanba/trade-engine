import unittest
import os
import sys
import sqlite3
import asyncio
from unittest.mock import MagicMock, AsyncMock, patch

sys.path.insert(0, '.')

import config
import database
from database import init_db, update_system_state, get_system_state
from core.ai_decision_engine import SentimentAgent, classify_signal
from core.data_layer import SignalData
from core.hyperparameter_tuner import optimize_parameters
from core.services.sentiment_scraper import sentiment_scraper
from websocket_events import event_manager

class TestNewFeatures(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        init_db()

    def setUp(self):
        # Clean trades and suggestions to have a fresh state
        with database.get_conn() as conn:
            conn.execute("DELETE FROM trades")
            conn.execute("DELETE FROM params WHERE id = 1")
            conn.execute("INSERT OR IGNORE INTO params (id) VALUES (1)")
            conn.execute("DELETE FROM system_state")
        
        # Reset mocks on event manager
        event_manager.broadcast_trailing_stop_updated = MagicMock()
        event_manager.broadcast_limit_chase_progress = MagicMock()
        event_manager.broadcast_agent_votes = MagicMock()
        event_manager._publish_event = MagicMock()

    def test_trailing_stop_broadcast(self):
        # Setup a mock trade in database
        trade_id = 1
        symbol = "BTCUSDT"
        with database.get_conn() as conn:
            conn.execute("""
                INSERT INTO trades (id, symbol, direction, status, entry, sl, qty, realized_pnl, net_pnl, metadata)
                VALUES (?, ?, 'LONG', 'OPEN', 50000.0, 49000.0, 1.0, 0.0, 0.0, '{}')
            """, (trade_id, symbol))
        
        from execution_engine import ExecutionEngine
        engine = ExecutionEngine()
        
        # We simulate trailing stop evaluation changing stop loss
        # We mock database.update_trade_sl to see if broadcast is triggered
        with patch('database.update_trade_sl') as mock_update_sl, \
             patch('execution_engine.get_current_price', return_value=51000.0), \
             patch('execution_engine.calculate_unrealized_pnl', return_value=1000.0):
            # Create a mock result
            mock_result = MagicMock()
            mock_result.should_full_close = False
            mock_result.should_partial_close = False
            mock_result.new_sl = 49500.0
            
            # Mock trailing evaluate
            engine.trailing.evaluate = MagicMock(return_value=mock_result)
            
            # Evaluate trade
            trade = {"id": trade_id, "symbol": symbol, "direction": "LONG", "sl": 49000.0, "entry": 50000.0, "qty": 1.0, "status": "OPEN"}
            engine._process_single_trade(trade)
            
            # Assert trailing stop updated is called
            event_manager.broadcast_trailing_stop_updated.assert_called_once()
            args = event_manager.broadcast_trailing_stop_updated.call_args[1]
            self.assertEqual(args['symbol'], symbol)
            self.assertEqual(args['trade_id'], trade_id)
            self.assertEqual(args['old_sl'], 49000.0)
            self.assertEqual(args['new_sl'], 49500.0)

    def test_optuna_tuner(self):
        # Insert 6 mock closed trades to satisfy the minimum count of 5
        with database.get_conn() as conn:
            for i in range(6):
                # 3 Wins (MFE hit), 3 Losses (MAE hit)
                direction = "LONG"
                entry = 100.0
                sl = 98.0
                net_pnl = 10.0 if i % 2 == 0 else -10.0
                mae = 0.005 if i % 2 == 0 else 0.03   # 0.5% vs 3% (sl distance is 2%)
                mfe = 0.05 if i % 2 == 0 else 0.002   # 5% vs 0.2%
                final_score = 65.0
                conn.execute("""
                    INSERT INTO trades (symbol, direction, status, entry, sl, realized_pnl, net_pnl, qty, final_score, mfe, mae)
                    VALUES ('ETHUSDT', ?, 'closed', ?, ?, ?, ?, 1.0, ?, ?, ?)
                """, (direction, entry, sl, net_pnl, net_pnl, final_score, mfe, mae))
        
        # Run tuner optimization
        optimize_parameters()
        
        # Verify that best parameters are updated in database params table
        with database.get_conn() as conn:
            row = conn.execute("SELECT sl_atr_mult, tp_atr_mult FROM params WHERE id = 1").fetchone()
            self.assertIsNotNone(row)
            self.assertGreater(row[0], 0.0)
            self.assertGreater(row[1], 0.0)
            
            # Verify system_state for trade_threshold
            threshold_val = get_system_state("trade_threshold")
            self.assertNotEqual(threshold_val, "-")
            self.assertGreater(float(threshold_val), 0.0)

    def test_sentiment_scraper_bullish(self):
        mock_xml = """<rss version="2.0">
            <channel>
                <item>
                    <title>Bitcoin Price Rally Surges Above Resistance As Institutional Adoption Grows</title>
                    <pubDate>Mon, 01 Jun 2026 00:00:00 GMT</pubDate>
                </item>
                <item>
                    <title>Ethereum Recovery Gains Momentum Following Network Upgrade</title>
                    <pubDate>Mon, 01 Jun 2026 01:00:00 GMT</pubDate>
                </item>
            </channel>
        </rss>"""

        # Mock aiohttp response context manager
        mock_resp = MagicMock()
        mock_resp.status = 200
        mock_resp.text = AsyncMock(return_value=mock_xml)

        mock_context = MagicMock()
        mock_context.__aenter__ = AsyncMock(return_value=mock_resp)
        mock_context.__aexit__ = AsyncMock(return_value=None)
        
        mock_session = MagicMock()
        mock_session.get = MagicMock(return_value=mock_context)
        
        mock_session_context = MagicMock()
        mock_session_context.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session_context.__aexit__ = AsyncMock(return_value=None)
        
        async def run_scraper():
            with patch('aiohttp.ClientSession', return_value=mock_session_context):
                return await sentiment_scraper.scrape_sentiment()
        
        sentiment = asyncio.run(run_scraper())
        self.assertEqual(sentiment, "bullish")
        
        # Verify saved state in DB
        db_sent = get_system_state("sentiment_scraper_macro")
        self.assertEqual(db_sent, "bullish")

    def test_sentiment_agent_influence(self):
        # Set system state macro sentiment to bearish
        update_system_state("sentiment_scraper_macro", "bearish")
        
        # Set signal data
        sig_dict = {
            'symbol': 'BTCUSDT',
            'side': 'LONG',
            'entry_price': 50000.0,
            'stop_loss': 49000.0,
            'tp1': 51500.0,
            'score': 70.0,
            'setup_quality': 'A',
            'trigger_score': 7.0,
            'trend_score': 7.0,
            'risk_score': 7.0,
            'risk_percent': 1.0,
            'confidence': 0.5,
        }
        sig = SignalData.from_dict(sig_dict)
        sig.score = 70.0
        
        # Evaluate using SentimentAgent directly
        agent = SentimentAgent()
        # Mock macro_service FNG fallback to neutral
        with patch('core.services.macro_service.macro_service.get_market_sentiment', return_value={"fng_value": 50}):
            vote, score, reason = agent.evaluate(sig, {})
            # Bearish macro sentiment should apply -10 reduction on LONG
            # FNG is 50 -> neutral (no reduction)
            # Base score starts at 70
            # Since macro_sentiment is bearish and side is LONG: score decreases to 60 (70 - 10)
            self.assertEqual(score, 60.0)
            self.assertIn("Bearish macro sentiment restricts LONG", reason)

if __name__ == '__main__':
    unittest.main()
