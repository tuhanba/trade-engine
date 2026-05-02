from core.data_layer import SignalData
from telegram_delivery import format_signal


def test_telegram_format_contains_required_fields():
    sig = SignalData(symbol="ETHUSDT", direction="LONG", entry_zone=100, stop_loss=98, tp1=102, tp2=104, tp3=106, setup_quality="A+")
    sig.final_score = 80
    sig.risk_percent = 1
    sig.max_loss = 10
    msg = format_signal(sig)
    assert "Mode:" in msg
    assert "Why this trade?" in msg
    assert "Invalidasyon" in msg
