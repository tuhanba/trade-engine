# AX Trade Engine

Paper-mode kripto trade engine. Binance Futures public verileri kullanarak sinyal üretir, paper trade yönetir ve dashboard sunar.

> ⚠️ **Live trading varsayılan olarak KAPALIDIR.** Sistem paper mode'da çalışır. Gerçek emir gönderilmez.

## Güvenlik

| Ayar | Default |
|------|---------|
| `EXECUTION_MODE` | `paper` |
| `LIVE_TRADING_ENABLED` | `False` |
| `DRY_RUN` | `True` |
| `CONFIRM_LIVE_TRADING` | `False` |
| `USE_BINANCE_PRIVATE_API` | `False` |

Live trading ancak **üç koşul birden** sağlandığında aktif olabilir: `EXECUTION_MODE=live`, `LIVE_TRADING_ENABLED=True`, `CONFIRM_LIVE_TRADING=True`.

## Mimari

```
scalp_bot_v3.py          → Ana bot döngüsü
├── core/market_data.py  → Binance public data
├── core/coin_library.py → Sembol filtre & ranking
├── core/signal_engine.py→ Sinyal üretimi
├── core/ai_decision_engine.py → ALLOW/WATCH/VETO
├── core/risk_engine.py  → Risk filtresi
├── core/accounting.py   → PnL / margin / fee hesap
├── core/paper_tracker.py→ Ghost sinyal takibi
├── core/data_layer.py   → Veri modelleri & normalleştirme
├── execution_engine.py  → Paper trade lifecycle
├── database.py          → SQLite veri katmanı
├── telegram_delivery.py → Telegram bildirimleri
├── dashboard_service.py → Dashboard veri servisi
└── app.py               → Flask API
```

## Dosya Görevleri

| Dosya | Görev |
|-------|-------|
| `config.py` | Merkezi konfigürasyon, env yükleme, güvenlik kontrolleri |
| `database.py` | SQLite CRUD, migration, tablo yönetimi |
| `execution_engine.py` | Paper trade açma/kapama/güncelleme |
| `dashboard_service.py` | Dashboard verilerini toplama |
| `telegram_delivery.py` | Telegram mesaj gönderimi |
| `app.py` | Flask REST API endpointleri |
| `scalp_bot_v3.py` | Ana bot runner (scan loop) |
| `core/data_layer.py` | SignalData, TradeData dataclass'ları |
| `core/accounting.py` | PnL, fee, margin, risk hesaplamaları |
| `core/market_data.py` | Binance public ticker/kline verileri |
| `core/coin_library.py` | Sembol universe ve filtre |
| `core/signal_engine.py` | Basit momentum sinyal üretimi |
| `core/ai_decision_engine.py` | Rule-based AI karar mekanizması |
| `core/risk_engine.py` | Risk bazlı trade filtresi |
| `core/paper_tracker.py` | Açılmayan sinyal adayı takibi |

## API Endpointleri

| Endpoint | Açıklama |
|----------|----------|
| `GET /` | Dashboard HTML |
| `GET /api/health` | Sistem sağlık durumu |
| `GET /api/live` | Açık trade listesi |
| `GET /api/stats` | Özet istatistikler |
| `GET /api/trades` | Son trade'ler |
| `GET /api/signals` | Son sinyal adayları |

Tüm API yanıtları `{"ok": true, "data": ...}` formatındadır.

## DB Tabloları

- **trades** – Trade kayıtları (entry, SL, TP, PnL, status)
- **signal_candidates** – Sinyal adayları ve kararlar
- **balance_ledger** – Bakiye geçmişi
- **bot_status** – Bot durum key-value

## Kurulum & Çalıştırma

```bash
pip install -r requirements.txt
cp .env.example .env  # Gerekli ayarları düzenle

# Dashboard
python app.py

# Bot
python scalp_bot_v3.py
```

## Test

```bash
# Compile kontrolü
python -m compileall .

# Testler
pytest -q

# PnL audit
python scripts/audit_pnl_consistency.py

# DB migration
python scripts/migrate_accounting_schema.py
```
