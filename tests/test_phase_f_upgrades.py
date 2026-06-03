import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest
import sqlite3
import pandas as pd
import numpy as np
from unittest.mock import MagicMock, patch
import config
from core.trend_engine import MLMarketRegimeClassifier, TrendEngine
from core.risk_engine import RiskEngine
from core.hyperparameter_tuner import check_win_rate_and_trigger_opt, optimize_ghost_filters

class MockBinanceClient:
    def __init__(self, klines=None, order_book=None):
        self._klines = klines or []
        self._order_book = order_book or {}

    def futures_klines(self, symbol, interval, limit):
        # Return a copy or slices of mock candles
        if not self._klines:
            # Return dummy candles
            candles = []
            for i in range(limit):
                candles.append([
                    0, 
                    str(100.0 + i * 0.1),  # open
                    str(101.0 + i * 0.1),  # high
                    str(99.0 + i * 0.1),   # low
                    str(100.0 + i * 0.1),  # close
                    str(1000.0 + i * 10),  # volume
                    0, "0", 0, "0", "0", "0"
                ])
            return candles
        return self._klines[:limit]

    def futures_order_book(self, symbol, limit):
        return self._order_book

@pytest.fixture
def setup_test_db(tmp_path, monkeypatch):
    """Sets up a temporary SQLite database for Phase F calculations."""
    db_path = tmp_path / "test_trading.db"
    monkeypatch.setattr(config, "DB_PATH", str(db_path))
    
    conn = sqlite3.connect(str(db_path))
    conn.execute("""
        CREATE TABLE IF NOT EXISTS system_state (
            key TEXT PRIMARY KEY,
            value TEXT,
            updated_at TEXT
        )
    """)
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
            realized_pnl        REAL DEFAULT 0,
            environment         TEXT,
            slippage            REAL DEFAULT 0,
            latency_ms          INTEGER DEFAULT 0
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS params (
            id                  INTEGER PRIMARY KEY,
            sl_atr_mult         REAL,
            risk_pct            REAL,
            tp_atr_mult         REAL,
            updated_at          TEXT
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS ghost_signals (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            candidate_id    INTEGER,
            symbol          TEXT DEFAULT '',
            timeframe       TEXT DEFAULT '5m',
            direction       TEXT DEFAULT '',
            entry_price     REAL DEFAULT 0,
            stop_loss       REAL DEFAULT 0,
            tp1             REAL DEFAULT 0,
            tp2             REAL DEFAULT 0,
            tp3             REAL DEFAULT 0,
            atr             REAL DEFAULT 0,
            final_score     REAL DEFAULT 0,
            reject_reason   TEXT DEFAULT '',
            trigger_type    TEXT DEFAULT 'UNKNOWN',
            market_regime   TEXT DEFAULT 'NEUTRAL',
            coin            TEXT DEFAULT '',
            side            TEXT DEFAULT '',
            take_profit     REAL DEFAULT 0,
            confidence      REAL DEFAULT 0,
            simulated       INTEGER DEFAULT 0,
            rsi             REAL DEFAULT 50.0,
            cvd_slope       REAL DEFAULT 0.0,
            created_at      TEXT DEFAULT (datetime('now'))
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS ghost_results (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            ghost_id        INTEGER NOT NULL,
            virtual_outcome TEXT DEFAULT 'OPEN',
            virtual_pnl_r   REAL DEFAULT 0,
            simulated_at    TEXT DEFAULT (datetime('now')),
            FOREIGN KEY (ghost_id) REFERENCES ghost_signals(id)
        )
    """)
    conn.execute("INSERT INTO params (id, risk_pct) VALUES (1, 1.0)")
    conn.execute("INSERT INTO balance_ledger (balance_after) VALUES (2000.0)")
    conn.commit()
    conn.close()
    
    return str(db_path)

def test_ml_market_regime_classifier_success():
    """Verify that MLMarketRegimeClassifier successfully clusters features when candles are available."""
    # Generate 150 varying mock candles to prevent flatlines / warnings in indicators
    mock_candles = []
    for i in range(150):
        # Varying price to produce non-zero ATR and RSI fluctuations
        price = 100.0 + np.sin(i / 5.0) * 2.0
        mock_candles.append([
            i * 3600000, 
            str(price),                # open
            str(price + 1.5),          # high
            str(price - 1.5),          # low
            str(price + 0.2),          # close
            str(1000.0 + np.sin(i) * 200), # volume
            0, "0", 0, "0", "0", "0"
        ])
    
    client = MockBinanceClient(klines=mock_candles)
    classifier = MLMarketRegimeClassifier(client)
    
    regime = classifier.classify("BTCUSDT")
    assert regime in ("TRENDING_HIGH_VOL", "TRENDING_LOW_VOL", "CHOPPY_HIGH_VOL", "CHOPPY_LOW_VOL")

def test_ml_market_regime_classifier_fallback():
    """Verify MLMarketRegimeClassifier falls back to rule-based logic when candles are empty/insufficient."""
    client = MockBinanceClient(klines=[])  # Empty
    classifier = MLMarketRegimeClassifier(client)
    
    with patch("core.trend_engine.TrendEngine.get_btc_trend", return_value="BULLISH"):
        regime = classifier.classify("BTCUSDT")
        assert regime in ("TRENDING_HIGH_VOL", "TRENDING_LOW_VOL", "CHOPPY_HIGH_VOL", "CHOPPY_LOW_VOL")

def test_order_book_wall_guard_imbalance_block(setup_test_db):
    """Verify order book bid-ask imbalance > 75% blocks entry."""
    # LONG with high asks dominance (80%)
    asks = [["100.1", "80.0"], ["100.2", "20.0"]] # 100 asks total
    bids = [["99.9", "20.0"], ["99.8", "5.0"]]    # 25 bids total
    # Total asks = 100, Total bids = 25. Total = 125. Asks ratio = 100/125 = 80% > 75%.
    client = MockBinanceClient(order_book={"bids": bids, "asks": asks})
    
    engine = RiskEngine(client, db_path=setup_test_db)
    
    # LONG test
    is_blocked, reason = engine.check_order_book_wall("BTCUSDT", "LONG", 100.0)
    assert is_blocked is True
    assert "order_book_wall_block" in reason
    assert "ask_imbalance" in reason

    # SHORT with high bids dominance (80%)
    asks = [["100.1", "20.0"], ["100.2", "5.0"]]
    bids = [["99.9", "80.0"], ["99.8", "20.0"]]
    # Total bids = 100, Total asks = 25. Bids ratio = 100/125 = 80% > 75%.
    client = MockBinanceClient(order_book={"bids": bids, "asks": asks})
    engine = RiskEngine(client, db_path=setup_test_db)
    
    # SHORT test
    is_blocked, reason = engine.check_order_book_wall("BTCUSDT", "SHORT", 100.0)
    assert is_blocked is True
    assert "order_book_wall_block" in reason
    assert "bid_imbalance" in reason

def test_order_book_wall_guard_passive_wall_block(setup_test_db):
    """Verify that a passive opposing wall close to entry blocks entry."""
    # LONG setup: opposing side is asks.
    # We want a thick ask wall within SCALP_OB_WALL_PCT (0.2%) of entry_price (100.0) -> wall price <= 100.2
    # Standard asks: one massive wall and many tiny levels
    # Let's say we have 10 ask levels. One level at 100.10 with 500.0 qty. Nine levels at 100.15 with 1.0 qty.
    # Total asks qty = 509. Average = 50.9. Wall multiplier is 5.0x average = 254.5.
    # Since 500.0 > 254.5, it should trigger the wall block.
    # We add 500.0 bid quantity so that asks/bids ratio is (509/1009) = 50.4%, which is <= 75%.
    bids = [["99.9", "500.0"]]
    asks = [["100.10", "500.0"]] + [["100.15", "1.0"] for _ in range(9)]
    client = MockBinanceClient(order_book={"bids": bids, "asks": asks})
    
    engine = RiskEngine(client, db_path=setup_test_db)
    is_blocked, reason = engine.check_order_book_wall("BTCUSDT", "LONG", 100.0)
    assert is_blocked is True
    assert "order_book_wall_block" in reason
    assert "sell_wall" in reason

    # SHORT setup: opposing side is bids.
    # Thick bid wall within 0.2% of entry_price (100.0) -> wall price >= 99.8
    # One level at 99.90 with 500.0 qty, nine levels at 99.85 with 1.0 qty.
    # We add 500.0 ask quantity so asks/bids ratio is balanced (500/1009) = 49.6% <= 75%.
    bids = [["99.90", "500.0"]] + [["99.85", "1.0"] for _ in range(9)]
    asks = [["100.1", "500.0"]]
    client = MockBinanceClient(order_book={"bids": bids, "asks": asks})
    engine = RiskEngine(client, db_path=setup_test_db)
    is_blocked, reason = engine.check_order_book_wall("BTCUSDT", "SHORT", 100.0)
    assert is_blocked is True
    assert "order_book_wall_block" in reason
    assert "buy_wall" in reason

def test_order_book_wall_guard_calculate_integration(setup_test_db):
    """Verify that RiskEngine's calculate intercept blocks trade when order book wall guard blocks."""
    client = MockBinanceClient()
    engine = RiskEngine(client, db_path=setup_test_db)
    
    # Mock check_order_book_wall to block
    with patch.object(engine, "check_order_book_wall", return_value=(True, "order_book_wall_block (mocked_imbalance)")):
        res = engine.calculate("BTCUSDT", "LONG", 100.0, "A", 2000.0)
        assert res["valid"] is False
        assert res["risk_reject_reason"] == "order_book_wall_block (mocked_imbalance)"

def test_self_healing_win_rate_trigger(setup_test_db):
    """Verify check_win_rate_and_trigger_opt triggers when win rate < 50% on 20 closed trades."""
    # Case 1: Less than 20 trades -> should return False
    conn = sqlite3.connect(setup_test_db)
    for i in range(10):
        conn.execute(
            "INSERT INTO trades (symbol, direction, status, realized_pnl, environment) "
            "VALUES ('BTCUSDT', 'LONG', 'closed', -10.0, 'paper')"
        )
    conn.commit()
    conn.close()
    
    assert check_win_rate_and_trigger_opt(setup_test_db) is False

    # Case 2: 20 closed trades, 12 losses, 8 wins (win rate = 40% < 50%) -> should return True
    conn = sqlite3.connect(setup_test_db)
    # Clear previous trades
    conn.execute("DELETE FROM trades")
    # Insert 12 losses
    for i in range(12):
        conn.execute(
            "INSERT INTO trades (symbol, direction, status, realized_pnl, environment) "
            "VALUES ('BTCUSDT', 'LONG', 'closed', -10.0, 'paper')"
        )
    # Insert 8 wins
    for i in range(8):
        conn.execute(
            "INSERT INTO trades (symbol, direction, status, realized_pnl, environment) "
            "VALUES ('BTCUSDT', 'LONG', 'closed', 20.0, 'paper')"
        )
    conn.commit()
    conn.close()
    
    assert check_win_rate_and_trigger_opt(setup_test_db) is True

    # Case 3: 20 closed trades, 12 wins, 8 losses (win rate = 60% >= 50%) -> should return False
    conn = sqlite3.connect(setup_test_db)
    conn.execute("DELETE FROM trades")
    # Insert 12 wins
    for i in range(12):
        conn.execute(
            "INSERT INTO trades (symbol, direction, status, realized_pnl, environment) "
            "VALUES ('BTCUSDT', 'LONG', 'closed', 20.0, 'paper')"
        )
    # Insert 8 losses
    for i in range(8):
        conn.execute(
            "INSERT INTO trades (symbol, direction, status, realized_pnl, environment) "
            "VALUES ('BTCUSDT', 'LONG', 'closed', -10.0, 'paper')"
        )
    conn.commit()
    conn.close()
    
    assert check_win_rate_and_trigger_opt(setup_test_db) is False

def test_self_healing_optuna_ghost_optimization(setup_test_db):
    """Verify that optimize_ghost_filters runs Optuna study on simulated ghost signals and finds parameters."""
    conn = sqlite3.connect(setup_test_db)
    # Insert 10 ghost signals and outcomes
    for i in range(10):
        # We need a mix of LONG/SHORT ghost signals with different RSI/CVD slope and outcomes
        direction = "LONG" if i % 2 == 0 else "SHORT"
        rsi = 35.0 if direction == "LONG" else 65.0
        cvd_slope = 0.05 if direction == "LONG" else -0.05
        
        conn.execute(
            "INSERT INTO ghost_signals (id, direction, side, rsi, cvd_slope, simulated) "
            "VALUES (?, ?, ?, ?, ?, 1)",
            (i + 1, direction, direction, rsi, cvd_slope)
        )
        
        outcome = "WIN" if i % 3 != 0 else "LOSS"
        pnl_r = 2.0 if outcome == "WIN" else -1.0
        conn.execute(
            "INSERT INTO ghost_results (ghost_id, virtual_outcome, virtual_pnl_r) "
            "VALUES (?, ?, ?)",
            (i + 1, outcome, pnl_r)
        )
    conn.commit()
    conn.close()
    
    res = optimize_ghost_filters(setup_test_db)
    assert res is not None
    rsi_limit, cvd_filter_val, best_val = res
    assert 20.0 <= rsi_limit <= 45.0
    assert -0.20 <= cvd_filter_val <= 0.10

def test_emergency_clutch_switch(setup_test_db):
    """Verify that RiskEngine calculates rejects and switches execution mode to paper on high latency/slippage."""
    # Write 3 high latency/slippage closed trades to DB
    conn = sqlite3.connect(setup_test_db)
    for _ in range(3):
        conn.execute("INSERT INTO trades (symbol, direction, status, realized_pnl, environment, slippage, latency_ms) "
                     "VALUES ('BTCUSDT', 'LONG', 'closed', 10.0, 'live', 0.30, 950)")
    conn.commit()
    conn.close()

    engine = RiskEngine(None, db_path=setup_test_db)
    
    with patch("database.get_open_trades", return_value=[]), \
         patch("database.get_market_regime", return_value="NEUTRAL"):
        res = engine.calculate("BTCUSDT", "LONG", 100.0, "A", 2000.0)
        assert res["valid"] is False
        assert res["risk_reject_reason"] == "emergency_clutch_switch_triggered"
        
        # Check that system_state execution mode was switched to paper
        from database import get_system_state
        assert get_system_state("tg_execution_mode") == "paper"

def test_ai_decision_consensus_gate():
    """Verify that AIDecisionService consensus gate correctly vetoes signals on momentum/macro anomalies."""
    from core.services.ai_decision_service import AIDecisionService
    from core.data_layer import SignalData
    
    service = AIDecisionService()
    
    # 1. Test LONG consensus veto on extreme RSI overbought
    sig_overbought = SignalData(symbol="BTCUSDT")
    sig_overbought.direction = "LONG"
    sig_overbought.metadata = {
        "rsi5": 80.0,
        "momentum_3c": 0.0,
        "ob_ratio": 1.0,
        "cvd_slope": 0.0,
        "btc_trend": "NEUTRAL"
    }
    
    passed, reason = service._check_consensus(sig_overbought)
    assert passed is False
    assert "micro_momentum_veto" in reason
    
    # 2. Test SHORT consensus veto on extreme buyer dominance (ob_ratio > 3.0)
    sig_ob_ratio = SignalData(symbol="BTCUSDT")
    sig_ob_ratio.direction = "SHORT"
    sig_ob_ratio.metadata = {
        "rsi5": 50.0,
        "momentum_3c": 0.0,
        "ob_ratio": 4.5,
        "cvd_slope": 0.0,
        "btc_trend": "NEUTRAL"
    }
    passed, reason = service._check_consensus(sig_ob_ratio)
    assert passed is False
    assert "macro_watchdog_veto" in reason

    # 3. Test fully compliant LONG signals pass consensus gate
    sig_compliant = SignalData(symbol="BTCUSDT")
    sig_compliant.direction = "LONG"
    sig_compliant.metadata = {
        "rsi5": 55.0,
        "momentum_3c": 0.5,
        "ob_ratio": 1.1,
        "cvd_slope": 0.02,
        "btc_trend": "BULLISH"
    }
    passed, reason = service._check_consensus(sig_compliant)
    assert passed is True

def test_database_integrity_and_backup(setup_test_db, tmp_path):
    """Verify database hot backups can be created and corrupted database can be restored autonomously."""
    from database import create_hot_backup, check_and_recover_db
    import os
    
    # Create hot backup
    create_hot_backup(setup_test_db)
    
    # Verify backup file exists
    backup_dir = os.path.join(os.path.dirname(setup_test_db), "backups")
    backup_file = os.path.join(backup_dir, "trading_backup_hot.db")
    assert os.path.exists(backup_file)
    
    # Now simulate db corruption by truncating the main db file
    with open(setup_test_db, "wb") as f:
        f.write(b"garbage_and_corrupt_data")
        
    # Check that database connection now fails and check_and_recover_db restores it
    check_and_recover_db(setup_test_db)
    
    # Verify we can open the database and it is healthy again
    conn = sqlite3.connect(setup_test_db)
    row = conn.execute("SELECT balance_after FROM balance_ledger WHERE id=1").fetchone()
    conn.close()
    assert row[0] == 2000.0

