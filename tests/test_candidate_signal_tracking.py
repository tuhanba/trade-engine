def test_candidate_signal_saved(test_db):
    cid = test_db.save_candidate_signal({
        "signal_id": "sig-1",
        "symbol": "BTCUSDT",
        "direction": "LONG",
        "final_score": 71,
        "quality": "A+",
    })
    with test_db.get_conn() as conn:
        row = conn.execute("SELECT id, symbol, final_score FROM candidate_signals WHERE id=?", (cid,)).fetchone()
    assert row["symbol"] == "BTCUSDT"
    assert row["final_score"] == 71
