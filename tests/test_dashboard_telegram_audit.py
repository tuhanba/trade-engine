"""
tests/test_dashboard_telegram_audit.py - Unit tests for System Audit, Dashboard Sync and Telegram updates.
"""

import os
import sys
import json
import unittest
from unittest.mock import MagicMock, patch

sys.path.insert(0, '.')

import config
import database
from app import app
from telegram_manager import TelegramManager
from core.friday_ceo import SYSTEM_PROMPT


class TestDashboardTelegramAudit(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        database.init_db()

    def setUp(self):
        # Clear system state and trades tables for clean test context
        with database.get_conn() as conn:
            conn.execute("DELETE FROM trades")
            conn.execute("DELETE FROM system_state")
        # Clear stats cache to avoid cross-test caching interference
        database._stats_cache.clear()

    def test_environment_stats_filtering(self):
        """Verify database functions filter trade stats correctly by environment."""
        # Insert 1 paper trade (win)
        with database.get_conn() as conn:
            conn.execute("""
                INSERT INTO trades (id, symbol, direction, status, entry, sl, net_pnl, is_valid_for_stats, environment, open_time, close_time)
                VALUES (301, 'BTCUSDT', 'LONG', 'closed', 50000.0, 49000.0, 500.0, 1, 'paper', '2026-06-01 00:00:00', '2026-06-01 01:00:00')
            """)
            # Insert 1 live trade (loss)
            conn.execute("""
                INSERT INTO trades (id, symbol, direction, status, entry, sl, net_pnl, is_valid_for_stats, environment, open_time, close_time)
                VALUES (302, 'ETHUSDT', 'SHORT', 'closed', 3000.0, 3100.0, -100.0, 1, 'live', '2026-06-01 00:00:00', '2026-06-01 01:00:00')
            """)
            
        # Get dashboard stats for paper environment
        paper_stats = database.get_dashboard_stats('paper')
        self.assertEqual(paper_stats['total_trades'], 1)
        self.assertEqual(paper_stats['closed_trades'], 1)
        self.assertEqual(paper_stats['win_trades'], 1)
        self.assertEqual(paper_stats['loss_trades'], 0)
        self.assertEqual(paper_stats['win_rate'], 100.0)
        self.assertEqual(paper_stats['realized_pnl'], 500.0)

        # Get dashboard stats for live environment
        live_stats = database.get_dashboard_stats('live')
        self.assertEqual(live_stats['total_trades'], 1)
        self.assertEqual(live_stats['closed_trades'], 1)
        self.assertEqual(live_stats['win_trades'], 0)
        self.assertEqual(live_stats['loss_trades'], 1)
        self.assertEqual(live_stats['win_rate'], 0.0)
        self.assertEqual(live_stats['realized_pnl'], -100.0)

    def test_ip_whitelisting_denied_json(self):
        """Verify Flask Whitelisting returns a standard JSON error response instead of HTML abort when blocked."""
        # Set whitelisted IP env variable to a specific IP so client from other IPs gets blocked
        with patch.dict(os.environ, {"ALLOWED_IPS": "192.168.1.50"}):
            # We must reload/reinitialize the allowed IPs set inside app.py
            # For testing, we can directly overwrite app._ALLOWED_IPS
            from app import _ALLOWED_IPS
            # Save original
            orig_allowed = set(_ALLOWED_IPS)
            _ALLOWED_IPS.clear()
            _ALLOWED_IPS.add("192.168.1.50")
            
            try:
                client = app.test_client()
                # Make request to /api/stats (should be blocked)
                # By default, Flask test client uses remote_addr = '127.0.0.1'
                res = client.get("/api/stats")
                self.assertEqual(res.status_code, 403)
                self.assertEqual(res.headers["Content-Type"], "application/json")
                
                # Check response body
                data = json.loads(res.data.decode('utf-8'))
                self.assertFalse(data["ok"])
                self.assertIn("IP Access Denied", data["error"])
            finally:
                # Restore original ALLOWED_IPS
                _ALLOWED_IPS.clear()
                _ALLOWED_IPS.update(orig_allowed)

    def test_friday_ceo_quant_prompt_upgrade(self):
        """Verify Friday CEO prompt contains professional quantitative metrics."""
        self.assertIn("Gaussian Mixture Model (GMM)", SYSTEM_PROMPT)
        self.assertIn("Pearson korelasyon matrisi", SYSTEM_PROMPT)
        self.assertIn("Cumulative Volume Delta (CVD)", SYSTEM_PROMPT)
        self.assertIn("L2 Wall", SYSTEM_PROMPT)
        self.assertIn("markdown tablosu", SYSTEM_PROMPT)

    def test_diagnose_command_telegram_output(self):
        """Verify the new /diagnose & /teshis Telegram commands return system diagnosis logs."""
        sent_messages = []
        def mock_send(text, reply_markup=None):
            sent_messages.append(text)
            return True
            
        tg_manager = TelegramManager(send_fn=mock_send)
        tg_manager.chat_id = "987654"
        
        # Trigger diagnose command
        tg_manager._handle_update({
            "message": {
                "text": "/diagnose",
                "chat": {"id": 987654}
            }
        })
        
        self.assertEqual(len(sent_messages), 1)
        report = sent_messages[0]
        self.assertIn("Sistem Teşhis Raporu", report)
        self.assertIn("DB Dosya Konumu", report)
        self.assertIn("DB Boyutu", report)
        self.assertIn("IP Whitelist Durumu", report)
        self.assertIn("RAM Durumu", report)
        self.assertIn("Disk Durumu", report)
        
        # Trigger teshis command
        sent_messages.clear()
        tg_manager._handle_update({
            "message": {
                "text": "/teshis",
                "chat": {"id": 987654}
            }
        })
        self.assertEqual(len(sent_messages), 1)
        self.assertIn("Sistem Teşhis Raporu", sent_messages[0])

    def test_dashboard_pin_unauthorized(self):
        """Verify API requests return 401 when DASHBOARD_PIN is configured and no/invalid PIN is provided."""
        orig_pin = getattr(config, "DASHBOARD_PIN", "")
        config.DASHBOARD_PIN = "9999"
        try:
            client = app.test_client()
            res = client.get("/api/stats")
            self.assertEqual(res.status_code, 401)
            
            res = client.get("/api/stats", headers={"X-Dashboard-PIN": "0000"})
            self.assertEqual(res.status_code, 401)
        finally:
            config.DASHBOARD_PIN = orig_pin

    def test_dashboard_pin_authorized(self):
        """Verify API requests return 200/ok when the correct DASHBOARD_PIN is provided."""
        orig_pin = getattr(config, "DASHBOARD_PIN", "")
        config.DASHBOARD_PIN = "9999"
        try:
            client = app.test_client()
            res = client.get("/api/stats", headers={"X-Dashboard-PIN": "9999"})
            self.assertEqual(res.status_code, 200)
            
            res = client.get("/api/stats?pin=9999")
            self.assertEqual(res.status_code, 200)
        finally:
            config.DASHBOARD_PIN = orig_pin

    def test_friday_menu_telegram_command(self):
        """Verify that trigger /friday command without arguments returns a menu with interactive buttons."""
        sent_messages = []
        reply_markups = []
        def mock_send(text, reply_markup=None):
            sent_messages.append(text)
            reply_markups.append(reply_markup)
            return True
            
        tg_manager = TelegramManager(send_fn=mock_send, friday_ceo=MagicMock())
        tg_manager.chat_id = "987654"
        
        tg_manager._handle_update({
            "message": {
                "text": "/friday",
                "chat": {"id": 987654}
            }
        })
        
        self.assertEqual(len(sent_messages), 1)
        self.assertIn("Friday AI CEO Yönetim Paneli", sent_messages[0])
        self.assertIsNotNone(reply_markups[0])
        self.assertIn("inline_keyboard", reply_markups[0])
        
        buttons = reply_markups[0]["inline_keyboard"]
        flat_buttons = [btn for row in buttons for btn in row]
        self.assertTrue(any(btn["text"] == "🏥 Sistem Teşhisi" for btn in flat_buttons))
        self.assertTrue(any(btn["text"] == "📈 Bakiye Grafiği" for btn in flat_buttons))

    def test_telegram_callback_query_robustness(self):
        """Verify callback query processing with None messages and multiple chat IDs."""
        sent_messages = []
        def mock_send(text, reply_markup=None):
            sent_messages.append(text)
            return True
            
        tg_manager = TelegramManager(send_fn=mock_send)
        tg_manager.chat_id = "987654,112233"  # Multiple comma-separated IDs
        
        # Mock API calls inside TelegramManager
        tg_manager._answer_callback_query = MagicMock()
        tg_manager._edit_message_text = MagicMock()
        
        # Case 1: Callback query with None message (should not raise AttributeError)
        cb_query_none_msg = {
            "id": "cb_123",
            "data": "cmd:refresh_settings",
            "message": None,
            "from": {"id": 987654}  # Authorized user ID
        }
        
        # Call handler (should run and answer the query)
        tg_manager._handle_callback_query(cb_query_none_msg)
        
        tg_manager._answer_callback_query.assert_any_call("cb_123", "İşlem alınıyor...")
        
        # Case 2: Clicked in group chat (unauthorized chat, but authorized user)
        cb_query_group_chat = {
            "id": "cb_456",
            "data": "cmd:refresh_settings",
            "message": {
                "message_id": 555,
                "chat": {"id": -1002345}  # Group chat ID
            },
            "from": {"id": 112233}  # Authorized user ID
        }
        
        tg_manager._handle_callback_query(cb_query_group_chat)
        tg_manager._answer_callback_query.assert_any_call("cb_456", "İşlem alınıyor...")

    def test_telegram_force_and_ignore_commands(self):
        """Verify /force and /ignore text commands successfully open or veto candidate signals."""
        sent_messages = []
        def mock_send(text, reply_markup=None):
            sent_messages.append(text)
            return True

        tg_manager = TelegramManager(send_fn=mock_send)
        tg_manager.chat_id = "987654"

        # Insert a dummy candidate signal to database
        import database
        cand_id = database.save_candidate_signal({
            "signal_id": "test_sig_uuid_123",
            "symbol": "BTCUSDT",
            "direction": "LONG",
            "entry": 50000.0,
            "sl": 49000.0,
            "tp1": 52000.0,
            "quality": "A",
            "score": 75.0
        })

        # 1. Test /ignore command
        tg_manager._handle_update({
            "message": {
                "text": f"/ignore {cand_id}",
                "chat": {"id": 987654}
            }
        })
        self.assertTrue(any("manüel olarak iptal edildi" in msg for msg in sent_messages))
        
        # Verify candidate decision in DB is VETOED
        cand = database.get_candidate_by_id(cand_id)
        self.assertEqual(cand["decision"], "VETOED")

        # Create a new candidate for testing /force command
        cand_id_2 = database.save_candidate_signal({
            "signal_id": "test_sig_uuid_456",
            "symbol": "ETHUSDT",
            "direction": "SHORT",
            "entry": 3000.0,
            "sl": 3100.0,
            "tp1": 2900.0,
            "quality": "A",
            "score": 80.0
        })

        sent_messages.clear()
        # 2. Test /force command (runs process_signal in paper mode)
        with patch("execution_engine.ExecutionEngine.process_signal", return_value=999):
            tg_manager._handle_update({
                "message": {
                    "text": f"/force {cand_id_2}",
                    "chat": {"id": 987654}
                }
            })
        
        # Verify it sent the confirmation message
        self.assertTrue(any("zorla trade açılıyor" in msg or "başarıyla açıldı" in msg for msg in sent_messages))
        
        # Verify candidate decision in DB is EXECUTED
        cand2 = database.get_candidate_by_id(cand_id_2)
        self.assertEqual(cand2["decision"], "EXECUTED")


if __name__ == "__main__":
    unittest.main()
