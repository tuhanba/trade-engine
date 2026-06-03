from core.data_layer import SignalData
from telegram_delivery import format_signal


def test_telegram_format_contains_required_fields():
    sig = SignalData(symbol="ETHUSDT", direction="LONG", entry_zone=100, stop_loss=98, tp1=102, tp2=104, tp3=106, setup_quality="A+")
    sig.final_score = 80
    sig.risk_percent = 1
    sig.max_loss = 10
    msg = format_signal(sig)
    assert "ETHUSDT" in msg
    assert "Giriş" in msg
    assert "Stop" in msg
    assert "Skor" in msg


def test_weekly_report_card_generator():
    from telegram_delivery import generate_weekly_report_card, generate_weekly_digest
    stats = {
        "total_trades": 10,
        "win_rate": 60.0,
        "wins_count": 6,
        "losses_count": 4,
        "net_pnl": 150.50,
        "best_coin": "BTCUSDT",
        "best_pnl": 200.00,
        "worst_coin": "SOLUSDT",
        "worst_pnl": -50.00,
        "avg_win_score": 75.0,
        "avg_loss_score": 45.0,
    }
    img_bytes = generate_weekly_report_card(stats)
    assert isinstance(img_bytes, bytes)
    assert len(img_bytes) > 0
    
    # Try text version
    text_ver = generate_weekly_digest()
    assert isinstance(text_ver, str)
