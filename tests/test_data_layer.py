"""test_data_layer.py — DataLayer ve SignalData testleri."""
import pytest


def test_signal_create_and_update(tmp_db):
    from core.data_layer import DataLayer, SignalData
    dl = DataLayer()
    sig = dl.create_signal("BTCUSDT")
    assert sig.symbol == "BTCUSDT"
    assert sig.lifecycle_stage == "SCANNED"
    assert sig.id in dl._active


def test_signal_lifecycle_stages(tmp_db):
    from core.data_layer import DataLayer
    dl = DataLayer()
    sig = dl.create_signal("ETHUSDT")
    sig.direction  = "LONG"
    sig.entry_zone = 2000
    sig.stop_loss  = 1950
    sig.setup_quality = "A+"

    dl.update_stage(sig, "TREND_CHECKED",   {"trend_score": 8.0})
    dl.update_stage(sig, "TRIGGER_CHECKED", {"trigger_score": 7.5})
    dl.update_stage(sig, "RISK_CHECKED",    {"risk_score": 6.0})
    assert sig.lifecycle_stage == "RISK_CHECKED"
    assert sig.trend_score == 8.0


def test_signal_reject(tmp_db):
    from core.data_layer import DataLayer
    dl = DataLayer()
    sig = dl.create_signal("SOLUSDT")
    sig.direction  = "SHORT"
    sig.final_score = 50  # data threshold üstü
    sig.entry_zone = 100
    sig.stop_loss  = 105

    dl.reject(sig, "bad_rr", "RR=1.2 < 1.5")
    assert sig.lifecycle_stage == "REJECTED"
    assert sig.reject_reason   == "bad_rr"
    assert sig.id not in dl._active


def test_threshold_evaluation(tmp_db):
    from core.data_layer import SignalData, DATA_THRESHOLD, WATCHLIST_THRESHOLD, TELEGRAM_THRESHOLD, TRADE_THRESHOLD
    sig = SignalData(symbol="XUSDT", direction="LONG",
                     entry_zone=1.0, stop_loss=0.95, setup_quality="A+")

    sig.final_score = DATA_THRESHOLD - 1
    assert not sig.passes_data_threshold()

    sig.final_score = WATCHLIST_THRESHOLD + 1
    assert sig.passes_watchlist_threshold()
    assert not sig.passes_telegram_threshold()

    sig.final_score = TRADE_THRESHOLD + 1
    assert sig.passes_trade_threshold()


def test_signal_is_valid(tmp_db):
    from core.data_layer import SignalData
    sig = SignalData()
    assert not sig.is_valid()

    sig.symbol       = "BTCUSDT"
    sig.direction    = "LONG"
    sig.entry_zone   = 30000
    sig.stop_loss    = 29000
    sig.setup_quality= "A"
    assert sig.is_valid()

    sig.setup_quality = "D"
    assert not sig.is_valid()


def test_evaluate_thresholds_sets_flags(tmp_db):
    from core.data_layer import DataLayer, TRADE_THRESHOLD
    dl  = DataLayer()
    sig = dl.create_signal("BTCUSDT")
    sig.final_score = TRADE_THRESHOLD + 5
    sig.direction   = "LONG"

    dl.evaluate_thresholds(sig)
    assert sig.approved_for_watchlist
    assert sig.approved_for_telegram
    assert sig.approved_for_trade


def test_pending_telegram_queue(tmp_db):
    from core.data_layer import DataLayer, TELEGRAM_THRESHOLD
    dl = DataLayer()
    sig = dl.create_signal("ADAUSDT")
    sig.direction   = "SHORT"
    sig.entry_zone  = 0.5
    sig.stop_loss   = 0.52
    sig.setup_quality = "A+"
    sig.final_score = TELEGRAM_THRESHOLD + 1
    sig.approved_for_telegram = True

    pending = dl.get_pending_telegram()
    assert any(s.id == sig.id for s in pending)
