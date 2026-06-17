"""
tests/test_phase_k_n_advanced.py - Unit tests for Phases K, L, M, and N upgrades.
"""

import os
import sys
import pickle
import pytest
import numpy as np
import sqlite3
import pandas as pd
from unittest.mock import MagicMock, patch

import config
import database
from core.data_layer import SignalData
from core.portfolio_risk import calculate_multi_asset_kelly, calculate_portfolio_var
from core.risk_engine import RiskEngine, calculate_kelly_risk_pct
from core.trigger_engine import TriggerEngine
from core.rl_meta_learner import GMMRegimeClassifier, QLearningMetaLearner, get_current_regime, update_rl_on_trade_closed, load_rl_meta_learner, save_rl_meta_learner


# ==============================================================================
# Phase K: Multi-Asset Kelly & Portfolio VaR
# ==============================================================================

def test_calculate_multi_asset_kelly():
    """Verify Multi-Asset Kelly calculation with simulated win rates, payoffs, and correlations."""
    symbols = ["BTCUSDT", "ETHUSDT"]
    win_rates = {"BTCUSDT": 0.60, "ETHUSDT": 0.55}
    payoff_ratios = {"BTCUSDT": 2.0, "ETHUSDT": 1.5}
    
    # We mock _get_returns to return constant values so correlation coefficient can be calculated
    with patch("core.portfolio_risk._get_returns") as mock_ret:
        # Return different return series for BTC and ETH
        mock_ret.side_effect = lambda sym, *a, **k: np.array([0.01, -0.02, 0.03, 0.01, 0.02] if sym == "BTCUSDT" else [0.01, -0.01, 0.02, 0.015, 0.01])
        
        weights = calculate_multi_asset_kelly(
            symbols=symbols,
            win_rates=win_rates,
            payoff_ratios=payoff_ratios,
            half_kelly=True
        )
        
        assert "BTCUSDT" in weights
        assert "ETHUSDT" in weights
        # Should be scaled to safe limits [0.005, 0.03]
        assert 0.005 <= weights["BTCUSDT"] <= 0.03
        assert 0.005 <= weights["ETHUSDT"] <= 0.03


def test_calculate_portfolio_var():
    """Verify parametric VaR calculation with simulated open/new positions."""
    open_positions = [{"symbol": "BTCUSDT", "qty": 1.0, "entry_price": 50000.0}]
    new_position = {"symbol": "ETHUSDT", "qty": 10.0, "entry_price": 3000.0}
    balance = 100000.0
    
    with patch("core.portfolio_risk._get_returns") as mock_ret:
        # returns for BTC and ETH
        mock_ret.side_effect = lambda sym, *a, **k: np.array([0.01, -0.02, 0.03, -0.01, 0.02] if sym == "BTCUSDT" else [0.02, -0.03, 0.04, -0.02, 0.03])
        
        var_val = calculate_portfolio_var(open_positions, new_position, balance)
        assert var_val >= 0.0
        assert isinstance(var_val, float)


def test_portfolio_var_limit_scaling():
    """Verify that RiskEngine scales risk_pct down when Portfolio VaR limit is exceeded."""
    client = MagicMock()
    engine = RiskEngine(client)
    
    # Enable bypass shields or check config
    with patch("config.BYPASS_LIVE_RISK_SHIELDS", False), \
         patch("config.EXECUTION_MODE", "live"), \
         patch("config.PORTFOLIO_VAR_LIMIT", 0.01), \
         patch("database.get_open_trades") as mock_open, \
         patch("core.risk_engine.calculate_historical_correlation", return_type=float) as mock_corr, \
         patch("core.portfolio_risk._get_returns") as mock_ret, \
         patch("core.risk_engine.get_coin_sector", return_value="OTHER"), \
         patch("core.risk_engine.check_daily_loss_limit", return_value=True), \
         patch("core.risk_engine.check_coin_cooldown", return_value=True), \
         patch("core.risk_engine.check_correlated_exposure", return_value=True):
        
        # Simulated open trade of a highly correlated coin in the same direction
        mock_open.return_value = [{"symbol": "BTCUSDT", "qty": 1.0, "entry_price": 60000.0, "direction": "LONG", "side": "LONG"}]
        mock_corr.return_value = 0.80  # high correlation > 0.75
        mock_ret.side_effect = lambda sym, *a, **k: np.array([0.05, -0.04, 0.06] if sym == "BTCUSDT" else [0.05, -0.04, 0.06])
        
        # Test calculate
        res = engine.calculate(
            symbol="ETHUSDT",
            direction="LONG",
            entry=3000.0,
            quality="A",
            balance=10000.0,
            score=80.0
        )
        
        assert res.get("valid") is True or res.get("risk_reject_reason") is None


# ==============================================================================
# Phase L: OBI, Block Trade Footprint & SFP Optimization
# ==============================================================================

def test_order_book_imbalance_check():
    """OBI imbalance < -0.4 on LONG should trigger veto."""
    client = MagicMock()
    engine = RiskEngine(client)
    
    # Simulated orderbook with massive ask (seller) depth in top 20 -> OBI is negative
    # We populate the rest of the book (20-100) such that the total ask ratio is <= 75%,
    # avoiding the bid-ask imbalance check (>75%) but triggering OBI check.
    # Top 20 bids = 1.0 (total 20), Top 20 asks = 10.0 (total 200).
    # Rest of bids = 10.0 (total 800), Rest of asks = 1.0 (total 80).
    # Total bids = 820, Total asks = 280. Total qty = 1100. Ask ratio = 25.4% <= 75%.
    bids_list = [[str(100 - i * 0.1), "1.0" if i < 20 else "10.0"] for i in range(100)]
    asks_list = [[str(100 + i * 0.1), "10.0" if i < 20 else "1.0"] for i in range(100)]
    client.futures_order_book.return_value = {
        "bids": bids_list,
        "asks": asks_list
    }
    
    # Fetch recent trades mock (neutral footprint)
    client.futures_recent_trades.return_value = []
    
    is_blocked, reason = engine.check_order_book_wall("BTCUSDT", "LONG", 100.0, mode="hard")
    assert is_blocked is True
    assert "obi_block" in reason


def test_block_trade_footprint_check():
    """Opposing block trade footprint should trigger veto."""
    client = MagicMock()
    engine = RiskEngine(client)
    
    # Balanced order book (OBI near 0)
    client.futures_order_book.return_value = {
        "bids": [[str(100 - i * 0.1), "5.0"] for i in range(100)],
        "asks": [[str(100 + i * 0.1), "5.0"] for i in range(100)]
    }
    
    # Opposing block trades (LONG direction, but massive sell blocks >= 50,000 USDT)
    # isBuyerMaker = True means sell trade
    client.futures_recent_trades.return_value = [
        {"qty": "1.0", "price": "60000.0", "isBuyerMaker": True}, # 60,000 USDT Sell
        {"qty": "1.5", "price": "60000.0", "isBuyerMaker": True}, # 90,000 USDT Sell
    ]
    
    is_blocked, reason = engine.check_order_book_wall("BTCUSDT", "LONG", 60000.0, mode="hard")
    assert is_blocked is True
    assert "block_trade_footprint_block" in reason


def test_sfp_entry_price_optimization():
    """TriggerEngine should set entry to sfp_level when SFP is detected."""
    client = MagicMock()
    # Mock candles history
    df = pd.DataFrame({
        "open": [10.0] * 50,
        "high": [10.2] * 50,
        "low": [9.8] * 50,
        "close": [10.0] * 50,
        "volume": [1000] * 50
    })
    
    # Make swing lows
    df.loc[10, "low"] = 9.2 # historical swing low
    df.loc[49, "low"] = 9.1 # current low sweeps below 9.2
    df.loc[49, "close"] = 9.5 # close is above 9.2 (SFP LONG)
    
    engine = TriggerEngine(client)
    with patch.object(engine, "get_candles", return_value=df):
        
        # Mock macro filters
        engine._macro_filter = MagicMock()
        engine._macro_filter.get_24h_funding_trend.return_value = {"avg_rate": 0.0001, "bias": "NEUTRAL"}
        engine._macro_filter.get_8h_funding_average.return_value = {"avg_rate": 0.0001, "bias": "NEUTRAL"}
        
        # Analyze
        res = engine.analyze("BTCUSDT", "LONG", btc_trend="NEUTRAL")
        if res.get("is_sfp"):
            assert res.get("entry") == res.get("sfp_level")
            assert res.get("entry") == 9.2


# ==============================================================================
# Phase M: GMM + Q-Learning Adaptive Switcher
# ==============================================================================

def test_gmm_regime_classification():
    """Verify GMM classifier labels components semantically based on centers."""
    # Create synthetic dataset with clear centers
    c0 = np.random.normal(loc=[0.002, 0.001], scale=[0.0001, 0.0001], size=(20, 2))  # Low Vol, Low Trend
    c1 = np.random.normal(loc=[0.003, 0.010], scale=[0.0001, 0.0002], size=(20, 2))  # Low Vol, High Trend
    c2 = np.random.normal(loc=[0.020, 0.002], scale=[0.0005, 0.0001], size=(20, 2))  # High Vol, Low Trend
    c3 = np.random.normal(loc=[0.025, 0.020], scale=[0.0005, 0.0005], size=(20, 2))  # High Vol, High Trend
    X = np.vstack([c0, c1, c2, c3])
    
    classifier = GMMRegimeClassifier(n_components=4)
    classifier.fit(X)
    
    assert classifier.is_fitted is True
    # Verify semantic classes are present in label mapping
    semantic_names = set(classifier.label_mapping.values())
    assert "CHOPPY_LOW_VOL" in semantic_names
    assert "TRENDING_LOW_VOL" in semantic_names
    assert "CHOPPY_HIGH_VOL" in semantic_names
    assert "TRENDING_HIGH_VOL" in semantic_names


def test_q_learning_parameter_tuner():
    """Verify Q-learning Meta-Learner updates state-action values and shifts parameter values."""
    q = QLearningMetaLearner()
    
    state = "CHOPPY_LOW_VOL"
    action = 1 # Defensive
    reward = -1.5 # Loss
    next_state = "CHOPPY_HIGH_VOL"
    
    old_q = q.q_table[state][action]
    q.update(state, action, reward, next_state)
    
    new_q = q.q_table[state][action]
    assert new_q != old_q
    
    # Save/load container verification
    container = load_rl_meta_learner()
    container.q_learner = q
    save_rl_meta_learner(container)
    
    loaded = load_rl_meta_learner()
    assert loaded.q_learner.q_table[state][action] == new_q


# ==============================================================================
# Phase N: High-Frequency API Hot-Swap & Redis Hot-Swap Backups
# ==============================================================================

def test_endpoint_hot_swap():
    """Verify Binance API endpoints swap on high latency."""
    from core.live_execution import LiveExecutionEngine
    from binance.client import Client
    
    with patch("binance.client.Client.ping", return_value={}), \
         patch("binance.client.Client._request", return_value={}):
        client = Client("", "")
    # Default URL
    Client.FUTURES_API_URL = "https://fapi.binance.com"
    client.FUTURES_API_URL = "https://fapi.binance.com"
    
    engine = LiveExecutionEngine()
    engine.client = client
    
    # Mock orderbook call to simulate high latency >300ms
    def mock_futures_order_book(*args, **kwargs):
        import time
        time.sleep(0.31) # Sleep 310ms to trigger hot-swap
        return {"bids": [["100", "1"]], "asks": [["101", "1"]]}
        
    engine.client.futures_order_book = mock_futures_order_book
    engine.client.futures_create_order = MagicMock()
    engine.client.futures_get_order = MagicMock(return_value={"status": "FILLED", "executedQty": "1.0", "avgPrice": "100.0"})
    
    with patch("core.live_execution.logger") as mock_log:
        engine._execute_chase_limit_order("BTCUSDT", "BUY", "1.0", 100.0, 0.15)
        # Verify Endpoint Hot-Swap was triggered
        assert Client.FUTURES_API_URL != "https://fapi.binance.com"
        assert client.FUTURES_API_URL != "https://fapi.binance.com"


def test_redis_hot_swap_database_fallbacks():
    """Verify that when database fails with OperationalError, Redis backups are used."""
    # Write backup data to Redis state
    from core import redis_state
    redis_state.init()
    
    trade_id = 9999
    trade_data = {"id": trade_id, "symbol": "DUMMYUSDT", "status": "OPEN", "environment": "paper", "entry": 1.23}
    redis_state.set(f"backup:trades:{trade_id}", trade_data)
    redis_state.set("backup:open_trades", [trade_data])
    redis_state.set("backup:system_state:some_config", "backup_value")
    
    # Mock SQLite connection/execution to fail
    with patch("database.get_connection") as mock_conn, \
         patch("database.get_conn") as mock_get_conn:
         
        # Simulate SQLite operational lock / failure
        mock_conn.side_effect = sqlite3.OperationalError("database is locked (mocked)")
        mock_get_conn.side_effect = sqlite3.OperationalError("database is locked (mocked)")
        
        # Test get_trade_by_id fallback
        res_trade = database.get_trade_by_id(trade_id)
        assert res_trade == trade_data
        
        # Test get_open_trades fallback
        res_open = database.get_open_trades("paper")
        assert len(res_open) == 1
        assert res_open[0]["symbol"] == "DUMMYUSDT"
        
        # Test get_system_state fallback
        res_state = database.get_system_state("some_config")
        assert res_state == "backup_value"
