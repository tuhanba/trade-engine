"""test_db_migrations.py — DB şema ve migration testleri."""
import sqlite3, pytest


def test_init_db_creates_all_tables(tmp_db):
    conn = sqlite3.connect(tmp_db)
    conn.row_factory = sqlite3.Row
    tables = {r["name"] for r in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table'"
    ).fetchall()}
    conn.close()

    required = {
        "trades", "signal_candidates", "paper_account",
        "system_state", "scalp_signals", "signal_events",
        "trade_events", "telegram_messages",
        "scanned_coins", "paper_results", "adaptive_stats",
        "coin_profile", "coin_cooldown", "daily_summary",
        "weekly_summary", "pattern_memory",
    }
    missing = required - tables
    assert not missing, f"Eksik tablolar: {missing}"


def test_signal_events_lifecycle(tmp_db):
    import database as db
    sig_id = "test-sig-001"
    db.log_signal_event(sig_id, "SCANNED", "initial")
    db.log_signal_event(sig_id, "TREND_CHECKED", "ok")
    db.log_signal_event(sig_id, "REJECTED", "weak_trend")

    events = db.get_signal_lifecycle(sig_id)
    assert len(events) == 3
    assert events[0]["stage"] == "SCANNED"
    assert events[2]["stage"] == "REJECTED"


def test_trade_events(tmp_db):
    import database as db
    trade = {
        "symbol": "BTCUSDT", "direction": "LONG", "entry": 30000,
        "sl": 29000, "tp1": 31000, "tp2": 32000, "qty": 0.01,
    }
    tid = db.save_trade(trade)
    db.log_trade_event(tid, "OPENED",  price=30000)
    db.log_trade_event(tid, "TP1_HIT", price=31000, pnl=10.0)
    db.log_trade_event(tid, "CLOSED",  price=32000, pnl=20.0)

    events = db.get_trade_events(tid)
    assert len(events) == 3
    assert events[1]["event"] == "TP1_HIT"


def test_telegram_dedup(tmp_db):
    import database as db
    db.mark_telegram_sent("sig-001", "BTCUSDT", "LONG", "A+")
    assert db.is_telegram_sent("sig-001")
    assert not db.is_telegram_sent("sig-999")
    # İkinci kez yazma sessizce ignore edilmeli
    db.mark_telegram_sent("sig-001", "BTCUSDT", "LONG", "A+")
    assert db.is_telegram_sent("sig-001")


def test_scanned_coins_save_and_query(tmp_db):
    import database as db
    db.save_scanned_coin({
        "symbol": "ETHUSDT", "price": 2000, "volume_24h": 50_000_000,
        "price_change_24h": 2.5, "tradeability_score": 7.5,
        "scanner_status": "ELIGIBLE", "scanner_reason": "ok",
        "volume_score": 8, "volatility_score": 7, "spread_score": 9,
    })
    coins = db.get_scanned_coins_today(limit=10)
    assert len(coins) >= 1
    assert coins[0]["symbol"] == "ETHUSDT"


def test_paper_result_lifecycle(tmp_db):
    import database as db
    rid = db.save_paper_result({
        "symbol": "SOLUSDT", "direction": "LONG",
        "entry": 100, "stop": 95, "tp1": 105, "tp2": 110,
        "environment": "paper", "open_time": "2026-01-01T00:00:00",
    })
    assert rid > 0
    open_res = db.get_open_paper_results()
    assert any(r["id"] == rid for r in open_res)

    db.update_paper_result(rid, {
        "close_time": "2026-01-01T01:00:00",
        "close_price": 110, "net_pnl": 4.5, "close_reason": "tp2",
    })
    open_res2 = db.get_open_paper_results()
    assert not any(r["id"] == rid for r in open_res2)


def test_adaptive_stats_upsert(tmp_db):
    import database as db
    db.upsert_adaptive_stat("coin", "symbol", "BTCUSDT", True, 5.0, 1.5)
    db.upsert_adaptive_stat("coin", "symbol", "BTCUSDT", False, -3.0, -1.0)
    db.upsert_adaptive_stat("coin", "symbol", "BTCUSDT", True, 4.0, 1.2)

    stats = db.get_adaptive_stats("coin", "symbol", min_count=1)
    btc   = next((s for s in stats if s["dimension_value"] == "BTCUSDT"), None)
    assert btc is not None
    assert btc["total_count"] == 3
    assert btc["win_count"] == 2
    assert btc["win_rate"] == pytest.approx(2/3, abs=0.01)


def test_migration_idempotent(tmp_db):
    """init_db() birden fazla çağrılabilir, crash etmemeli."""
    import database as db
    db.init_db()
    db.init_db()
    db.init_db()
    # Sadece crash etmediğini doğrula
    assert True
