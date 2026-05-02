"""test_telegram_format.py — Telegram format ve DB dedup."""
import pytest


def _make_sig(score=80.0, quality="A+", direction="LONG",
              rr=2.0, risk_pct=1.5, notional=150.0, leverage=5):
    from core.data_layer import SignalData
    sig = SignalData(
        symbol="BTCUSDT", direction=direction,
        entry_zone=30000.0, stop_loss=29500.0,
        tp1=30500.0, tp2=31000.0, tp3=32000.0,
        setup_quality=quality, final_score=score,
        rr=rr, net_rr=rr * 0.9,
        risk_percent=risk_pct, risk_amount=15.0,
        position_size=0.005, notional_size=notional,
        leverage_suggestion=leverage, max_loss=15.0,
        estimated_fee=0.12, confidence=0.8,
        reason="Güçlü trend + yüksek hacim",
    )
    sig.approved_for_telegram = True
    return sig


def test_format_contains_required_fields():
    from telegram_delivery import format_signal
    sig = _make_sig()
    msg = format_signal(sig)

    required = [
        "BTCUSDT", "LONG", "PAPER",   # mode + sembol
        "Entry", "Stop", "TP1", "TP2",
        "RR", "Risk", "Pozisyon", "Notional",
        "Kaldıraç", "MaxKayıp", "Fee",
        "Güven", "Sebep",
    ]
    for field in required:
        assert field in msg, f"Eksik alan: {field}"


def test_format_live_mode(monkeypatch):
    from telegram_delivery import format_signal
    import os
    monkeypatch.setenv("EXECUTION_MODE", "live")

    sig = _make_sig()
    msg = format_signal(sig)
    assert "LIVE" in msg


def test_format_paper_mode(monkeypatch):
    from telegram_delivery import format_signal
    import os
    monkeypatch.setenv("EXECUTION_MODE", "paper")

    sig = _make_sig()
    msg = format_signal(sig)
    assert "PAPER" in msg


def test_format_quality_s():
    from telegram_delivery import format_signal
    sig = _make_sig(quality="S", score=90)
    msg = format_signal(sig)
    assert "S-CLASS" in msg or "FULL SIZE" in msg


def test_format_quality_b_warning():
    from telegram_delivery import format_signal
    sig = _make_sig(quality="B", score=76)
    msg = format_signal(sig)
    assert "yarım pozisyon" in msg.lower() or "HALF SIZE" in msg


def test_db_dedup_prevents_resend(tmp_db):
    from telegram_delivery import deliver_signal
    import database as db

    sig = _make_sig()
    sig.id = "dedup-test-001"

    # İlk kez gönderilen → DB'ye yaz
    db.mark_telegram_sent(sig.id, sig.symbol, sig.direction, sig.setup_quality)

    # deliver_signal DB'den kontrol edip skip etmeli
    result = deliver_signal(sig)
    assert not result  # duplicate — gönderilmemeli


def test_deliver_requires_approved_flag(tmp_db):
    from telegram_delivery import deliver_signal
    sig = _make_sig()
    sig.approved_for_telegram = False
    result = deliver_signal(sig)
    assert not result


def test_deliver_skips_invalid_signal(tmp_db):
    from telegram_delivery import deliver_signal
    from core.data_layer import SignalData
    sig = SignalData(symbol="BTCUSDT")  # entry_zone=0 → invalid
    sig.approved_for_telegram = True
    sig.setup_quality = "A+"
    result = deliver_signal(sig)
    assert not result
