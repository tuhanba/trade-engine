def test_paper_result_saved(test_db):
    test_db.save_paper_result({
        "signal_id": "sig-paper",
        "candidate_id": "cand-paper",
        "symbol": "SOLUSDT",
        "direction": "LONG",
        "tracked_from": "watchlist",
        "would_have_won": 1,
    })
    with test_db.get_conn() as conn:
        row = conn.execute("SELECT would_have_won FROM paper_results WHERE signal_id='sig-paper'").fetchone()
    assert row["would_have_won"] == 1
