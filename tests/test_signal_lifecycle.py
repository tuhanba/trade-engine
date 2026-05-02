"""test_signal_lifecycle.py — Lifecycle stage geçişleri ve DB tutarlılığı."""
import pytest


def test_full_lifecycle_approved(tmp_db):
    """SCANNED → ... → APPROVED_FOR_TRADE akışı."""
    from core.data_layer import DataLayer, TRADE_THRESHOLD
    import database as db

    dl  = DataLayer()
    sig = dl.create_signal("BTCUSDT")
    sig.direction      = "LONG"
    sig.entry_zone     = 30000.0
    sig.stop_loss      = 29500.0
    sig.tp1            = 30500.0
    sig.tp2            = 31000.0
    sig.tp3            = 32000.0
    sig.setup_quality  = "S"
    sig.rr             = 2.0
    sig.net_rr         = 1.8
    sig.risk_percent   = 2.0
    sig.risk_amount    = 20.0
    sig.position_size  = 0.004
    sig.notional_size  = 120.0
    sig.leverage_suggestion = 4

    dl.update_stage(sig, "TREND_CHECKED",   {"trend_score":   9.0})
    dl.update_stage(sig, "TRIGGER_CHECKED", {"trigger_score": 8.5})
    dl.update_stage(sig, "RISK_CHECKED",    {"risk_score":    8.0})
    dl.update_stage(sig, "AI_CHECKED",      {"ai_score": 9.0, "final_score": TRADE_THRESHOLD + 5})
    sig.final_score = TRADE_THRESHOLD + 5

    dl.evaluate_thresholds(sig)
    assert sig.approved_for_watchlist
    assert sig.approved_for_telegram
    assert sig.approved_for_trade

    dl.approve_trade(sig)
    assert sig.lifecycle_stage == "APPROVED_FOR_TRADE"

    dl.mark_opened(sig, trade_id=1)
    assert sig.lifecycle_stage == "OPENED"
    assert sig.linked_trade_id == 1


def test_full_lifecycle_rejected(tmp_db):
    """SCANNED → REJECTED — event DB'ye yazıldı mı."""
    from core.data_layer import DataLayer
    import database as db

    dl  = DataLayer()
    sig = dl.create_signal("DOGEUSDT")
    sig.direction   = "LONG"
    sig.final_score = 50
    sig.entry_zone  = 0.1
    sig.stop_loss   = 0.095

    dl.update_stage(sig, "TREND_CHECKED", {"trend_score": 3.0})
    dl.reject(sig, "weak_trend", "ADX=18 < 28")

    events = db.get_signal_lifecycle(sig.id)
    stages = [e["stage"] for e in events]
    assert "SCANNED" in stages
    assert "TREND_CHECKED" in stages
    assert "REJECTED" in stages
    assert sig.id not in dl._active


def test_lifecycle_persists_to_db(tmp_db):
    """Persist edilmiş sinyal DB'de var mı."""
    import database as db
    from core.data_layer import DataLayer, DATA_THRESHOLD

    dl  = DataLayer()
    sig = dl.create_signal("ETHUSDT")
    sig.direction      = "SHORT"
    sig.entry_zone     = 2000.0
    sig.stop_loss      = 2050.0
    sig.setup_quality  = "A"
    sig.final_score    = DATA_THRESHOLD + 1

    dl.update_stage(sig, "RISK_CHECKED", {"risk_score": 6.0})
    dl.persist(sig)

    with db.get_conn() as conn:
        row = conn.execute(
            "SELECT id, lifecycle_stage FROM scalp_signals WHERE id=?", (sig.id,)
        ).fetchone()
    assert row is not None
    assert row["lifecycle_stage"] == "RISK_CHECKED"


def test_bot_restart_restore(tmp_db):
    """OPENED stage'deki sinyaller restore edilmeli."""
    import database as db
    from core.data_layer import DataLayer

    # Önce bir sinyal yaz
    dl_write = DataLayer()
    sig = dl_write.create_signal("BNBUSDT")
    sig.direction       = "LONG"
    sig.entry_zone      = 300.0
    sig.stop_loss       = 290.0
    sig.setup_quality   = "A+"
    sig.final_score     = 85
    sig.lifecycle_stage = "OPENED"
    sig.linked_trade_id = 42

    with db.get_conn() as conn:
        conn.execute("""
            INSERT OR REPLACE INTO scalp_signals
            (id, symbol, direction, entry_zone, stop_loss, setup_quality, final_score,
             lifecycle_stage, linked_trade_id)
            VALUES (?,?,?,?,?,?,?,?,?)
        """, (sig.id, sig.symbol, sig.direction, sig.entry_zone, sig.stop_loss,
              sig.setup_quality, sig.final_score, "OPENED", 42))

    # Yeni DataLayer instance — restore et
    dl_restore = DataLayer()
    dl_restore.restore_from_db()
    assert sig.id in dl_restore._active
