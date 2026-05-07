# AURVEX AI Trade Engine — Claude Code Rehberi

> Bu dosya Claude Code oturumları için yazılmıştır.
> Son güncelleme: 2026-05-07 | Branch: `claude/audit-fix-codebase-Uf2IF`
> Commit: `4c30148` — FAZ 3 tamamlandı

---

## Projeye Genel Bakış

Python 3.11 + SQLite (WAL-mode) tabanlı kripto vadeli işlem botu.
Binance Futures üzerinde çalışır; paper ve live mod destekler.

```
trade-engine/
├── config.py               # Tüm sabitler — asla doğrudan os.getenv kullanma
├── database.py             # SQLite katmanı v5.1 — tek giriş noktası
├── app.py                  # Flask API / dashboard
├── scalp_bot.py            # Ana bot döngüsü
├── execution_engine.py     # Trade aç/kapat/yönet
├── signal_engine.py        # Sinyal üretimi
├── ai_brain.py             # AI yöneticisi (karar + öğrenme)
├── telegram_delivery.py    # Bildirim formatlama
├── dashboard_service.py    # /api/* veri katmanı
├── ml_signal_scorer.py     # Voting Ensemble RF+GB
├── coin_library.py         # Coin profilleri
├── core/
│   ├── accounting.py       # Saf PnL/fee formülleri
│   ├── ai_decision_engine.py
│   ├── market_scanner.py
│   ├── risk_engine.py
│   ├── trend_engine.py
│   └── trigger_engine.py
└── scripts/
    ├── migrate_accounting_schema.py  # DB migration
    └── audit_pnl_consistency.py     # 12 noktalı audit
```

---

## Kritik Tasarım Kuralları

### 1. PnL Formülü
```python
# LONG:  pnl = (exit - entry) × qty
# SHORT: pnl = (entry - exit) × qty
# Kaldıraç PnL'i ÇARPMAZ — sadece pozisyon büyüklüğünü etkiler
```
Tüm hesaplamalar `core/accounting.py` üzerinden geçer.

### 2. Fee Çift Sayımı Önleme
- `open_fee` sadece pozisyon açılışında **bir kez** kaydedilir
- TP1/TP2/SL kısmi kapanışlarda **sadece çıkış tarafı fee** hesaplanır
- `calculate_partial_close_pnl(direction, entry, exit_price, close_qty)` → `(net_pnl, exit_fee)`

### 3. `_TRADE_COLUMNS` Cache
`database.py` içinde global `_TRADE_COLUMNS = None` var.
Migration sonrası mutlaka sıfırla:
```python
import database
database._TRADE_COLUMNS = None
```

### 4. `close_trade()` İmzası (v5.1)
```python
close_trade(trade_id, net_pnl, total_fee, reason, r_multiple=0, close_price=0)
# ESKİ (v4): close_trade(id, close_price, pnl, reason, hold_min)  ← KALDIRILDI
```

### 5. Ghost Learning
```python
save_scalp_signal(data, decision=decision)  # decision dışarıdan geçilir
# Asla: save_scalp_signal(data, decision="ALLOW")  ← hardcode yasak
```

### 6. Config'den Oku, os.getenv Kullanma
Hiçbir dosya doğrudan `os.getenv()` çağırmamalı.
Tüm sabitler `config.py` içinden import edilmeli.

---

## Import Sağlığı (14/14 OK — 2026-05-07)

```
config              OK
database            OK
core.accounting     OK
core.trigger_engine OK
core.risk_engine    OK
core.trend_engine   OK
core.ai_decision_engine OK
core.market_scanner OK
execution_engine    OK
telegram_delivery   OK
signal_engine       OK
ml_signal_scorer    OK
ai_brain            OK
dashboard_service   OK
```

Kontrol komutu:
```bash
cd /home/user/trade-engine
python3 -c "
import sys; sys.path.insert(0, '.')
for m in ['config','database','core.accounting','core.trigger_engine',
          'core.risk_engine','core.trend_engine','core.ai_decision_engine',
          'core.market_scanner','execution_engine','telegram_delivery',
          'signal_engine','ml_signal_scorer','ai_brain','dashboard_service']:
    try: __import__(m); print(f'OK  {m}')
    except Exception as e: print(f'ERR {m}: {e}')
" 2>/dev/null
```

---

## DB Migration ve Audit

### Migration Çalıştır
```bash
cd /home/user/trade-engine
python3 scripts/migrate_accounting_schema.py
```
Idempotent — birden fazla kez çalıştırılabilir.

### 12 Noktalı Audit
```bash
python3 scripts/audit_pnl_consistency.py
# Beklenen: 0 ERROR, 3 WARNING (kapalı trade yok, dashboard kapalı, sinyal boş)
```

---

## Tamamlanan Fazlar

### FAZ 1 — Muhasebe Altyapısı ✅
- `core/accounting.py` oluşturuldu (saf formüller, fee double-count önleme)
- `database.py` v5.1'e yükseltildi (70+ idempotent migration)
- `scripts/migrate_accounting_schema.py` genişletildi

### FAZ 2 — Audit ve Veri Bütünlüğü ✅
- `scripts/audit_pnl_consistency.py` (12 kontrol noktası)
- `execution_engine.py` — close_trade() imzası düzeltildi, trade event'leri eklendi
- `app.py` — eksik import'lar düzeltildi, /api/history eklendi

### FAZ 3 — Servis Katmanı ✅ (commit: 4c30148)
- `telegram_delivery.py` — send_tp_hit() eklendi, format güncellendi
- `dashboard_service.py` — bot heartbeat, paper_safety_status, execution_mode
- `core/ai_decision_engine.py` — learn_from_outcome() (8 alan) eklendi
- `core/market_scanner.py` — PERPETUAL filtresi eklendi
- `core/risk_engine.py` — 5 modül-seviyesi risk governor fonksiyonu
- `config.py` — DRY_RUN, LIVE_TRADING_ENABLED, MAX_CONSECUTIVE_LOSSES ekli

---

## Bekleyen İşler (FAZ 4+)

### FAZ 4 — ML Entegrasyonu (ROADMAP Faz A1)
`ml_signal_scorer.py` mevcut pipeline'a **bağlı değil**.
- `TriggerEngine.analyze()` çıktısına `ml_score` alanı eklenecek
- `AIDecisionEngine.evaluate()` içinde `ai_adj` düzeltmesi yapılacak

### FAZ 4 — Live Tracker Postmortem (ROADMAP Faz A2)
`live_tracker.py` verileri `ai_brain.py`'ye geri beslenmemiş.
- `execution_engine._finalize()` içine `live_tracker.record_close()` eklenecek

### FAZ 4 — Çoklu Zaman Dilimi Confluence (ROADMAP Faz B1)
- `core/trend_engine.py` 4 zaman dilimini tek confluence skoruna indirmeli
- Bu skor `trigger_engine.py`'deki kalite kararına dahil edilmeli

---

## Risk Sistemi

### Kalite Bazlı Risk (RiskEngine.calculate)
| Kalite | Risk Çarpanı | Risk % (base=1.0) |
|--------|-------------|-------------------|
| S      | 2.0x        | %2.0              |
| A+     | 1.5x        | %1.5              |
| A      | 1.0x        | %1.0              |
| B      | 0.5x        | %0.5              |
| C/D    | 0           | Trade yok         |

### Risk Governor Fonksiyonları (`core/risk_engine.py`)
```python
check_daily_loss_limit(balance)      # DAILY_MAX_LOSS_PCT=%3
check_consecutive_losses()           # MAX_CONSECUTIVE_LOSSES=3
check_coin_cooldown(symbol)          # coin_cooldown tablosu
check_max_open_trades()              # MAX_OPEN_TRADES=2
check_correlated_exposure(symbol, open_trades)  # MAX_CORRELATED_TRADES=2
# Tümü: True=açılabilir, False=bloke
```

---

## Önemli Config Değerleri

```python
EXECUTION_MODE    = "paper"    # paper | live
PAPER_MODE        = True
DRY_RUN           = True       # False yapma! LIVE_TRADING_ENABLED ile birlikte
LIVE_TRADING_ENABLED = False

SL_ATR_MULT       = 1.2        # Sıkılaştırılmış (1.5'ten)
TP1_R / TP2_R / TP3_R = 1.0 / 2.0 / 3.0
TP1_CLOSE_PCT     = 30         # %30 kapat
TP2_CLOSE_PCT     = 50         # %50 kapat
RUNNER_CLOSE_PCT  = 20         # %20 runner
ADX_MIN_THRESHOLD = 28
ALLOWED_QUALITIES = ["S", "A+"]
DAILY_MAX_LOSS_PCT = 3.0
MAX_OPEN_TRADES   = 2
```

---

## Deployment

### Systemd Servisleri
```bash
systemctl status aurvex-bot        # Ana bot
systemctl status aurvex-dashboard  # Flask API
systemctl status aurvex-watchdog   # Watchdog
```

### Bot Heartbeat Kontrolü
`dashboard_service.get_ax_status()` → `bot_running` alanı.
`system_state` tablosunda `bot_heartbeat_at` < 120 saniye ise canlı.

### DB Yolu
```python
DB_PATH = os.getenv("DB_PATH", "/root/trade_engine/trading.db")
```

---

## Sık Yapılan Hatalar

| Hata | Çözüm |
|------|-------|
| `no such column: total_fee` | `python3 scripts/migrate_accounting_schema.py` çalıştır |
| `close_trade()` yanlış sıra | Keyword arg kullan: `close_trade(id, net_pnl=x, total_fee=y, reason=z)` |
| Import zinciri kopuk | `python3 -c "import database"` ile test et |
| `_TRADE_COLUMNS` cache eski | `database._TRADE_COLUMNS = None` ile sıfırla |
| coin_profiles yok | Migration çalıştır — tablo otomatik oluşturulur |
| QUARTERLY kontrat tarama | `scan()` zaten `contractType=PERPETUAL` filtreler |

---

## Branch Politikası

**Geliştirme branchi:** `claude/audit-fix-codebase-Uf2IF`
```bash
git push -u origin claude/audit-fix-codebase-Uf2IF
```
`main`'e asla doğrudan push yapma.
