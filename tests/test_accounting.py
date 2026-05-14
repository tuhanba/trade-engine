"""
tests/test_accounting.py – Accounting PnL testleri.
"""

import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.accounting import (
    calculate_position_size,
    calculate_notional,
    calculate_margin_used,
    calculate_fee,
    calculate_unrealized_pnl,
    calculate_realized_pnl,
    calculate_rr,
    validate_risk,
    build_trade_from_signal,
)
from core.data_layer import SignalData


def test_position_size():
    qty = calculate_position_size(1000, 1.0, 100.0, 95.0)
    assert qty > 0
    # risk = 10 USD, stop_dist = 5, qty = 2.0
    assert abs(qty - 2.0) < 0.01


def test_notional():
    n = calculate_notional(2.0, 100.0)
    assert abs(n - 200.0) < 0.01


def test_margin():
    m = calculate_margin_used(200.0, 10)
    assert abs(m - 20.0) < 0.01


def test_fee():
    f = calculate_fee(200.0, 0.0004)
    assert abs(f - 0.08) < 0.001


def test_pnl_long():
    pnl = calculate_realized_pnl("LONG", 100.0, 110.0, 2.0)
    assert pnl == 20.0


def test_pnl_short():
    pnl = calculate_realized_pnl("SHORT", 100.0, 90.0, 2.0)
    assert pnl == 20.0


def test_pnl_long_loss():
    pnl = calculate_realized_pnl("LONG", 100.0, 95.0, 2.0)
    assert pnl == -10.0


def test_pnl_short_loss():
    pnl = calculate_realized_pnl("SHORT", 100.0, 105.0, 2.0)
    assert pnl == -10.0


def test_pnl_with_fee():
    pnl = calculate_realized_pnl("LONG", 100.0, 110.0, 2.0, 0.0004)
    # raw=20, fee=(200+220)*0.0004=0.168
    assert pnl < 20.0
    assert pnl > 19.0


def test_unrealized_long():
    upnl = calculate_unrealized_pnl("LONG", 100.0, 105.0, 2.0)
    assert upnl == 10.0


def test_unrealized_short():
    upnl = calculate_unrealized_pnl("SHORT", 100.0, 95.0, 2.0)
    assert upnl == 10.0


def test_rr():
    rr = calculate_rr(100.0, 95.0, 110.0)
    assert rr == 2.0


def test_validate_risk_ok():
    sig = SignalData(
        symbol="BTCUSDT", side="LONG",
        entry_price=100.0, stop_loss=95.0, tp1=110.0,
        leverage=5, risk_pct=1.0,
    )
    valid, reason = validate_risk(sig, 1000.0)
    assert valid is True


def test_validate_risk_bad_sl():
    sig = SignalData(
        symbol="BTCUSDT", side="LONG",
        entry_price=100.0, stop_loss=100.0, tp1=110.0,
    )
    valid, reason = validate_risk(sig, 1000.0)
    assert valid is False


def test_build_trade():
    sig = SignalData(
        symbol="ETHUSDT", side="LONG",
        entry_price=2000.0, stop_loss=1950.0,
        tp1=2100.0, tp2=2200.0, tp3=2300.0,
        leverage=10, risk_pct=1.0,
    )
    trade = build_trade_from_signal(sig, 1000.0)
    assert trade is not None
    assert trade.symbol == "ETHUSDT"
    assert trade.quantity > 0
    assert trade.notional > 0
    assert trade.margin_used > 0


if __name__ == "__main__":
    tests = [v for k, v in globals().items() if k.startswith("test_")]
    passed = 0
    failed = 0
    for t in tests:
        try:
            t()
            print(f"  ✓ {t.__name__}")
            passed += 1
        except Exception as exc:
            print(f"  ✗ {t.__name__}: {exc}")
            failed += 1
    print(f"\n{passed} passed, {failed} failed")
    sys.exit(1 if failed else 0)
