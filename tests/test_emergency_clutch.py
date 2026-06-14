import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest
import sqlite3
import config
from datetime import datetime, timezone, timedelta
from core.risk_engine import RiskEngine
from unittest.mock import MagicMock, patch

class MockBinanceClient:
    def futures_klines(self, symbol, interval, limit):
        rows = []
        price = 100.0
        for i in range(limit):
            o = price + i * 0.1
            h = o + 0.5
            l = o - 0.5
            c = o + 0.2
            rows.append([0, str(o), str(h), str(l), str(c), "1000", 0, "0", 0, "0", "0", "0"])
        return rows

@pytest.fixture
def test_db_setup(tmp_path, monkeypatch):
    """Sets up a temporary SQLite database for testing the emergency clutch."""
    db_path = tmp_path / "test_trading.db"
    monkeypatch.setattr(config, "DB_PATH", str(db_path))
    
    import database
    database.init_db()
    
    # Insert initial required test rows
    with database.get_conn() as conn:
        conn.execute("INSERT OR REPLACE INTO params (id, risk_pct) VALUES (1, 1.0)")
        conn.execute("INSERT INTO balance_ledger (balance_after) VALUES (2000.0)")
        conn.commit()
        
    return str(db_path)

def test_clutch_2_hour_lookback(test_db_setup, monkeypatch):
    """Verifies that trades closed older than 2 hours are not checked by the clutch."""
    conn = sqlite3.connect(test_db_setup)
    
    # Insert 3 closed trades from 3 hours ago with high slippage/latency
    three_hours_ago = (datetime.now(timezone.utc) - timedelta(hours=3)).isoformat()
    conn.execute(
        "INSERT INTO trades (symbol, direction, status, slippage, latency_ms, close_time) VALUES (?, ?, ?, ?, ?, ?)",
        ("BTCUSDT", "LONG", "closed", 0.50, 1500, three_hours_ago)
    )
    conn.execute(
        "INSERT INTO trades (symbol, direction, status, slippage, latency_ms, close_time) VALUES (?, ?, ?, ?, ?, ?)",
        ("ETHUSDT", "LONG", "closed", 0.60, 2000, three_hours_ago)
    )
    conn.execute(
        "INSERT INTO trades (symbol, direction, status, slippage, latency_ms, close_time) VALUES (?, ?, ?, ?, ?, ?)",
        ("SOLUSDT", "LONG", "closed", 0.40, 1800, three_hours_ago)
    )
    conn.commit()
    conn.close()
    
    # Force execution mode to live
    monkeypatch.setattr(config, "EXECUTION_MODE", "live")
    monkeypatch.setattr(config, "BYPASS_LIVE_RISK_SHIELDS", False)
    
    engine = RiskEngine(MockBinanceClient(), db_path=test_db_setup)
    
    with patch("database.get_open_trades", return_value=[]):
        res = engine.calculate("BTCUSDT", "LONG", 100.0, "A+", 2000.0)
        # Should be valid because the bad trades are older than 2 hours
        assert res["valid"] is True

def test_clutch_triggers_for_recent_trades(test_db_setup, monkeypatch):
    """Verifies that recent trades (within 2 hours) with high slippage/latency trigger the clutch."""
    conn = sqlite3.connect(test_db_setup)
    
    # Insert 3 closed trades from 10 minutes ago with high slippage/latency
    ten_mins_ago = (datetime.now(timezone.utc) - timedelta(minutes=10)).isoformat()
    conn.execute(
        "INSERT INTO trades (symbol, direction, status, slippage, latency_ms, close_time) VALUES (?, ?, ?, ?, ?, ?)",
        ("BTCUSDT", "LONG", "closed", 0.50, 1500, ten_mins_ago)
    )
    conn.execute(
        "INSERT INTO trades (symbol, direction, status, slippage, latency_ms, close_time) VALUES (?, ?, ?, ?, ?, ?)",
        ("ETHUSDT", "LONG", "closed", 0.60, 2000, ten_mins_ago)
    )
    conn.execute(
        "INSERT INTO trades (symbol, direction, status, slippage, latency_ms, close_time) VALUES (?, ?, ?, ?, ?, ?)",
        ("SOLUSDT", "LONG", "closed", 0.40, 1800, ten_mins_ago)
    )
    conn.commit()
    conn.close()
    
    # Force execution mode to live and ensure shields are NOT bypassed
    monkeypatch.setattr(config, "EXECUTION_MODE", "live")
    monkeypatch.setattr(config, "BYPASS_LIVE_RISK_SHIELDS", False)
    
    engine = RiskEngine(MockBinanceClient(), db_path=test_db_setup)
    
    with patch("database.get_open_trades", return_value=[]):
        res = engine.calculate("BTCUSDT", "LONG", 100.0, "A+", 2000.0)
        # Should be blocked and trigger clutch veto
        assert res["valid"] is False
        assert res["risk_reject_reason"] == "emergency_clutch_switch_triggered"
        
        # Verify db was updated to paper mode and friday_emergency_clutch is set
        import database
        assert database.get_system_state("tg_execution_mode") == "paper"
        assert database.get_system_state("friday_emergency_clutch") != "-"

def test_clutch_bypasses_in_paper_mode(test_db_setup, monkeypatch):
    """Verifies that the emergency clutch does not trigger when the engine is in paper mode."""
    conn = sqlite3.connect(test_db_setup)
    
    # Insert recent trades with high slippage/latency
    ten_mins_ago = (datetime.now(timezone.utc) - timedelta(minutes=10)).isoformat()
    conn.execute(
        "INSERT INTO trades (symbol, direction, status, slippage, latency_ms, close_time) VALUES (?, ?, ?, ?, ?, ?)",
        ("BTCUSDT", "LONG", "closed", 0.50, 1500, ten_mins_ago)
    )
    conn.commit()
    conn.close()
    
    # Force execution mode to paper (which triggers bypass_shields = True)
    monkeypatch.setattr(config, "EXECUTION_MODE", "paper")
    
    engine = RiskEngine(MockBinanceClient(), db_path=test_db_setup)
    
    with patch("database.get_open_trades", return_value=[]):
        res = engine.calculate("BTCUSDT", "LONG", 100.0, "A+", 2000.0)
        # Should be allowed (valid) since we are in paper mode
        assert res["valid"] is True

def test_clutch_cooldown_override(test_db_setup, monkeypatch):
    """Verifies that an active clutch cooldown prevents triggering and allows trading."""
    conn = sqlite3.connect(test_db_setup)
    
    # Insert recent bad trades
    ten_mins_ago = (datetime.now(timezone.utc) - timedelta(minutes=10)).isoformat()
    conn.execute(
        "INSERT INTO trades (symbol, direction, status, slippage, latency_ms, close_time) VALUES (?, ?, ?, ?, ?, ?)",
        ("BTCUSDT", "LONG", "closed", 0.50, 1500, ten_mins_ago)
    )
    conn.commit()
    conn.close()
    
    # Set a clutch cooldown until 10 minutes in the future
    import database
    cooldown_future = (datetime.now(timezone.utc) + timedelta(minutes=10)).isoformat()
    database.set_state("friday_clutch_cooldown_until", cooldown_future)
    
    # Force execution mode to live
    monkeypatch.setattr(config, "EXECUTION_MODE", "live")
    monkeypatch.setattr(config, "BYPASS_LIVE_RISK_SHIELDS", False)
    
    engine = RiskEngine(MockBinanceClient(), db_path=test_db_setup)
    
    with patch("database.get_open_trades", return_value=[]):
        res = engine.calculate("BTCUSDT", "LONG", 100.0, "A+", 2000.0)
        # Should be valid because clutch is in cooldown override
        assert res["valid"] is True
        
        # Now set the cooldown to the past and verify it triggers again
        cooldown_past = (datetime.now(timezone.utc) - timedelta(minutes=10)).isoformat()
        database.set_state("friday_clutch_cooldown_until", cooldown_past)
        
        res_after = engine.calculate("BTCUSDT", "LONG", 100.0, "A+", 2000.0)
        assert res_after["valid"] is False
        assert res_after["risk_reject_reason"] == "emergency_clutch_switch_triggered"

def test_telegram_resume_and_live_cooldown(test_db_setup):
    """Verifies that calling /resume or /live commands clears the clutch state and sets the cooldown."""
    import database
    from telegram_manager import TelegramManager
    
    # 1. Trigger clutch state manually in db
    database.set_state("friday_emergency_clutch", "slippage=0.35,latency=1200")
    database.set_state("tg_execution_mode", "paper")
    
    # Instantiate TelegramManager
    sent_msgs = []
    def mock_send(text, reply_markup=None):
        sent_msgs.append(text)
        return True
        
    manager = TelegramManager(send_fn=mock_send)
    manager.chat_id = "123456"
    
    # 2. Simulate /resume command
    manager._handle_update({
        "message": {
            "text": "/resume",
            "chat": {"id": 123456}
        }
    })
    
    # Verify clutch is reset to "-" and cooldown is set in DB
    assert database.get_system_state("friday_emergency_clutch") == "-"
    cooldown_val = database.get_system_state("friday_clutch_cooldown_until")
    assert cooldown_val != "-"
    # Verify cooldown is in the future
    cooldown_dt = datetime.fromisoformat(cooldown_val)
    assert datetime.now(timezone.utc) < cooldown_dt
    
    # 3. Simulate /live force command (which calls _do_live)
    # Reset clutch state again
    database.set_state("friday_emergency_clutch", "slippage=0.35,latency=1200")
    
    manager._handle_update({
        "message": {
            "text": "/live force",
            "chat": {"id": 123456}
        }
    })
    
    assert database.get_system_state("friday_emergency_clutch") == "-"
    assert database.get_system_state("tg_execution_mode") == "live"
    cooldown_val_2 = database.get_system_state("friday_clutch_cooldown_until")
    assert cooldown_val_2 != "-"
    cooldown_dt_2 = datetime.fromisoformat(cooldown_val_2)
    assert datetime.now(timezone.utc) < cooldown_dt_2
