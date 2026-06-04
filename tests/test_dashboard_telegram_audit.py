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
from core.spectra_ceo import SYSTEM_PROMPT


class TestDashboardTelegramAudit(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        database.init_db()

    def setUp(self):
        # Clear system state and trades tables for clean test context
        with database.get_conn() as conn:
            conn.execute("DELETE FROM trades")
            conn.execute("DELETE FROM system_state")

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

    def test_spectra_ceo_quant_prompt_upgrade(self):
        """Verify Spektra CEO prompt contains professional quantitative metrics."""
        self.assertIn("GMM (Gaussian Mixture Model)", SYSTEM_PROMPT)
        self.assertIn("Pearson korelasyon matrisi", SYSTEM_PROMPT)
        self.assertIn("CVD (Cumulative Volume Delta)", SYSTEM_PROMPT)
        self.assertIn("L2 Wall", SYSTEM_PROMPT)
        self.assertIn("markdown tabloları", SYSTEM_PROMPT)

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


if __name__ == "__main__":
    unittest.main()
