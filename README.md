# AX Scalp Engine v2.0

**Minimal, sagLam, hizli, profesyonel, yuksek frekanslı ama disiplinli calisan AI scalp trade engine.**

---

## Mimari

```
Market Scanner
-> Trend Engine
-> Trigger Engine
-> Risk Engine
-> AI Decision Engine
-> Data Layer
-> Dashboard + Telegram
```

> **Kural:** Dashboard ve Telegram ham veri kullanmaz. Sadece Data Layer'dan validate edilmis veri alir.

---

## Moduller

| Modul | Dosya | Gorev |
|---|---|---|
| Market Scanner | `core/market_scanner.py` | Binance'den USDT paritelerini ceker, filtreler, Tradeability Score verir |
| Trend Engine | `core/trend_engine.py` | EMA 20/50/200, market structure, trend yonu ve skoru |
| Trigger Engine | `core/trigger_engine.py` | Giris onayi, setup kalitesi (A+/A/B/C/D) |
| Risk Engine | `core/risk_engine.py` | Stop, TP1/2/3, RR, pozisyon buyuklugu, kaldirac |
| AI Decision Engine | `core/ai_decision_engine.py` | Final karar katmani, gunluk limit, spam engeli |
| Data Layer | `core/data_layer.py` | Tek schema, validate edilmis veri akisi |
| Dashboard | `app.py` + `templates/index.html` | Sadece Data Layer'dan beslenir |
| Telegram Delivery | `telegram_delivery.py` | Sadece Data Layer'dan beslenir, duplicate engeli |

---

## Tek Schema (SignalData)

Tum moduller `SignalData` ile calisir:

```
id, symbol, timestamp, source, timeframe, direction,
coin_score, trend_score, trigger_score, risk_score, final_score,
setup_quality, entry_zone, stop_loss, tp1, tp2, tp3,
rr, risk_percent, position_size, notional_size, leverage_suggestion,
max_loss, invalidation_level, confidence, status, reason,
telegram_status, dashboard_status, error
```

---

## Setup Kalitesi

| Kalite | Aciklama | Telegram | Dashboard |
|---|---|---|---|
| A+ | En guclu setup | Gonderilir | Gosterilir |
| A | Guclu setup | Gonderilir | Gosterilir |
| B | Dusuk riskli aktif scalp | Gonderilir (half size) | Gosterilir |
| C | Zayif setup | Gonderilmez | Watchlist |
| D | Elenir | Hayir | Hayir |

---

## Sinyal Frekansi

- Normal gun: 25-30 kaliteli firsat
- Volatil gun: 30-40 kaliteli firsat
- Maksimum: **40 sinyal/gun**

---

## Kurulum

```bash
pip install -r requirements.txt
cp .env.example .env
# .env dosyasini duzenle
python scalp_bot.py   # Bot
python app.py         # Dashboard
```

---

## Faz Durumu

- [x] Faz 1 - Market Scanner / Coin Discovery
- [x] Faz 2 - Trend + Trigger Engine
- [x] Faz 3 - Risk Engine / Stop / Target
- [x] AI Decision Engine
- [x] Data Layer (Tek Schema)
- [x] Dashboard (Data Layer'dan besleniyor)
- [x] Telegram Delivery (Data Layer'dan besleniyor)
