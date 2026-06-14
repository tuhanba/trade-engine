import unittest
from unittest.mock import MagicMock, patch
import sys
import os
import json

sys.path.insert(0, '.')

import config
import database
from telegram_manager import TelegramManager
from core.data_layer import TradeData

class TestTelegramInteractiveManager(unittest.TestCase):
    def setUp(self):
        # Enforce initial database configuration
        database.init_db()
        
        # Clear out overrides/configs to start fresh
        with database.get_conn() as conn:
            conn.execute("DELETE FROM system_state")
            conn.execute("DELETE FROM trades")

        # Mock send function
        self.sent_messages = []
        def mock_send(text, reply_markup=None):
            self.sent_messages.append({"text": text, "reply_markup": reply_markup})
            return True
            
        config.TELEGRAM_BOT_TOKEN = "mock_token"
        config.TELEGRAM_CHAT_ID = "123456"
        
        self.manager = TelegramManager(send_fn=mock_send)
        self.manager.chat_id = "123456"

    def test_help_command_includes_markup(self):
        self.manager._handle_update({
            "message": {
                "text": "/help",
                "chat": {"id": 123456}
            }
        })
        
        self.assertEqual(len(self.sent_messages), 1)
        last_msg = self.sent_messages[-1]
        self.assertIn("AurvexAI Yönetim Merkezi", last_msg["text"])
        self.assertIsNotNone(last_msg["reply_markup"])
        self.assertIn("inline_keyboard", last_msg["reply_markup"])
        
        # Check presence of Status and Open Positions buttons
        kbd = last_msg["reply_markup"]["inline_keyboard"]
        self.assertEqual(kbd[0][0]["text"], "📊 Durum")
        self.assertEqual(kbd[0][0]["callback_data"], "cmd:cat_status")

    def test_set_parameter_dynamic_override(self):
        # Initial config value
        self.assertEqual(config.TRADE_THRESHOLD, 55.0)

        # Set a new value through Telegram /set command
        self.manager._handle_update({
            "message": {
                "text": "/set trade_threshold 60.5",
                "chat": {"id": 123456}
            }
        })

        # Check DB state
        val = database.get_state("trade_threshold")
        self.assertEqual(val, "60.5")

        # Config should dynamically fetch the override from DB
        self.assertEqual(config.TRADE_THRESHOLD, 60.5)

    def test_set_parameter_invalid_or_unknown(self):
        self.manager._handle_update({
            "message": {
                "text": "/set invalid_param 100",
                "chat": {"id": 123456}
            }
        })
        self.assertIn("Bilinmeyen parametre", self.sent_messages[-1]["text"])

    @patch('telegram_manager.requests.post')
    def test_callback_query_toggles_pause_resume(self, mock_post):
        mock_post.return_value = MagicMock(status_code=200)

        # Initially active
        self.manager.is_paused = False
        
        # Callback query: pause
        self.manager._handle_update({
            "callback_query": {
                "id": "cb_1",
                "data": "cmd:pause",
                "message": {
                    "message_id": 111,
                    "chat": {"id": 123456}
                }
            }
        })

        self.assertTrue(self.manager.is_paused)
        self.assertTrue(database.get_state("tg_is_paused") == "True")

        # Callback query: resume
        self.manager._handle_update({
            "callback_query": {
                "id": "cb_2",
                "data": "cmd:resume",
                "message": {
                    "message_id": 111,
                    "chat": {"id": 123456}
                }
            }
        })

        self.assertFalse(self.manager.is_paused)
        self.assertTrue(database.get_state("tg_is_paused") == "False")

    @patch('telegram_manager.requests.post')
    @patch('execution_engine.ExecutionEngine.close_trade')
    def test_close_command_triggers_execution_exit(self, mock_close_trade, mock_post):
        mock_post.return_value = MagicMock(status_code=200)

        # Insert a mock open trade in DB
        trade = TradeData(
            symbol="BTCUSDT",
            side="LONG",
            entry_price=50000.0,
            quantity=1.0,
            status="OPEN"
        )
        trade_id = database.create_trade(trade)
        self.assertIsNotNone(trade_id)

        # Trigger manual close through Telegram
        self.manager._handle_update({
            "message": {
                "text": f"/close {trade_id}",
                "chat": {"id": 123456}
            }
        })

        # Ensure close_trade of ExecutionEngine was called
        mock_close_trade.assert_called_once()
        args, kwargs = mock_close_trade.call_args
        self.assertEqual(args[0]["id"], trade_id)
        self.assertEqual(kwargs.get("reason"), "manual")

if __name__ == "__main__":
    unittest.main()
