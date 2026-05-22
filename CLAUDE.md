# AurvexAI — Master Claude Code Rehberi
> **Güncelleme:** 2026-05-22 | **Versiyon:** v3.0 Production
> **Sunucu:** `/root/trade_engine/trade-engine/` | **DB:** `trading.db`

---

## 🚨 ACİL — ŞU AN YAPILACAK (sırayla)

```
1. debug-no-telegram     → Neden Telegram mesajı gelmiyor? Bul ve düzelt
2. fix-tp-sl-ratios      → TP1=1.5R, TP2=2.5R, SL min %1.5
3. coin-library-v2       → Coin scoring + kütüphane
4. signal-quality-v2     → Swing H/L TP, giriş kalitesi
5. fix-ghost-pipeline    → Ghost learning tam entegrasyon
6. enhance-all           → Dashboard + Telegram komutları
```

---

## Sistem Mimarisi

```
Binance Futures API (public endpoints, no key needed for data)
    ↓
MarketScanner → 25 coin filtrele (hacim + hareket)
    ↓
TriggerEngine → Sinyal üret (ADX, RSI, MACD, VWAP, Volume)
    ↓  Score: 5-10 (trigger_score)
TrendEngine → 4TF trend konfirmasyonu (confluence_score)
    ↓  Score: 0-10 (trend_score)
RiskEngine → Pozisyon boyutu, kaldıraç, R:R
    ↓  Score: 0-10 (risk_score)
AIDecisionEngine → final_score = trigger×4 + trend×3 + risk×3 (max 100)
    ↓
Threshold Pipeline:
  final_score >= DATA_THRESHOLD(20)     → signal_candidates'a kaydet
  final_score >= WATCHLIST_THRESHOLD(25) → watchlist
  final_score >= TELEGRAM_THRESHOLD(28)  → Telegram'a gönder ← BURASI SORUNLU
  final_score >= TRADE_THRESHOLD(35)     → Trade aç
    ↓
ExecutionEngine → Trade yönet (TP1/TP2/Breakeven/Trailing/SL)
    ↓
Telegram bildirimi → Trade open/TP1/TP2/Close
```

## Kritik Eşikler (config.py)

```python
DATA_THRESHOLD      = 20.0   # Bu altı hiç kaydedilmez
WATCHLIST_THRESHOLD = 25.0   # Watchlist'e girer
TELEGRAM_THRESHOLD  = 28.0   # ← Telegram'a gönderilir
TRADE_THRESHOLD     = 35.0   # ← Trade açılır
TP1_R               = 1.5    # TP1 = entry ± SL_dist × 1.5
TP2_R               = 2.5    # TP2 = entry ± SL_dist × 2.5
TP3_R               = 4.0    # Runner hedef
SL_ATR_MULT         = 1.8    # SL = entry ± ATR × 1.8
MIN_RR              = 1.5    # Bu altı R:R kabul edilmez
MIN_SL_PCT          = 0.015  # SL min %1.5 (gürültü koruması)
EXECUTABLE_QUALITIES = [S, A+, A]  # B kalite execute edilmez
```

## Dosya Haritası

```
scalp_bot.py          ← Ana döngü (tüm pipeline buradan geçer)
execution_engine.py   ← Trade aç/kapat, TP1/TP2/Trailing/SL
signal_engine.py      ← Sinyal üretimi (entry/SL/TP hesabı)
core/
  trigger_engine.py   ← Kalite skorlama (S/A+/A/B/C/D)
  trend_engine.py     ← Multi-TF trend analizi
  risk_engine.py      ← Pozisyon boyutu ve risk
  ai_decision_engine.py ← ALLOW/VETO kararı
  coin_library.py     ← Coin filtresi ve kütüphanesi
  ghost_learning.py   ← Ghost Learning 2.0
  market_scanner.py   ← Coin tarama
telegram_delivery.py  ← Bildirim formatları
telegram_manager.py   ← /komutlar
database.py           ← SQLite katmanı
ai_brain.py           ← Nightly optimizer
ghost_learner.py      ← Ghost scheduler
config.py             ← TÜM parametreler buradan
app.py                ← Dashboard API
```

## Bilinen Sorunlar

| Sorun | Neden | Agent |
|---|---|---|
| Telegram mesajı gelmiyor | final_score < 28 veya deliver_signal fail | debug-no-telegram |
| 10/10 kayıp | TP1_R=1.0 çok düşük, SL gürültüde | fix-tp-sl-ratios |
| Ghost signals = 0 | save_ghost_signal fail | fix-ghost-pipeline |
| Bakiye $406 | Kümülatif kayıplar | Kabul (paper mode) |

## Araştırma Bulguları (Best Practices)

### Optimal Scalping Parametreleri
- **SL**: ATR × 1.5-2.5 (5m TF için), minimum %1.5 mesafe
- **TP1**: 1.5-2.0R (erken kâr al)
- **TP2**: 2.5-3.5R (ana hedef)
- **R:R minimum**: 1.5 (daha azı uzun vadede kârsız)
- **ADX filtresi**: >20 (trend var/yok ayrımı) — zaten var ✅
- **Funding rate filtresi**: zaten var ✅
- **Session filtresi**: London (08-12 UTC) + NY (13-17 UTC) en iyi
- **Coin başına cooldown**: 3 ardışık kayıp → 2 saat bekle — zaten var ✅

### Coin Library Best Practices
- Sadece hacim değil: **trend tutarlılığı** (ADX tarihsel ortalama)
- **Spread kontrolü**: bid-ask spread < %0.1
- **Funding rate tarihi**: sürekli negatif/pozitif funding = risk
- **Coin volatilite skoru**: coin başına ATR/price oran ortalaması
- **Likidite derinliği**: order book depth %2 içi

### Ghost Learning Best Practices
- VETO sinyallerini sakla → sonucu simüle et
- Hangi setup türleri "miss" oluyor? (ghost WIN rate)
- Threshold'u periyodik düşür → daha fazla fırsat yakala

## Test Komutları

```bash
# Telegram test
bash aurvex_maintain.sh --telegram-test

# Son 10 sinyalin skoru
python3 -c "
from database import get_conn
with get_conn() as c:
    rows = c.execute('''
        SELECT symbol, direction, decision, final_score, setup_quality, created_at
        FROM signal_candidates
        ORDER BY id DESC LIMIT 10
    ''').fetchall()
    for r in rows:
        print(f'{r[5][:16]} {r[0]:<12} {r[1]:<6} score={r[2]} qual={r[3]} dec={r[4]}')
"

# Bot sağlığı
bash aurvex_maintain.sh --fix

# Canlı log
tail -f logs/ax_bot.log | grep -v "Scanner\|Kayıp bilgisi"
```
