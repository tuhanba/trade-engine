"""test_paper_tracking.py — Paper sim fee/slippage ve result tracking."""
import pytest
from datetime import datetime, timezone, timedelta


def _now():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S")


def _hour_ago():
    return (datetime.now(timezone.utc) - timedelta(hours=1)).strftime("%Y-%m-%dT%H:%M:%S")


def test_paper_result_save_and_close(tmp_db):
    import database as db
    rid = db.save_paper_result({
        "symbol": "BTCUSDT", "direction": "LONG",
        "entry": 30000.0, "stop": 29500.0,
        "tp1": 30500.0, "tp2": 31000.0,
        "environment": "paper",
        "open_time": _now(),
        "fee_paid": 0.48,
        "is_candidate_track": 0,
    })
    assert rid > 0

    open_res = db.get_open_paper_results()
    assert any(r["id"] == rid for r in open_res)

    db.update_paper_result(rid, {
        "close_time":   _now(),
        "close_price":  31000.0,
        "close_reason": "tp2",
        "gross_pnl":    5.0,
        "fee_paid":     0.96,
        "net_pnl":      4.04,
        "r_multiple":   1.9,
        "tp2_hit":      1,
    })

    open_res2 = db.get_open_paper_results()
    assert not any(r["id"] == rid for r in open_res2)


def test_paper_stats_aggregation(tmp_db):
    import database as db
    for i in range(5):
        rid = db.save_paper_result({
            "symbol": "ETHUSDT", "direction": "LONG",
            "entry": 2000.0, "stop": 1950.0,
            "environment": "paper",
            "open_time": _now(),
        })
        pnl = 5.0 if i < 3 else -3.0
        db.update_paper_result(rid, {
            "close_time": _now(),
            "net_pnl":    pnl,
            "r_multiple": pnl / 2,
            "close_reason": "tp2" if pnl > 0 else "sl",
        })

    stats = db.get_paper_stats(days=1)
    assert stats["total"] == 5
    assert stats["wins"] == 3
    assert stats["win_rate"] == pytest.approx(3/5)


def test_fee_deducted_from_paper_pnl(tmp_db):
    """Net PnL = Gross PnL - Fee."""
    import database as db
    rid = db.save_paper_result({
        "symbol": "SOLUSDT", "direction": "LONG",
        "entry": 100.0, "stop": 97.0,
        "environment": "paper",
        "open_time": _now(),
    })

    gross  = 3.0
    fee    = 0.08
    net    = gross - fee
    db.update_paper_result(rid, {
        "close_time": _now(),
        "gross_pnl": gross,
        "fee_paid":  fee,
        "net_pnl":   net,
        "close_reason": "tp1",
    })

    stats = db.get_paper_stats(days=1)
    assert abs(stats["total_pnl"] - net) < 0.001


def test_candidate_track_flag(tmp_db):
    """Watchlist sinyaller is_candidate_track=1 ile kaydedilmeli."""
    import database as db
    rid = db.save_paper_result({
        "symbol": "BTCUSDT", "direction": "LONG",
        "entry": 30000.0, "stop": 29000.0,
        "environment": "paper",
        "open_time": _now(),
        "is_candidate_track": 1,
    })

    with db.get_conn() as conn:
        row = conn.execute(
            "SELECT is_candidate_track FROM paper_results WHERE id=?", (rid,)
        ).fetchone()
    assert row["is_candidate_track"] == 1
