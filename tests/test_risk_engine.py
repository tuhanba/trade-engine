"""test_risk_engine.py — Risk engine hesap doğruluğu."""
import pytest
import pandas as pd
import numpy as np


def _make_df(n=50, price=30000.0, atr_range=200.0):
    prices = price + np.cumsum(np.random.randn(n) * atr_range / 10)
    return pd.DataFrame({
        "time":   range(n),
        "open":   prices - 50,
        "high":   prices + atr_range / 4,
        "low":    prices - atr_range / 4,
        "close":  prices,
        "volume": np.abs(np.random.randn(n)) * 1000 + 500,
    })


def test_risk_calculate_long(mock_client):
    from core.risk_engine import RiskEngine
    df = _make_df(50, 30000.0)
    mock_client.futures_klines.return_value = [
        [r["time"], r["open"], r["high"], r["low"], r["close"], r["volume"],
         0, 0, 0, 0, 0, 0]
        for _, r in df.iterrows()
    ]
    re = RiskEngine(mock_client)
    re.base_risk_pct = 1.0
    result = re.calculate("BTCUSDT", "LONG", 30000.0, "A", balance=1000.0)

    assert result["valid"] or result.get("risk_reject_reason")
    if result["valid"]:
        assert result["sl"] < 30000.0
        assert result["tp1"] > 30000.0
        assert result["tp2"] > result["tp1"]
        assert result["rr"] > 0
        assert result["net_rr"] <= result["rr"]  # fee düşülmüş
        assert result["estimated_fee"] > 0
        assert result["position_size"] >= 0


def test_risk_calculate_short(mock_client):
    from core.risk_engine import RiskEngine
    df = _make_df(50, 2000.0, 20.0)
    mock_client.futures_klines.return_value = [
        [r["time"], r["open"], r["high"], r["low"], r["close"], r["volume"],
         0, 0, 0, 0, 0, 0]
        for _, r in df.iterrows()
    ]
    re = RiskEngine(mock_client)
    result = re.calculate("ETHUSDT", "SHORT", 2000.0, "A+", balance=500.0)

    if result["valid"]:
        assert result["sl"] > 2000.0
        assert result["tp1"] < 2000.0
        assert result["tp2"] < result["tp1"]


def test_risk_quality_scaling(mock_client):
    """S kalite daha büyük pozisyon alır."""
    from core.risk_engine import RiskEngine
    df = _make_df(50)
    klines = [[r["time"], r["open"], r["high"], r["low"], r["close"], r["volume"],
               0, 0, 0, 0, 0, 0] for _, r in df.iterrows()]
    mock_client.futures_klines.return_value = klines

    re = RiskEngine(mock_client)
    r_s  = re.calculate("BTCUSDT", "LONG", 30000.0, "S",  1000.0)
    r_b  = re.calculate("BTCUSDT", "LONG", 30000.0, "B",  1000.0)
    if r_s["valid"] and r_b["valid"]:
        assert r_s["risk_pct"] > r_b["risk_pct"]


def test_risk_invalid_on_zero_entry(mock_client):
    from core.risk_engine import RiskEngine
    re = RiskEngine(mock_client)
    result = re.calculate("BTCUSDT", "LONG", 0.0, "A", 1000.0)
    assert not result["valid"]


def test_risk_invalid_on_empty_candles(mock_client):
    from core.risk_engine import RiskEngine
    mock_client.futures_klines.return_value = []
    re = RiskEngine(mock_client)
    result = re.calculate("BTCUSDT", "LONG", 30000.0, "A", 1000.0)
    assert not result["valid"]
    assert result["risk_reject_reason"] in ("no_candle_data", "invalid_atr")


def test_tick_rounding():
    from core.risk_engine import _round_to_tick, _round_to_step
    assert _round_to_tick(30001.7, 0.1) == pytest.approx(30001.7)
    assert _round_to_step(0.0037, 0.001) == pytest.approx(0.003)  # floor
