"""test_adaptive_engine.py — Adaptive stats ve öğrenme güvenlik sınırları."""
import pytest


def test_adaptive_stat_upsert_multiple(tmp_db):
    import database as db
    for is_win, pnl in [(True, 5.0), (False, -3.0), (True, 4.0), (True, 6.0)]:
        db.upsert_adaptive_stat("coin", "symbol", "BTCUSDT", is_win, pnl, pnl/2)

    stats = db.get_adaptive_stats("coin", "symbol", min_count=1)
    btc   = next((s for s in stats if s["dimension_value"] == "BTCUSDT"), None)
    assert btc is not None
    assert btc["total_count"] == 4
    assert btc["win_count"]   == 3
    assert btc["win_rate"] == pytest.approx(0.75)


def test_adaptive_min_count_filter(tmp_db):
    import database as db
    db.upsert_adaptive_stat("coin", "symbol", "NEWCOIN", True, 1.0, 0.5)

    # min_count=5 → bu coin çıkmamalı
    stats = db.get_adaptive_stats("coin", "symbol", min_count=5)
    assert not any(s["dimension_value"] == "NEWCOIN" for s in stats)

    # min_count=1 → çıkmalı
    stats2 = db.get_adaptive_stats("coin", "symbol", min_count=1)
    assert any(s["dimension_value"] == "NEWCOIN" for s in stats2)


def test_adaptive_hour_stats(tmp_db):
    import database as db
    # Saat 9 → 3 trade, 2 win
    for is_win, pnl in [(True, 5.0), (True, 4.0), (False, -3.0)]:
        db.upsert_adaptive_stat("hour", "hour", "9", is_win, pnl, pnl/2)

    stats = db.get_adaptive_stats("hour", "hour", min_count=1)
    hour9 = next((s for s in stats if s["dimension_value"] == "9"), None)
    assert hour9 is not None
    assert hour9["win_rate"] == pytest.approx(2/3, abs=0.01)


def test_adaptive_session_stats(tmp_db):
    import database as db
    for sess in ["ASIA", "LONDON", "NEW_YORK"]:
        for is_win, pnl in [(True, 5.0), (False, -3.0)]:
            db.upsert_adaptive_stat("session", "session", sess, is_win, pnl, pnl/2)

    stats = db.get_adaptive_stats("session", "session", min_count=1)
    sessions = {s["dimension_value"] for s in stats}
    assert "ASIA" in sessions
    assert "LONDON" in sessions


def test_adaptive_quality_stats(tmp_db):
    """Setup kalitesi bazlı öğrenme."""
    import database as db
    for quality in ["S", "A+", "A", "B"]:
        for is_win, pnl in [(True, 5.0), (True, 4.0), (False, -3.0)]:
            db.upsert_adaptive_stat("quality", "setup_quality", quality, is_win, pnl, pnl/2)

    stats = db.get_adaptive_stats("quality", "setup_quality", min_count=1)
    qualities = {s["dimension_value"] for s in stats}
    assert "S" in qualities
    assert "B" in qualities
