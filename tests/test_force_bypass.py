"""
tests/test_force_bypass.py – Manual Trade Force Bypass unit tests.
"""

import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest
import config
from core.data_layer import SignalData, TradeStatus
from core.accounting import validate_risk, build_trade_from_signal
from execution_engine import ExecutionEngine


def test_validate_risk_forced():
    # Regular signal violating leverage limit
    sig = SignalData(
        symbol="BTCUSDT", side="LONG",
        entry_price=100.0, stop_loss=95.0, tp1=110.0,
        leverage=50, risk_pct=10.0, # leverage > 20, risk > 5.0
    )
    valid, reason = validate_risk(sig, 1000.0, max_leverage=20, max_risk_pct=5.0)
    assert valid is False

    # Forced signal violating leverage/risk limits
    forced_sig = SignalData(
        symbol="BTCUSDT", side="LONG",
        entry_price=100.0, stop_loss=95.0, tp1=110.0,
        leverage=50, risk_pct=10.0,
    )
    forced_sig.source = "telegram_force_cmd"
    valid, reason = validate_risk(forced_sig, 1000.0, max_leverage=20, max_risk_pct=5.0)
    assert valid is True
    assert reason == "OK"

    # Forced signal with invalid stop distance should still fail
    bad_forced_sig = SignalData(
        symbol="BTCUSDT", side="LONG",
        entry_price=100.0, stop_loss=100.0,
    )
    bad_forced_sig.source = "telegram_force_cmd"
    valid, reason = validate_risk(bad_forced_sig, 1000.0)
    assert valid is False


def test_build_trade_from_signal_forced(test_db):
    # Forced signal should bypass Kelly scaling and use base risk directly
    sig = SignalData(
        symbol="ETHUSDT", side="LONG",
        entry_price=2000.0, stop_loss=1950.0,
        tp1=2100.0, tp2=2200.0, tp3=2300.0,
        leverage=25, risk_pct=3.0,
    )
    sig.source = "telegram_force"
    sig.final_score = 45.0 # Low score would normally scale risk by 0.6x

    trade = build_trade_from_signal(sig, 1000.0, max_leverage=20)
    assert trade is not None
    assert trade.risk_pct == 3.0 # Kelly downscaling bypassed!
    assert trade.leverage == 25 # Leverage clamp bypassed!

    # Forced signal with insufficient balance should scale down to fit available margin
    sig_large = SignalData(
        symbol="ETHUSDT", side="LONG",
        entry_price=2000.0, stop_loss=1900.0,
        leverage=1, risk_pct=50.0, # risk USD = 500, margin req = 10000 (exceeds balance 1000)
    )
    sig_large.source = "telegram_force"
    
    trade_large = build_trade_from_signal(sig_large, 1000.0)
    assert trade_large is not None
    # Margin used should be scaled down to fit within the available balance
    assert trade_large.margin_used <= 1000.0


def test_open_paper_trade_forced_bypasses(test_db):
    # Setup execution engine
    engine = ExecutionEngine()
    
    # Mock database and market data calls to force staleness, correlation, etc.
    original_get_price_age = None
    
    try:
        import core.market_data
        original_get_price_age = core.market_data.get_price_age
        # Force stale price (>120s limit)
        core.market_data.get_price_age = lambda symbol: 500.0
    except Exception:
        pass
        
    # We populate the DB with an open trade to trigger correlation blocker
    # First create a trade in DB
    sig_existing = SignalData(
        symbol="BTCUSDT", side="LONG",
        entry_price=100.0, stop_loss=95.0, tp1=110.0,
        leverage=10, risk_pct=1.0,
    )
    sig_existing.source = "telegram_force" # bypass validation to insert
    trade_id_existing = engine.open_paper_trade(sig_existing)
    assert trade_id_existing is not None
    
    # Now we try to open another BTCUSDT trade (100% correlation)
    sig_new = SignalData(
        symbol="BTCUSDT", side="LONG",
        entry_price=100.0, stop_loss=95.0, tp1=110.0,
        leverage=10, risk_pct=1.0,
    )
    
    # 1. Stale price / correlation should cause regular signal to fail
    trade_id = engine.open_paper_trade(sig_new)
    assert trade_id is None
    
    # 2. Forced signal should bypass price staleness, correlation, and open successfully
    sig_new.source = "telegram_force_cmd"
    trade_id_forced = engine.open_paper_trade(sig_new)
    assert trade_id_forced is not None
    
    # Clean up mocks
    if original_get_price_age is not None:
        try:
            import core.market_data
            core.market_data.get_price_age = original_get_price_age
        except Exception:
            pass


if __name__ == "__main__":
    pytest.main(["-v", __file__])
