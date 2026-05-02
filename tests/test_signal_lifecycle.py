def test_signal_lifecycle_events_written(test_db):
    test_db.save_signal_event("sig-123", "SCANNED", symbol="ETHUSDT")
    test_db.save_signal_event("sig-123", "TREND_CHECKED", symbol="ETHUSDT")
    test_db.save_signal_event("sig-123", "REJECTED", symbol="ETHUSDT", reject_reason="weak_trend")
    with test_db.get_conn() as conn:
        rows = conn.execute("SELECT stage, reject_reason FROM signal_events WHERE signal_id='sig-123'").fetchall()
    assert len(rows) == 3
    assert rows[-1]["reject_reason"] == "weak_trend"
