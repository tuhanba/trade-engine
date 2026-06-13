"""
tests/test_telegram_templates.py — Faz 5.1 merkezi şablon + sayı formatı testleri.

Plan kabul kriteri: sabit görsel imza (her mesaj tipi aynı düzen) + SABİT
sayı formatı (para $1,234.50, yüzde +1.57%, R +1.27R).
"""
import pytest

import telegram_delivery as t


# ── Sayı formatı tutarlılığı ──────────────────────────────────────────────────

def test_fmt_money():
    assert t.fmt_money(1234.5) == "$1,234.50"
    assert t.fmt_money(0) == "$0.00"
    assert t.fmt_money(-50.1) == "-$50.10"


def test_fmt_money_signed():
    assert t.fmt_money_signed(31.4) == "+$31.40"
    assert t.fmt_money_signed(-10) == "-$10.00"
    assert t.fmt_money_signed(0) == "+$0.00"


def test_fmt_pct():
    assert t.fmt_pct(1.57) == "+1.57%"
    assert t.fmt_pct(-1.58) == "-1.58%"


def test_fmt_r():
    assert t.fmt_r(1.27) == "+1.27R"
    assert t.fmt_r(-1.0) == "-1.00R"


def test_fmt_price_adaptive():
    assert t.fmt_price(60000) == "60,000.00"
    assert t.fmt_price(142.35) == "142.350"
    assert t.fmt_price(0.00002431) == "0.00002431"


# ── R hesabı ──────────────────────────────────────────────────────────────────

def test_r_at_target_long():
    # entry 142.35, sl 140.10 (risk 2.25), tp1 145.20 (mesafe 2.85) → R≈1.27
    r = t._r_at_target(142.35, 140.10, 145.20, "LONG")
    assert round(r, 2) == 1.27


def test_r_at_target_short():
    # SHORT: entry 100, sl 102 (risk 2), target 96 (mesafe 4) → R=2.0
    r = t._r_at_target(100, 102, 96, "SHORT")
    assert round(r, 2) == 2.0


# ── Trade açılış şablonu ──────────────────────────────────────────────────────

def test_tpl_trade_open_format():
    msg = t.tpl_trade_open("SOLUSDT", "LONG", 10, 142.350, 140.100, 145.200, 147.800,
                           20.0, 1.0, 68, regime="TREND↑", ghost_wr=64, ghost_n=22)
    assert "🟢 <b>LONG • SOLUSDT</b>" in msg
    assert "x10" in msg
    assert "Giriş   <code>142.350</code>" in msg
    assert "(-1.58%)" in msg          # stop yüzdesi
    assert "R 1.27" in msg            # TP1 R
    assert "Risk $20.00 (1.0%) • Skor 68 • Rejim TREND↑" in msg
    assert "👻 Ghost WR(setup): 64% (n=22)" in msg


def test_tpl_trade_open_short_icon():
    msg = t.tpl_trade_open("BTCUSDT", "SHORT", 5, 60000, 61000, 59000, 58000,
                           50.0, 2.0, 72)
    assert "🔵 <b>SHORT • BTCUSDT</b>" in msg
    # ghost satırı yoksa eklenmemeli
    assert "Ghost WR" not in msg


# ── Trade kapanış şablonu (format DEĞİŞMEZ, sadece ikon) ─────────────────────

def test_tpl_trade_close_win():
    msg = t.tpl_trade_close("SOLUSDT", "LONG", 31.40, 1.57, "47dk", "tp2", 2184.20,
                            today_wins=4, today_losses=1, today_pnl=54.10, expectancy_r=0.31)
    assert "✅ <b>KAZANÇ</b> • SOLUSDT LONG" in msg
    assert "PnL    +$31.40  (+1.57R)" in msg
    assert "Süre   47dk • Sebep: TP2" in msg
    assert "Bakiye $2,184.20  (gün: +$54.10)" in msg
    assert "📊 Bugün: 4W-1L • E(30g): +0.31R" in msg


def test_tpl_trade_close_loss_same_layout():
    win = t.tpl_trade_close("X", "LONG", 10, 1.0, "5dk", "tp1", 100)
    loss = t.tpl_trade_close("X", "LONG", -10, -1.0, "5dk", "sl", 100)
    # İkon farklı, satır düzeni aynı (PnL/Süre/Bakiye satır başlıkları korunur)
    assert "✅ <b>KAZANÇ</b>" in win and "🔻 <b>ZARAR</b>" in loss
    for label in ("PnL    ", "Süre   ", "Bakiye "):
        assert label in win and label in loss


# ── Anomali şablonu ───────────────────────────────────────────────────────────

def test_tpl_anomaly_format():
    msg = t.tpl_anomaly("Sinyal Kuraklığı",
                        ["Son 2 saattir sinyal yok.", "Teşhis: WS canlı ✓"],
                        suggestion="BTC NEUTRAL'a dönene dek normal.")
    assert msg.startswith("🚨 <b>ANOMALİ • Sinyal Kuraklığı</b>")
    assert "Öneri: BTC NEUTRAL'a dönene dek normal." in msg
    assert msg.rstrip().endswith("/teshis ile tam rapor")
