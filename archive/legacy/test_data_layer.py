def test_new_tables_exist(test_db):
    with test_db.get_conn() as conn:
        names = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    required = {
        "market_snapshots", "scanned_coins", "candidate_signals", "signal_events",
        "trades", "trade_events", "paper_results", "coin_profile", "adaptive_stats",
        "telegram_messages", "system_state", "backtest_runs",
    }
    assert required.issubset(names)
