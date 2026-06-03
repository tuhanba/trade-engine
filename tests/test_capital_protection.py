import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest
import sqlite3
import config
from core.risk_engine import RiskEngine
from unittest.mock import MagicMock, patch

class MockBinanceClient:
    def futures_klines(self, symbol, interval, limit):
        # Return mock candlesticks (for ATR calculation)
        # 14 rows of standard mock klines: open, high, low, close
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
def setup_test_db(tmp_path, monkeypatch):
    """Sets up a temporary SQLite database for risk calculation tests."""
    db_path = tmp_path / "test_trading.db"
    monkeypatch.setattr(config, "DB_PATH", str(db_path))
    
    # Initialize schema
    conn = sqlite3.connect(str(db_path))
    conn.execute("""
        CREATE TABLE IF NOT EXISTS balance_ledger (
            id             INTEGER PRIMARY KEY AUTOINCREMENT,
            trade_id       INTEGER,
            symbol         TEXT DEFAULT '',
            event_type     TEXT DEFAULT 'CLOSE',
            amount         REAL NOT NULL DEFAULT 0,
            balance_before REAL DEFAULT 0,
            balance_after  REAL NOT NULL DEFAULT 0,
            note           TEXT DEFAULT '',
            created_at     TEXT DEFAULT (datetime('now'))
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS trades (
            id                  INTEGER PRIMARY KEY AUTOINCREMENT,
            symbol              TEXT NOT NULL,
            direction           TEXT NOT NULL DEFAULT 'LONG',
            status              TEXT DEFAULT 'OPEN',
            net_pnl             REAL DEFAULT 0,
            environment         TEXT
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS params (
            id                  INTEGER PRIMARY KEY,
            sl_atr_mult         REAL,
            risk_pct            REAL,
            tp_atr_mult         REAL
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS coin_cooldown (
            symbol TEXT PRIMARY KEY,
            until TEXT NOT NULL,
            consec_losses INTEGER DEFAULT 0
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS coin_profiles (
            symbol TEXT PRIMARY KEY,
            win_rate REAL,
            avg_mae REAL,
            avg_mfe REAL,
            fakeout_rate REAL,
            danger_score REAL,
            total_trades INTEGER
        )
    """)
    conn.execute("INSERT INTO params (id, risk_pct) VALUES (1, 1.0)")
    conn.commit()
    conn.close()
    
    return str(db_path)

def test_drawdown_hard_lockout(setup_test_db):
    """Verifies that drawdown >= DRAWDOWN_LOCK_PCT (10%) blocks trading."""
    conn = sqlite3.connect(setup_test_db)
    # Insert balance ledger entries showing a peak at 2000 and current at 1700 (15% drawdown)
    conn.execute("INSERT INTO balance_ledger (balance_after) VALUES (2000.0)")
    conn.execute("INSERT INTO balance_ledger (balance_after) VALUES (1700.0)")
    conn.commit()
    conn.close()

    # Instantiate RiskEngine with test DB
    engine = RiskEngine(MockBinanceClient(), db_path=setup_test_db)
    
    # Check trade approval (Current balance: 1700.0)
    # 15% drawdown is >= 10.0% max limit, should block trade
    res = engine.calculate("BTCUSDT", "LONG", 100.0, "A+", 1700.0)
    assert res["valid"] is False
    assert res["risk_reject_reason"] == "drawdown_hard_lock"

def test_drawdown_defensive_mode(setup_test_db):
    """Verifies that drawdown >= DRAWDOWN_DEFENSIVE_PCT (5%) reduces risk by 50%."""
    conn = sqlite3.connect(setup_test_db)
    # Peak at 2000, current at 1880 (6% drawdown)
    # Drawdown is 6% which is between 5% and 10%. Risk should be scaled by 0.5x.
    conn.execute("INSERT INTO balance_ledger (balance_after) VALUES (2000.0)")
    conn.execute("INSERT INTO balance_ledger (balance_after) VALUES (1880.0)")
    conn.commit()
    conn.close()

    engine = RiskEngine(MockBinanceClient(), db_path=setup_test_db)
    
    # We must patch calculate_kelly_risk_pct to return a constant base risk of 1.0
    with patch("core.risk_engine.calculate_kelly_risk_pct", return_value=1.0):
        # A+ setup has quality_mult of 1.5. So base risk = 1.0 * 1.5 = 1.5%
        # Drawdown defensive scaling (0.5x) should reduce risk to 0.75%
        res = engine.calculate("BTCUSDT", "LONG", 100.0, "A+", 1880.0)
        assert res["valid"] is True
        assert res["risk_pct"] == 0.75

def test_equity_curve_sma_filter(setup_test_db):
    """Verifies that if current balance is below Equity SMA, risk is scaled by 50%."""
    conn = sqlite3.connect(setup_test_db)
    # Insert 10 entries of balance_after (SMA will be 1000)
    for _ in range(10):
        conn.execute("INSERT INTO balance_ledger (balance_after) VALUES (1000.0)")
    conn.commit()
    conn.close()

    engine = RiskEngine(MockBinanceClient(), db_path=setup_test_db)
    
    with patch("core.risk_engine.calculate_kelly_risk_pct", return_value=1.0):
        # Current balance is 900 (below SMA of 1000). Drawdown is 100/1000 = 10% (this triggers lockout)
        # So let's make current balance 980 (drawdown 2%, which doesn't trigger drawdown scaling)
        # SMA is 1000, current balance 980 is below SMA -> Equity curve filter reduces risk by 0.5x.
        # Quality mult A+ (1.5) -> risk_pct should be 1.0 * 1.5 * 0.5 = 0.75%
        res = engine.calculate("BTCUSDT", "LONG", 100.0, "A+", 980.0)
        assert res["valid"] is True
        assert res["risk_pct"] == 0.75

def test_mtf_trend_alignment_counter_trend(setup_test_db):
    """Verifies that if trade direction opposes 1H/4H trend, risk is scaled by 0.4x."""
    conn = sqlite3.connect(setup_test_db)
    # 2000.0 balance, no drawdown
    conn.execute("INSERT INTO balance_ledger (balance_after) VALUES (2000.0)")
    conn.commit()
    conn.close()

    client = MockBinanceClient()
    engine = RiskEngine(client, db_path=setup_test_db)

    # Mock TrendEngine get_1h_trend to return BEARISH, which opposes LONG direction
    with patch("core.risk_engine.calculate_kelly_risk_pct", return_value=1.0), \
         patch("core.trend_engine.TrendEngine.get_1h_trend", return_value="BEARISH"), \
         patch("core.trend_engine.TrendEngine.get_4h_trend", return_value="NEUTRAL"):
        # Base risk: 1.0 (Kelly) * 1.5 (A+ quality) = 1.5%
        # Opposing trend reduction: 0.4x -> 1.5 * 0.4 = 0.6%
        res = engine.calculate("BTCUSDT", "LONG", 100.0, "A+", 2000.0)
        assert res["valid"] is True
        assert res["risk_pct"] == 0.6

def test_dynamic_cooldown_win(setup_test_db):
    """Verifies win cooldown calculations based on ATR% (15m base)."""
    from execution_engine import set_dynamic_cooldown
    
    # Win trade, total_pnl > 0
    trade = {
        "symbol": "BTCUSDT",
        "metadata": '{"atr_pct": 0.04}' # 4% volatility
    }
    
    # PnL > 0 (WIN)
    # Win base = 15.0. 15 * (1 + 0.04 * 5) = 15 * 1.20 = 18.0
    cooldown = set_dynamic_cooldown(trade, total_pnl=10.0)
    assert cooldown == 18.0
    
    # Verify DB entry
    conn = sqlite3.connect(setup_test_db)
    row = conn.execute("SELECT until FROM coin_cooldown WHERE symbol='BTCUSDT'").fetchone()
    conn.close()
    assert row is not None

def test_dynamic_cooldown_loss(setup_test_db):
    """Verifies loss cooldown calculations based on ATR% (60m base)."""
    from execution_engine import set_dynamic_cooldown
    
    # Loss trade, total_pnl <= 0
    trade = {
        "symbol": "ETHUSDT",
        "metadata": '{"atr_pct": 0.02}' # 2% volatility
    }
    
    # PnL <= 0 (LOSS)
    # Loss base = 60.0. 60 * (1 + 0.02 * 10) = 60 * 1.2 = 72.0
    cooldown = set_dynamic_cooldown(trade, total_pnl=-5.0)
    assert cooldown == 72.0
    
    # Verify DB entry
    conn = sqlite3.connect(setup_test_db)
    row = conn.execute("SELECT until FROM coin_cooldown WHERE symbol='ETHUSDT'").fetchone()
    conn.close()
    assert row is not None

def test_equity_curve_sma_filter_disabled(setup_test_db):
    """Verifies that if EQUITY_CURVE_FILTER_ENABLED is False, risk is NOT scaled down."""
    conn = sqlite3.connect(setup_test_db)
    for _ in range(10):
        conn.execute("INSERT INTO balance_ledger (balance_after) VALUES (1000.0)")
    conn.commit()
    conn.close()

    engine = RiskEngine(MockBinanceClient(), db_path=setup_test_db)
    
    with patch("core.risk_engine.calculate_kelly_risk_pct", return_value=1.0), \
         patch("config.EQUITY_CURVE_FILTER_ENABLED", False):
        res = engine.calculate("BTCUSDT", "LONG", 100.0, "A+", 980.0)
        assert res["valid"] is True
        assert res["risk_pct"] == 1.5
