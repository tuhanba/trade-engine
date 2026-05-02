def test_paper_result_saved(test_db):
    test_db.save_paper_result({
        "signal_id": "sig-paper",
        "candidate_id": "cand-paper",
        "symbol": "SOLUSDT",
        "direction": "LONG",
        "tracked_from": "watchlist",
        "would_have_won": 1,
        "preview_entry": 100.0,
        "preview_sl": 99.0,
        "preview_tp1": 101.0,
        "preview_tp2": 102.0,
        "preview_tp3": 103.0,
        "status": "pending",
    })
    with test_db.get_conn() as conn:
        row = conn.execute(
            """SELECT would_have_won, preview_entry, preview_sl FROM paper_results
               WHERE signal_id='sig-paper'"""
        ).fetchone()
    assert row["would_have_won"] == 1
    assert row["preview_entry"] == 100.0
    assert row["preview_sl"] == 99.0


def test_paper_resolve_bar_intrabar_priority():
    from core import paper_tracker

    assert paper_tracker._resolve_bar("LONG", high=105, low=97, sl=98, tp1=104) == "stop"
    assert paper_tracker._resolve_bar("LONG", high=103, low=99.5, sl=98, tp1=101) == "tp1"
    assert paper_tracker._resolve_bar("SHORT", high=206, low=192, sl=205, tp1=198) == "stop"
