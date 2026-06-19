"""P1-3 regression: per-setup expectancy report (directive Section 11)."""
from scripts.setup_expectancy_report import compute_by_setup


def _t(setup, r, risk=10.0, symbol="BTCUSDT", close_reason="TP1"):
    return {"setup_type": setup, "r_multiple": r, "net_pnl": r * risk, "risk_usd": risk,
            "symbol": symbol, "close_reason": close_reason if r > 0 else "SL"}


def test_groups_by_setup():
    rep = compute_by_setup([_t("A", 1.0)] * 5 + [_t("B", -1.0)] * 5)
    assert set(rep) == {"A", "B"}
    assert rep["A"]["sample_size"] == 5


def test_needs_more_data_below_min():
    rep = compute_by_setup([_t("A", 1.0)] * 10)   # 10 < 30
    assert rep["A"]["recommendation"] == "NEEDS_MORE_DATA"


def test_disable_negative_expectancy():
    rep = compute_by_setup([_t("A", -1.0)] * 40)
    assert rep["A"]["expectancy_r"] < 0
    assert rep["A"]["recommendation"] == "DISABLE"


def test_enable_strong_edge():
    trades = []
    for i in range(120):
        win = (i % 20) < 13                       # 65% win
        trades.append(_t("MOMENTUM_EXPANSION_SCALP", 1.6 if win else -1.0))
    m = compute_by_setup(trades)["MOMENTUM_EXPANSION_SCALP"]
    assert m["expectancy_r"] > 0.15
    assert m["recommendation"] == "ENABLE"


def test_tp_and_stop_rates():
    rep = compute_by_setup([_t("A", 1.0)] * 6 + [_t("A", -1.0)] * 4)
    assert rep["A"]["tp_hit_rate"] == 0.6
    assert rep["A"]["stop_first_rate"] == 0.4


def test_unknown_setup_grouped():
    rep = compute_by_setup([{"r_multiple": 1.0, "net_pnl": 10, "risk_usd": 10, "symbol": "X"}])
    assert "UNKNOWN" in rep   # missing setup_type -> UNKNOWN bucket
