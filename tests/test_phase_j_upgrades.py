import pytest
import sqlite3
import os
from unittest.mock import MagicMock, patch
import config
import database
from core.risk_engine import RiskEngine, check_coin_cooldown, calculate_kelly_risk_pct
from core.trailing_engine import TrailingEngine, TradeExitState

@pytest.fixture
def setup_test_db():
    db_file = "test_phase_j.db"
    if os.path.exists(db_file):
        os.remove(db_file)
        
    # Set config DB path
    orig_db = config.DB_PATH
    config.DB_PATH = db_file
    
    # Initialize DB schema
    database.init_db()
    
    yield db_file
    
    # Cleanup
    import gc
    import time
    gc.collect()
    for _ in range(10):
        try:
            if os.path.exists(db_file):
                os.remove(db_file)
            for ext in ["-wal", "-shm"]:
                extra_file = db_file + ext
                if os.path.exists(extra_file):
                    os.remove(extra_file)
            break
        except PermissionError:
            time.sleep(0.1)
            
    config.DB_PATH = orig_db


def test_ghost_warmup_win_rate_calc(setup_test_db):
    # Verify that get_ghost_warmup_win_rate computes correct outcomes
    # Insert some ghost signals and results
    with database.get_conn() as conn:
        # Create mock ghost signals
        conn.execute("INSERT INTO ghost_signals (id, symbol, coin, simulated) VALUES (1, 'BTCUSDT', 'BTCUSDT', 1)")
        conn.execute("INSERT INTO ghost_signals (id, symbol, coin, simulated) VALUES (2, 'BTCUSDT', 'BTCUSDT', 1)")
        conn.execute("INSERT INTO ghost_signals (id, symbol, coin, simulated) VALUES (3, 'ETHUSDT', 'ETHUSDT', 1)")
        
        # Create mock ghost results
        conn.execute("INSERT INTO ghost_results (ghost_id, virtual_outcome) VALUES (1, 'WIN')")
        conn.execute("INSERT INTO ghost_results (ghost_id, virtual_outcome) VALUES (2, 'LOSS')")
        conn.execute("INSERT INTO ghost_results (ghost_id, virtual_outcome) VALUES (3, 'WIN')")
        conn.commit()

    # 1. Global win rate: should be 2 wins out of 3 total -> 2/3 = 0.6666...
    wr, total = database.get_ghost_warmup_win_rate(None, lookback=10)
    assert total == 3
    assert wr == pytest.approx(0.666, 0.01)

    # 2. Symbol specific (BTCUSDT): 1 win, 1 loss -> 1/2 = 0.50
    wr_btc, total_btc = database.get_ghost_warmup_win_rate('BTCUSDT', lookback=10)
    assert total_btc == 2
    assert wr_btc == 0.50

    # 3. Symbol specific (ETHUSDT): 1 win -> 1/1 = 1.00
    wr_eth, total_eth = database.get_ghost_warmup_win_rate('ETHUSDT', lookback=10)
    assert total_eth == 1
    assert wr_eth == 1.00


def test_ghost_warmup_bypass_logic(setup_test_db):
    # Custom real-cooldown check for the SQLite fallback in testing
    def custom_is_coin_in_cooldown(symbol: str) -> bool:
        from datetime import datetime, timezone
        now_str = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
        with database.get_conn() as conn:
            row = conn.execute(
                "SELECT until FROM coin_cooldown WHERE symbol = ? AND until > ?",
                (symbol, now_str)
            ).fetchone()
            return row is not None

    with patch("database.is_coin_in_cooldown", side_effect=custom_is_coin_in_cooldown), \
         patch("config.GHOST_WARMUP_ENABLED", True):
        # Set coin in cooldown
        with database.get_conn() as conn:
            conn.execute("INSERT INTO coin_cooldown (symbol, until) VALUES ('BTCUSDT', datetime('now', '+1 hour'))")
            # Global boss cooldown
            conn.execute("""
                INSERT INTO system_state (key, value, updated_at)
                VALUES ('friday_boss_cooldown_until', datetime('now', '+1 hour'), datetime('now'))
                ON CONFLICT(key) DO UPDATE SET value=excluded.value
            """)
            conn.commit()

        # Base case: should not bypass because no ghost trades exist yet (count < 3)
        assert check_coin_cooldown('BTCUSDT') is False

        # Insert 3 simulated WIN ghost trades globally and for BTCUSDT
        with database.get_conn() as conn:
            for i in range(10, 13):
                conn.execute(f"INSERT INTO ghost_signals (id, symbol, coin, simulated) VALUES ({i}, 'BTCUSDT', 'BTCUSDT', 1)")
                conn.execute(f"INSERT INTO ghost_results (ghost_id, virtual_outcome) VALUES ({i}, 'WIN')")
            conn.commit()

        # Now, BTCUSDT has 3 WIN trades (win rate 1.0 >= 0.55). It should bypass the cooldown!
        assert check_coin_cooldown('BTCUSDT') is True

    # Now test Boss Cooldown bypass in RiskEngine.calculate
    client = MagicMock()
    # Mock client ticker
    client.futures_klines.return_value = [
        [0, 100, 101, 99, 100, 1000, 0, 0, 0, 0, 0, 0] for _ in range(20)
    ]
    re = RiskEngine(client)
    
    with patch("config.GHOST_WARMUP_ENABLED", True), \
         patch("config.GHOST_WARMUP_MIN_WIN_RATE", 0.55), \
         patch("config.GHOST_WARMUP_TRADES_LOOKBACK", 10), \
         patch("config.EXECUTION_MODE", "live"), \
         patch("config.LIVE_TRADING_ENABLED", True), \
         patch("config.CONFIRM_LIVE_TRADING", True), \
         patch("config.BYPASS_LIVE_RISK_SHIELDS", False):
         
        # With enough global win rate, calculate shouldn't be blocked by boss cooldown
        res = re.calculate('BTCUSDT', 'LONG', 100.0, 'A', 2000.0, 75.0)
        assert res.get("risk_reject_reason") != "friday_boss_cooldown"


def test_dynamic_kelly_sizing_logic(setup_test_db):
    # Insert 5 trades with WIN/LOSS to establish Kelly baseline
    # 7-day rolling trades to trigger dynamic compounding/downscaling
    with database.get_conn() as conn:
        # Altcoin BTCUSDT has 4 WIN trades and 1 LOSS trade in the last 7 days -> 80% Win Rate
        for i in range(1, 6):
            pnl = 15.0 if i < 5 else -5.0
            conn.execute("""
                INSERT INTO trades (id, symbol, direction, entry, close_price, qty, realized_pnl, net_pnl, status, close_time, environment)
                VALUES (?, 'BTCUSDT', 'LONG', 100.0, 105.0, 1.0, ?, ?, 'closed', datetime('now', '-2 days'), 'live')
            """, (i, pnl, pnl))
        
        # ETHUSDT has 1 WIN trade and 4 LOSS trades in the last 7 days -> 20% Win Rate
        for i in range(10, 15):
            pnl = 15.0 if i == 10 else -5.0
            conn.execute("""
                INSERT INTO trades (id, symbol, direction, entry, close_price, qty, realized_pnl, net_pnl, status, close_time, environment)
                VALUES (?, 'ETHUSDT', 'LONG', 100.0, 105.0, 1.0, ?, ?, 'closed', datetime('now', '-2 days'), 'live')
            """, (i, pnl, pnl))
        conn.commit()

    # Test compounding for BTCUSDT (win rate = 80% >= 60%)
    with patch("config.DYNAMIC_KELLY_ENABLED", True), \
         patch("config.DYNAMIC_KELLY_LOOKBACK_DAYS", 7), \
         patch("config.RISK_PCT", 1.0):
        # BTCUSDT win rate 80% should apply 1.3x compounding
        # Let's check calculate_kelly_risk_pct directly
        risk_btc = calculate_kelly_risk_pct('BTCUSDT', 2.0, 1.0)
        
        # ETHUSDT win rate 20% should apply 0.5x downscale
        risk_eth = calculate_kelly_risk_pct('ETHUSDT', 2.0, 1.0)
        
        # Compounded BTC risk should be significantly higher than downscaled ETH risk
        assert risk_btc > risk_eth


def test_chandelier_runner_exit_logic():
    # Verify TrailingEngine TP3 Chandelier trailing exit logic
    engine = TrailingEngine()
    
    trade = {
        "id": 123,
        "symbol": "BTCUSDT",
        "direction": "LONG",
        "entry": 100.0,
        "sl": 95.0,
        "tp1": 105.0,
        "tp2": 110.0,
        "tp3": 120.0,
        "qty": 10.0
    }
    
    # 1. State: tp2_hit = True, trailing_active = True
    state = TradeExitState(
        tp1_hit=True,
        tp2_hit=True,
        tp3_hit=False,
        trailing_active=True,
        current_sl=100.0,
        highest_price=115.0,
        qty_remaining_pct=30.0
    )
    
    # Let's test that when price climbs to 121 (hitting TP3=120) with Chandelier Exit enabled
    with patch("config.CHANDELIER_EXIT_ENABLED", True), \
         patch("config.CHANDELIER_ATR_MULT", 3.0):
             
        res = engine.evaluate(trade, 121.0, state, atr=2.0)
        
        # Instead of should_full_close=True, it should execute partial close
        assert res.should_partial_close is True
        assert res.should_full_close is False
        assert res.reason == "TP3_CHANDELIER_ACTIVE"
        assert state.tp3_hit is True
        assert state.trailing_active is True
        assert state.qty_remaining_pct == 15.0 # closed half of remaining 30.0

    # 2. Test the Chandelier SL trailing update
    # In section 6, if trailing_active is True and tp2_hit is True, Chandelier SL should update based on highest_price
    state2 = TradeExitState(
        tp1_hit=True,
        tp2_hit=True,
        tp3_hit=True,
        trailing_active=True,
        current_sl=110.0,
        highest_price=130.0, # price peaked at 130
        qty_remaining_pct=15.0
    )
    
    # With ATR=2.0 and MULT=3.0, Chandelier SL = 130.0 - 2.0 * 3.0 = 124.0
    # Since 124.0 > 110.0, current_sl should update to 124.0
    with patch("config.CHANDELIER_EXIT_ENABLED", True), \
         patch("config.CHANDELIER_ATR_MULT", 3.0):
             
        res2 = engine.evaluate(trade, 128.0, state2, atr=2.0)
        assert state2.current_sl == 124.0
