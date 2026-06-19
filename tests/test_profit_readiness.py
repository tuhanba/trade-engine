"""P1-2 regression: profit_readiness gate (directive Section 10).

Pure compute() must fail CLOSED with no data, PASS only when every gate holds,
and flag the specific failing gate otherwise.
"""
from scripts.profit_readiness import compute


def _mk(r, risk=10.0, symbol="BTCUSDT", setup="MOMENTUM_EXPANSION_SCALP", session="LONDON"):
    return {"r_multiple": r, "net_pnl": r * risk, "risk_usd": risk,
            "symbol": symbol, "setup_type": setup, "session": session}


def _pass_set(n=300):
    coins = [f"C{i}USDT" for i in range(10)]
    setups = ["MOMENTUM_EXPANSION_SCALP", "EMA_VWAP_PULLBACK_SCALP",
              "LIQUIDITY_SWEEP_SFP_REVERSAL", "ORDERFLOW_IMBALANCE_SCALP",
              "FUNDING_SQUEEZE_MEAN_REVERSION", "NEWS_VOLATILITY_OPPORTUNITY"]
    sessions = ["ASIA", "LONDON", "NY", "OFF"]
    out = []
    for i in range(n):
        win = (i % 5) < 3                       # 60% win rate
        out.append(_mk(1.5 if win else -1.0, symbol=coins[i % 10],
                       setup=setups[i % 6], session=sessions[i % 4]))
    return out


def test_empty_is_not_ready():
    res = compute([])
    assert res["ready"] is False
    assert res["n_trades"] == 0


def test_full_edge_passes():
    res = compute(_pass_set(), base_balance=100_000)
    assert res["ready"] is True, res["summary"]
    assert res["metrics"]["expectancy_r"] > 0.10
    assert float(res["metrics"]["profit_factor"]) > 1.25


def test_below_min_trades_fails():
    res = compute(_pass_set(100), base_balance=100_000)
    assert res["ready"] is False
    assert "min_trades" in res["failed_gates"]


def test_negative_expectancy_fails():
    # 60% win but tiny wins, big losses -> negative expectancy
    trades = []
    for i in range(300):
        win = (i % 5) < 3
        trades.append(_mk(0.2 if win else -2.0, symbol=f"C{i%10}", session=["A", "B", "C", "D"][i % 4],
                          setup=["S1", "S2", "S3", "S4", "S5", "S6"][i % 6]))
    res = compute(trades, base_balance=100_000)
    assert res["ready"] is False
    assert "expectancy" in res["failed_gates"]


def test_coin_concentration_fails():
    # every winning trade on the same coin -> coin concentration = 100%
    res = compute(_pass_set(), base_balance=100_000)
    same = [dict(t, symbol="BTCUSDT") for t in _pass_set()]
    res2 = compute(same, base_balance=100_000)
    assert res2["ready"] is False
    assert "coin_concentration" in res2["failed_gates"]


def test_expectancy_formula():
    trades = [_mk(2.0)] * 6 + [_mk(-1.0)] * 4
    res = compute(trades, base_balance=100_000)
    # win_rate 0.6, avg_win 2.0, avg_loss -1.0 -> E = 0.6*2 - 0.4*1 = 0.8
    assert abs(res["metrics"]["expectancy_r"] - 0.8) < 1e-6
    assert res["metrics"]["win_rate"] == 0.6


def test_r_fallback_from_pnl_and_risk():
    # no r_multiple -> derived from net_pnl / risk_usd
    res = compute([{"net_pnl": 30.0, "risk_usd": 10.0, "symbol": "X"}] * 1, base_balance=100_000)
    assert res["metrics"]["avg_win_r"] == 3.0
