"""test_candidate_signal_tracking.py — Aday sinyal ve veri toplama."""
import pytest


def test_candidate_saved_above_data_threshold(tmp_db):
    """DATA_THRESHOLD üstü sinyal DB'ye candidate olarak yazılır."""
    import database as db
    from core.data_layer import DataLayer, DATA_THRESHOLD

    dl  = DataLayer()
    sig = dl.create_signal("ETHUSDT")
    sig.direction   = "LONG"
    sig.entry_zone  = 2000.0
    sig.stop_loss   = 1950.0
    sig.setup_quality = "A"
    sig.final_score = DATA_THRESHOLD + 1

    dl.persist(sig)

    candidates = db.get_candidate_signals(limit=10)
    assert any(c["id"] == sig.id for c in candidates)


def test_rejected_candidate_stored(tmp_db):
    """Reddedilen sinyal DB'de görünmeli (reject_reason ile)."""
    import database as db
    from core.data_layer import DataLayer, DATA_THRESHOLD

    dl  = DataLayer()
    sig = dl.create_signal("SOLUSDT")
    sig.direction   = "SHORT"
    sig.entry_zone  = 100.0
    sig.stop_loss   = 105.0
    sig.setup_quality = "B"
    sig.final_score = DATA_THRESHOLD + 2

    dl.reject(sig, "ai_veto", "low coin win_rate")

    with db.get_conn() as conn:
        row = conn.execute(
            "SELECT reject_reason, lifecycle_stage FROM scalp_signals WHERE id=?",
            (sig.id,)
        ).fetchone()
    assert row is not None
    assert row["reject_reason"] == "ai_veto"
    assert row["lifecycle_stage"] == "REJECTED"


def test_signal_funnel_counts_today(tmp_db):
    """Signal funnel doğru sayıları döndürmeli."""
    import database as db
    from core.data_layer import DataLayer, DATA_THRESHOLD

    dl = DataLayer()
    # 3 sinyal yaz: 1 approved, 2 rejected
    for i in range(3):
        sig = dl.create_signal("XUSDT")
        sig.direction   = "LONG"
        sig.entry_zone  = 1.0
        sig.stop_loss   = 0.9
        sig.setup_quality = "A"
        sig.final_score = DATA_THRESHOLD + 1
        if i == 0:
            dl.approve_watchlist(sig)
        else:
            dl.reject(sig, "weak_trend")

    funnel = db.get_signal_funnel_today()
    assert funnel.get("candidates", 0) >= 1 or funnel.get("APPROVED_FOR_WATCHLIST", 0) >= 1


def test_outcome_update_for_rejected(tmp_db):
    """Reddedilen sinyal için outcome güncellenebilir."""
    import database as db
    from core.data_layer import DataLayer, DATA_THRESHOLD

    dl  = DataLayer()
    sig = dl.create_signal("BNBUSDT")
    sig.direction   = "LONG"
    sig.entry_zone  = 300.0
    sig.stop_loss   = 290.0
    sig.setup_quality = "A"
    sig.final_score = DATA_THRESHOLD + 1
    dl.reject(sig, "bad_rr")

    db.update_signal_outcome(sig.id, {
        "outcome_tp1_reached": 1,
        "outcome_tp2_reached": 1,
        "outcome_sl_hit": 0,
        "outcome_max_favorable_r": 2.5,
        "outcome_max_adverse_r": 0.3,
        "outcome_minutes_to_move": 45,
        "outcome_would_win": 1,
    })

    with db.get_conn() as conn:
        row = conn.execute(
            "SELECT outcome_would_win, outcome_tp2_reached FROM scalp_signals WHERE id=?",
            (sig.id,)
        ).fetchone()
    assert row["outcome_would_win"] == 1
    assert row["outcome_tp2_reached"] == 1


def test_missed_opportunities_query(tmp_db):
    """Reddedilen ama kazandıracak sinyaller sorgulanabilmeli."""
    import database as db
    from core.data_layer import DataLayer, DATA_THRESHOLD

    dl  = DataLayer()
    sig = dl.create_signal("ADAUSDT")
    sig.direction   = "LONG"
    sig.entry_zone  = 0.5
    sig.stop_loss   = 0.48
    sig.setup_quality = "B"
    sig.final_score = DATA_THRESHOLD + 1
    dl.reject(sig, "low_confidence")

    db.update_signal_outcome(sig.id, {
        "outcome_tp2_reached": 1,
        "outcome_would_win": 1,
        "outcome_max_favorable_r": 3.1,
    })

    missed = db.get_missed_opportunities(days=1)
    assert any(m["outcome_would_win"] == 1 for m in missed)
