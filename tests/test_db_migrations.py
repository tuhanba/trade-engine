def test_db_idempotent_init(test_db):
    test_db.init_db()
    test_db.init_db()
    with test_db.get_conn() as conn:
        row = conn.execute("SELECT COUNT(*) FROM sqlite_master WHERE type='table' AND name='candidate_signals'").fetchone()
    assert row[0] == 1
