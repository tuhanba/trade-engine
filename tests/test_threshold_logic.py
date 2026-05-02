"""test_threshold_logic.py — 3 katmanlı eşik mantığı."""
import pytest


def test_data_threshold_not_persisted(tmp_db):
    """DATA_THRESHOLD altı sinyaller DB'ye yazılmaz."""
    from core.data_layer import DataLayer, DATA_THRESHOLD
    import database as db

    dl  = DataLayer()
    sig = dl.create_signal("BTCUSDT")
    sig.direction   = "LONG"
    sig.final_score = DATA_THRESHOLD - 5
    sig.entry_zone  = 30000.0
    sig.stop_loss   = 29000.0
    sig.setup_quality = "B"

    dl.persist(sig)

    with db.get_conn() as conn:
        row = conn.execute("SELECT id FROM scalp_signals WHERE id=?", (sig.id,)).fetchone()
    assert row is None


def test_watchlist_approved_at_threshold(tmp_db):
    from core.data_layer import DataLayer, WATCHLIST_THRESHOLD
    dl  = DataLayer()
    sig = dl.create_signal("ETHUSDT")
    sig.direction   = "SHORT"
    sig.entry_zone  = 2000.0
    sig.stop_loss   = 2100.0
    sig.setup_quality = "A"
    sig.final_score = WATCHLIST_THRESHOLD + 1

    dl.evaluate_thresholds(sig)
    assert sig.approved_for_watchlist
    assert not sig.approved_for_telegram
    assert not sig.approved_for_trade


def test_telegram_approved_at_threshold(tmp_db):
    from core.data_layer import DataLayer, TELEGRAM_THRESHOLD
    dl  = DataLayer()
    sig = dl.create_signal("SOLUSDT")
    sig.direction   = "LONG"
    sig.entry_zone  = 100.0
    sig.stop_loss   = 95.0
    sig.setup_quality = "A+"
    sig.final_score = TELEGRAM_THRESHOLD + 1

    dl.evaluate_thresholds(sig)
    assert sig.approved_for_watchlist
    assert sig.approved_for_telegram
    assert not sig.approved_for_trade


def test_trade_approved_at_threshold(tmp_db):
    from core.data_layer import DataLayer, TRADE_THRESHOLD
    dl  = DataLayer()
    sig = dl.create_signal("BNBUSDT")
    sig.direction   = "LONG"
    sig.entry_zone  = 300.0
    sig.stop_loss   = 290.0
    sig.setup_quality = "S"
    sig.final_score = TRADE_THRESHOLD + 1

    dl.evaluate_thresholds(sig)
    assert sig.approved_for_watchlist
    assert sig.approved_for_telegram
    assert sig.approved_for_trade


def test_boundary_values(tmp_db):
    """Eşik sınır değerlerinde doğru davranış."""
    from core.data_layer import (DataLayer, SignalData,
                                  DATA_THRESHOLD, WATCHLIST_THRESHOLD,
                                  TELEGRAM_THRESHOLD, TRADE_THRESHOLD)
    sig = SignalData(symbol="X", direction="LONG",
                     entry_zone=1.0, stop_loss=0.9, setup_quality="A")

    sig.final_score = DATA_THRESHOLD
    assert sig.passes_data_threshold()

    sig.final_score = WATCHLIST_THRESHOLD
    assert sig.passes_watchlist_threshold()

    sig.final_score = TELEGRAM_THRESHOLD
    assert sig.passes_telegram_threshold()

    sig.final_score = TRADE_THRESHOLD
    assert sig.passes_trade_threshold()

    sig.final_score = TRADE_THRESHOLD - 1
    assert not sig.passes_trade_threshold()
