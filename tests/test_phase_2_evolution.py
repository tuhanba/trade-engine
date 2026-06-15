import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest
import sqlite3
import json
import config
from datetime import datetime, timezone, timedelta
from unittest.mock import MagicMock, patch

from core.coin_library import update_coin_stats, get_coin_score
from core.ai_decision_engine import classify_signal
from core.risk_engine import evaluate_signal_risk
from core.trend_engine import MLMarketRegimeClassifier
from core.data_layer import SignalData

class MockBinanceClient:
    def __init__(self, atr_pct=0.010, rel_vol=1.0, rsi=50.0, adx=20.0, trend_dir="NEUTRAL"):
        self.atr_pct = atr_pct
        self.rel_vol = rel_vol
        self.rsi = rsi
        self.adx = adx
        self.trend_dir = trend_dir

    def futures_klines(self, symbol, interval, limit):
        # Generate dummy candles to trigger specific indicator values
        rows = []
        price = 100.0
        # If trend_dir is BULLISH, generate candles with upward slope
        # If trend_dir is BEARISH, generate candles with downward slope
        for i in range(limit):
            slope = 0.0
            if self.trend_dir == "BULLISH":
                slope = i * 0.05
            elif self.trend_dir == "BEARISH":
                slope = -i * 0.05
                
            o = price + slope
            h = o + (price * self.atr_pct)
            l = o - (price * self.atr_pct)
            c = o + slope * 0.2
            
            # To make relative volume dynamic in features
            v = 1000.0
            if i == limit - 1:
                v = 1000.0 * self.rel_vol
                
            rows.append([0, str(o), str(h), str(l), str(c), str(v), 0, "0", 0, "0", "0", "0"])
        return rows

@pytest.fixture
def test_db_setup(tmp_path, monkeypatch):
    """Sets up a temporary SQLite database for testing Phase 2 evolution."""
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

def test_coin_reputation_calculation(test_db_setup):
    """Verifies that the Coin Reputation is calculated correctly based on the last 50 closed trades."""
    # 1. Test Elite Reputation (6 wins, 0 losses out of 6 trades)
    import database
    conn = sqlite3.connect(test_db_setup)
    now_str = datetime.now(timezone.utc).isoformat()
    for i in range(6):
        conn.execute(
            "INSERT INTO trades (symbol, direction, status, net_pnl, r_multiple, close_time) VALUES (?, ?, ?, ?, ?, ?)",
            ("BTCUSDT", "LONG", "closed", 10.0, 1.5, now_str)
        )
    conn.commit()
    
    update_coin_stats("BTCUSDT", "WIN", 10.0, 1.5, "LONG")
    
    coin_cfg = database.get_coin_config("BTCUSDT")
    assert coin_cfg.get("reputation") == "Elite"
    assert coin_cfg.get("wins") == 6
    assert coin_cfg.get("win_rate") == 1.0
    
    # 2. Test Trash Reputation (5 losses, 0 wins out of 5 trades)
    for i in range(5):
        conn.execute(
            "INSERT INTO trades (symbol, direction, status, net_pnl, r_multiple, close_time) VALUES (?, ?, ?, ?, ?, ?)",
            ("ETHUSDT", "LONG", "closed", -5.0, -1.0, now_str)
        )
    conn.commit()
    
    update_coin_stats("ETHUSDT", "LOSS", -5.0, -1.0, "LONG")
    
    coin_cfg = database.get_coin_config("ETHUSDT")
    assert coin_cfg.get("reputation") == "Trash"
    assert coin_cfg.get("win_rate") == 0.0

    # 3. Test Risky Reputation (4 losses, 2 wins out of 6 trades = 33.3% win rate)
    for i in range(4):
        conn.execute(
            "INSERT INTO trades (symbol, direction, status, net_pnl, r_multiple, close_time) VALUES (?, ?, ?, ?, ?, ?)",
            ("SOLUSDT", "LONG", "closed", -5.0, -1.0, now_str)
        )
    for i in range(2):
        conn.execute(
            "INSERT INTO trades (symbol, direction, status, net_pnl, r_multiple, close_time) VALUES (?, ?, ?, ?, ?, ?)",
            ("SOLUSDT", "LONG", "closed", 5.0, 1.0, now_str)
        )
    conn.commit()
    
    update_coin_stats("SOLUSDT", "LOSS", -5.0, -1.0, "LONG")
    
    coin_cfg = database.get_coin_config("SOLUSDT")
    assert coin_cfg.get("reputation") == "Risky"
    conn.close()

def test_coin_reputation_constraints_in_ai_decision_engine(test_db_setup, monkeypatch):
    """Verifies that coin reputation dynamically relaxes or restricts thresholds and risk scaling."""
    import database
    
    # 1. Elite coin: threshold -5, risk 1.25x
    database.save_coin_config("BTCUSDT", {"reputation": "Elite"})
    
    # 2. Risky coin: threshold +5, risk 0.5x
    database.save_coin_config("SOLUSDT", {"reputation": "Risky"})
    
    # 3. Trash coin: Vetoed
    database.save_coin_config("ETHUSDT", {"reputation": "Trash"})
    
    # Configure mock environment and clean caches
    monkeypatch.setattr(config, "EXECUTION_MODE", "live")
    monkeypatch.setattr(config, "BYPASS_LIVE_RISK_SHIELDS", False)
    if "TRADE_THRESHOLD" in config._CONFIG_CACHE:
        del config._CONFIG_CACHE["TRADE_THRESHOLD"]
    database.set_state("trade_threshold", "65.0")
    
    # Test Trash Coin Veto
    sig_trash = SignalData(symbol="ETHUSDT", side="LONG", entry_price=100.0, stop_loss=98.0, tp1=103.0)
    sig_trash.score = 60.0
    sig_trash.risk_pct = 1.0
    res_trash = classify_signal(sig_trash, {"market_regime": "NEUTRAL"})
    assert res_trash.decision == "VETO"
    assert "Trash" in res_trash.reason

    # Test Risky Coin Threshold Penalty (+5) and Risk Scaling (0.5x)
    sig_risky = SignalData(symbol="SOLUSDT", side="LONG", entry_price=100.0, stop_loss=98.0, tp1=103.0)
    sig_risky.score = 58.0
    sig_risky.risk_pct = 1.0
    res_risky = classify_signal(sig_risky, {"market_regime": "NEUTRAL"})
    # Risky penalty: effective threshold becomes 65.0 + 5.0 = 70.0. Score is 58.0, so it should be WATCH/VETO
    assert res_risky.decision in ("WATCH", "VETO")
    assert sig_risky.risk_pct == 0.5  # Risk scaled by 0.5x
    
    # Test Elite Coin Threshold Bonus (-5) and Risk Scaling (1.25x)
    sig_elite = SignalData(symbol="BTCUSDT", side="LONG", entry_price=100.0, stop_loss=98.0, tp1=103.0)
    sig_elite.score = 51.0  # Base threshold is 65.0, Elite threshold is 60.0. Score 51.0 should be ALLOWED!
    sig_elite.risk_pct = 1.0
    res_elite = classify_signal(sig_elite, {"market_regime": "NEUTRAL"})
    assert res_elite.decision == "ALLOW"
    assert sig_elite.risk_pct == 1.25  # Risk scaled by 1.25x

def test_coin_reputation_veto_in_risk_engine(test_db_setup, monkeypatch):
    """Verifies that the Risk Engine vetos signals for Trash coins."""
    import database
    database.save_coin_config("ETHUSDT", {"reputation": "Trash"})
    
    monkeypatch.setattr(config, "EXECUTION_MODE", "live")
    monkeypatch.setattr(config, "BYPASS_LIVE_RISK_SHIELDS", False)
    
    sig = SignalData(symbol="ETHUSDT", side="LONG", entry_price=100.0, stop_loss=98.0, tp1=103.0)
    sig.direction = "LONG"
    sig.risk_pct = 1.0
    sig.leverage = 10
    sig.tp1 = 103.0
    
    res = evaluate_signal_risk(sig, [], 2000.0)
    assert res["decision"] == "VETO"
    assert "Trash" in res["reason"]

def test_market_regime_classification(test_db_setup, monkeypatch):
    """Verifies that the MLMarketRegimeClassifier correctly identifies the 7 granular market regimes."""
    import pandas as pd
    def mock_get_regime_features(self, symbol="BTCUSDT", limit=100):
        return pd.DataFrame([{
            "atr_pct": self.client.atr_pct,
            "rel_vol": self.client.rel_vol,
            "rsi": self.client.rsi,
            "adx": self.client.adx
        }])
    monkeypatch.setattr(MLMarketRegimeClassifier, "get_regime_features", mock_get_regime_features)

    from core.trend_engine import _GLOBAL_KLINE_CACHE
    
    # Test BULLISH
    _GLOBAL_KLINE_CACHE.clear()
    client_bull = MockBinanceClient(atr_pct=0.010, rel_vol=1.0, rsi=60.0, adx=25.0, trend_dir="BULLISH")
    classifier_bull = MLMarketRegimeClassifier(client_bull)
    assert classifier_bull.classify("BTCUSDT") == "BULLISH"
    
    # Test BEARISH
    _GLOBAL_KLINE_CACHE.clear()
    client_bear = MockBinanceClient(atr_pct=0.010, rel_vol=1.0, rsi=40.0, adx=25.0, trend_dir="BEARISH")
    classifier_bear = MLMarketRegimeClassifier(client_bear)
    assert classifier_bear.classify("BTCUSDT") == "BEARISH"
    
    # Test NEWS_DRIVEN (rel_vol > 2.2)
    _GLOBAL_KLINE_CACHE.clear()
    client_news = MockBinanceClient(atr_pct=0.010, rel_vol=2.5, rsi=50.0, adx=20.0, trend_dir="NEUTRAL")
    classifier_news = MLMarketRegimeClassifier(client_news)
    assert classifier_news.classify("BTCUSDT") == "NEWS_DRIVEN"
    
    # Test HIGH_MOMENTUM (adx > 32 and rel_vol > 1.3)
    _GLOBAL_KLINE_CACHE.clear()
    client_mom = MockBinanceClient(atr_pct=0.010, rel_vol=1.5, rsi=50.0, adx=35.0, trend_dir="BULLISH")
    classifier_mom = MLMarketRegimeClassifier(client_mom)
    assert classifier_mom.classify("BTCUSDT") == "HIGH_MOMENTUM"
    
    # Test HIGH_VOLATILITY (atr_pct > 0.015)
    _GLOBAL_KLINE_CACHE.clear()
    client_vol = MockBinanceClient(atr_pct=0.016, rel_vol=1.0, rsi=50.0, adx=20.0, trend_dir="NEUTRAL")
    classifier_vol = MLMarketRegimeClassifier(client_vol)
    assert classifier_vol.classify("BTCUSDT") == "HIGH_VOLATILITY"

def test_dynamic_regime_scaling(test_db_setup, monkeypatch):
    """Verifies config.py scales parameters dynamically according to the 7 market regimes."""
    import database
    from core import redis_state
    
    # 1. Test BULLISH regime parameter modifications
    database.update_system_state("market_regime", "BULLISH")
    try:
        redis_state.set("market_regime", "BULLISH")
    except Exception:
        pass
        
    # Clean cache to force reload
    for key in ["RISK_PCT", "TRADE_THRESHOLD"]:
        if key in config._CONFIG_CACHE:
            del config._CONFIG_CACHE[key]
            
    database.set_state("risk_pct", "1.0")
    database.set_state("trade_threshold", "55.0")
    
    assert config.RISK_PCT == 1.3  # BULLISH is 1.3x risk
    assert config.TRADE_THRESHOLD == 51.0  # BULLISH is -4 threshold

    # 2. Test NEWS_DRIVEN regime modifications
    database.update_system_state("market_regime", "NEWS_DRIVEN")
    try:
        redis_state.set("market_regime", "NEWS_DRIVEN")
    except Exception:
        pass
        
    for key in ["RISK_PCT", "TRADE_THRESHOLD"]:
        if key in config._CONFIG_CACHE:
            del config._CONFIG_CACHE[key]
            
    assert config.RISK_PCT == 0.4  # NEWS_DRIVEN is 0.4x risk
    assert config.TRADE_THRESHOLD == 60.0  # NEWS_DRIVEN is +5 threshold

def test_trade_starvation_alarm(test_db_setup, monkeypatch):
    """Verifies starvation alarm triggers and relaxes threshold/quality gate dynamically."""
    import database
    from core.friday_ceo import FridayCeo
    from core import redis_state
    
    database.update_system_state("market_regime", "NEUTRAL")
    try:
        redis_state.set("market_regime", "NEUTRAL")
    except Exception:
        pass
        
    # Clean caches
    for key in ["TRADE_THRESHOLD", "REGIME_FILTER_MIN_QUALITY_IN_CHOPPY"]:
        if key in config._CONFIG_CACHE:
            del config._CONFIG_CACHE[key]
            
    database.set_state("trade_threshold", "55.0")
    database.set_state("regime_filter_min_quality_in_choppy", "A+")
    database.set_state("trade_starvation_alarm", "false")
    
    # Populate database with 9 signal candidates and 0 trades in the last 24h
    conn = sqlite3.connect(test_db_setup)
    now_str = datetime.now(timezone.utc).isoformat()
    for i in range(9):
        conn.execute(
            "INSERT INTO signal_candidates (symbol, side, created_at, status) VALUES (?, ?, ?, ?)",
            ("BTCUSDT", "LONG", now_str, "NEW")
        )
    conn.commit()
    conn.close()
    
    # Trigger sysadmin checks using FridayCeo
    ceo = FridayCeo(db_path=test_db_setup)
    with patch("telegram_delivery.send_message", return_value=True):
        ceo._run_sysadmin_checks()
        
    # Verify starvation alarm is active in database
    assert database.get_system_state("trade_starvation_alarm") == "true"
    
    # Verify configuration parameters are dynamically relaxed
    # Clean config caches again to make sure they pick up the starvation status
    for key in ["TRADE_THRESHOLD", "REGIME_FILTER_MIN_QUALITY_IN_CHOPPY"]:
        if key in config._CONFIG_CACHE:
            del config._CONFIG_CACHE[key]
            
    assert config.TRADE_THRESHOLD == 50.0  # Base 55.0 - 5.0 starvation relaxation
    assert config.REGIME_FILTER_MIN_QUALITY_IN_CHOPPY == "B"  # Relaxed from A+ to B
