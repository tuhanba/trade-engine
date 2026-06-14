def test_db_idempotent_init(test_db):
    test_db.init_db()
    test_db.init_db()
    with test_db.get_conn() as conn:
        row = conn.execute("SELECT COUNT(*) FROM sqlite_master WHERE type='table' AND name='signal_candidates'").fetchone()
    assert row[0] == 1


def test_tenant_id_column_present(test_db):
    """Faz 6.5: SaaS temeli — trades ve daily_summary'de tenant_id (default 'main')."""
    with test_db.get_conn() as conn:
        for tbl in ("trades", "daily_summary"):
            cols = {r[1] for r in conn.execute(f"PRAGMA table_info({tbl})").fetchall()}
            assert "tenant_id" in cols, f"{tbl}: tenant_id eksik"
        # Yeni eklenen trade default 'main' tenant'ına düşer
        conn.execute("INSERT INTO trades (symbol, direction, status, environment) "
                     "VALUES ('BTCUSDT','LONG','closed','paper')")
        val = conn.execute("SELECT tenant_id FROM trades WHERE symbol='BTCUSDT'").fetchone()[0]
    assert val == "main"
