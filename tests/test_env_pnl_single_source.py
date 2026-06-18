"""
tests/test_env_pnl_single_source.py
===================================
Fix D REGRESYON TESTİ — Aktif ortam (paper/live) ve PnL/bakiye TEK kaynaktan.

Bug: ortam 3 ayrı yoldan (config.EXECUTION_MODE, get_state('tg_execution_mode'),
get_open_trades default) çözülüyordu; PnL ise Telegram'da bal-init (yalnız
realize), Dashboard'da total_pnl (unrealized dahil) — aynı anda farklı toplam
çıkıyordu.

Fix: database.current_environment() tek ortam kaynağı; get_open_trades /
get_dashboard_stats / get_active_balance_details + dashboard_service hepsi oradan
çözer. _cmd_balance PnL/bakiyeyi get_dashboard_stats'tan (tek PnL kaynağı) alır.
"""
import config
from telegram_manager import TelegramManager


def _insert_open(db, tid, symbol, env):
    with db.get_conn() as conn:
        conn.execute(
            "INSERT INTO trades (id, symbol, direction, status, entry, sl, net_pnl, "
            "is_valid_for_stats, environment, open_time) "
            "VALUES (?, ?, 'LONG', 'open', 100.0, 95.0, 0, 0, ?, '2026-06-01 00:00:00')",
            (tid, symbol, env),
        )


def _insert_closed(db, tid, symbol, env, pnl, close_time="2026-06-01 01:00:00"):
    with db.get_conn() as conn:
        conn.execute(
            "INSERT INTO trades (id, symbol, direction, status, entry, sl, net_pnl, "
            "is_valid_for_stats, environment, open_time, close_time) "
            "VALUES (?, ?, 'LONG', 'closed', 100.0, 95.0, ?, 1, ?, '2026-06-01 00:00:00', ?)",
            (tid, symbol, pnl, env, close_time),
        )


def test_current_environment_drives_get_open_trades(test_db):
    """get_open_trades() argümansız çağrısı current_environment()'i izlemeli."""
    _insert_open(test_db, 401, "BTCUSDT", "paper")
    _insert_open(test_db, 402, "ETHUSDT", "live")

    test_db.update_system_state("tg_execution_mode", "live")
    config._CONFIG_CACHE.pop("EXECUTION_MODE", None)
    assert test_db.current_environment() == "live"
    syms_default = {t["symbol"] for t in test_db.get_open_trades()}
    syms_live = {t["symbol"] for t in test_db.get_open_trades("live")}
    assert syms_default == syms_live == {"ETHUSDT"}

    test_db.update_system_state("tg_execution_mode", "paper")
    config._CONFIG_CACHE.pop("EXECUTION_MODE", None)
    assert test_db.current_environment() == "paper"
    assert {t["symbol"] for t in test_db.get_open_trades()} == {"BTCUSDT"}


def test_balance_text_matches_stats(test_db):
    """Telegram /balance metnindeki PnL & bakiye, Dashboard stats ile AYNI olmalı."""
    # paper: realize +500 ve -100 → total_pnl = 400 (açık trade yok → unrealized 0)
    _insert_closed(test_db, 411, "BTCUSDT", "paper", 500.0)
    _insert_closed(test_db, 412, "ETHUSDT", "paper", -100.0)
    test_db.update_system_state("tg_execution_mode", "paper")
    config._CONFIG_CACHE.pop("EXECUTION_MODE", None)
    test_db._stats_cache.clear()

    stats = test_db.get_dashboard_stats("paper")

    sent = []
    tm = TelegramManager(send_fn=lambda text, **kw: sent.append(text))
    tm._cmd_balance(env="paper")

    assert len(sent) == 1
    text = sent[0]
    # Toplam K/Z aynı tek kaynaktan (get_total_pnl → get_dashboard_stats.total_pnl)
    assert f"{stats['total_pnl']:+.2f}" in text, (text, stats["total_pnl"])
    # Bakiye de aynı kaynaktan
    assert f"${stats['balance']:.2f}" in text
    # total_pnl realize + unrealized + partial = 400 (yalnız realize var)
    assert stats["total_pnl"] == 400.0
