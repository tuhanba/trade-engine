# AURVEX AI Trade Engine — Claude Code Rehberi
> **Son güncelleme:** 2026-05-21
> **Durum:** Ghost Learning 2.0 aktif — Paper pipeline çalışıyor
> **Sunucu yolu:** `/root/trade_engine/trade-engine/`
> **Venv:** `/root/trade_engine/trade-engine/.venv/bin/python`

---

## 1. Proje Yapısı

```
trade-engine/
│
├── config.py                    # Tüm sabitler — os.getenv YASAK, buradan oku
├── database.py                  # SQLite katmanı v5.2 — tek DB giriş noktası
├── scalp_bot.py                 # Ana bot döngüsü (çalıştırılan dosya)
├── execution_engine.py          # Trade aç / kapat / yönet
├── signal_engine.py             # Sinyal üretimi (EMA+RSI+MACD+Volume)
├── ai_brain.py                  # Gece 03:00 UTC nightly optimizer
├── ghost_learner.py             # Ghost Learning scheduler (manuel/cron)
├── ml_signal_scorer.py          # Voting Ensemble RF+GB
├── app.py                       # Flask dashboard (port 5000)
├── dashboard_service.py         # /api/* veri katmanı
├── telegram_delivery.py         # Bildirim formatlama + gönderim
│
├── core/
│   ├── accounting.py            # PnL/fee formülleri — DOKUNMA
│   ├── ai_decision_engine.py    # ALLOW/VETO + ghost hook + paper öğrenim
│   ├── ghost_learning.py        # Ghost Learning 2.0 motoru
│   ├── paper_tracker.py         # Paper simülasyonu (v5.2)
│   ├── market_scanner.py        # PERPETUAL coin tarama
│   ├── risk_engine.py           # 5 risk governor fonksiyonu
│   ├── trigger_engine.py        # Kalite sınıfı: S/A+/A/B/C/D
│   ├── trend_engine.py          # Multi-TF trend analizi
│   ├── trailing_engine.py       # Trailing SL yönetimi
│   ├── signal_intelligence.py   # Sinyal kalite analizi
│   ├── watchdog.py              # Bot sağlık izleme
│   └── data_layer.py            # SignalData / TradeData dataclass'ları
│
├── scripts/
│   ├── migrate_accounting_schema.py
│   ├── audit_pnl_consistency.py
│   ├── backtest_engine.py
│   └── monitor_paper_run.py
│
├── .claude/agents/              # Claude Code agent'ları
│   ├── ai-brain.md              # Nightly optimizer agent
│   ├── ghost-learner.md         # Ghost Learning 2.0 agent
│   └── signal-analyst.md        # Signal quality agent
│
├── aurvex-bot.service           # Systemd — scalp_bot.py  ← KULLAN
├── aurvex-dashboard.service     # Systemd — app.py        ← KULLAN
└── requirements.txt
```

---

## 2. Kritik Tasarım Kuralları

### 2.1 PnL Formülü (core/accounting.py — KESİNLİKLE DOKUNMA)
```python
# LONG:  pnl = (exit_price - entry_price) × qty - fee
# SHORT: pnl = (entry_price - exit_price) × qty - fee
from core.accounting import calculate_realized_pnl, calculate_partial_close_pnl
```

### 2.2 Config Kuralı — os.getenv Yasak
```python
# DOĞRU:
from config import SL_ATR_MULT, PAPER_MODE, TRADE_THRESHOLD
# YANLIŞ:
import os; sl = float(os.getenv("SL_ATR_MULT", 1.2))
```

### 2.3 database.py — Fonksiyon İmzaları
```python
close_trade(trade_id, net_pnl=x, total_fee=y, reason=z, close_price=p)
save_paper_result(data_dict)
update_paper_result(id, updates_dict)
get_pending_paper_results(limit=35)
# Migration sonrası cache sıfırla:
import database; database._TRADE_COLUMNS = None
```

### 2.4 Ghost Learning — Decision Dışarıdan Gelir
```python
# DOĞRU:
save_scalp_signal(data, decision=decision)
# YANLIŞ — hardcode yasak:
save_scalp_signal(data, decision="ALLOW")
```

### 2.5 aurvex-dashboard DOKUNMA
`aurvex-dashboard` servisini değişiklik sırasında **asla** yeniden başlatma.
Sadece `aurvex-bot` restart edilir.

---

## 3. Ghost Learning 2.0 Mimarisi

### Veri Akışı
```
scalp_bot → classify_signal() → VETO
                ↓
          maybe_ghost_log()         [core/ghost_learning.py]
                ↓
          save_ghost_signal()        [database.py]
                ↓
          ghost_signals tablosu
                ↓
          _ghost_worker thread       [scalp_bot.py:199]
          (her 5 dakikada bir)
                ↓
          simulate_pending_ghosts()  [core/ghost_learning.py]
                ↓
          ghost_results tablosu
                ↓
          generate_threshold_suggestions()
                ↓
          ghost_suggestions tablosu
                ↓
          nightly_optimize_coins()   [ai_brain.py]
          apply_ghost_suggestions()
                ↓
          coin_configs tablosu       ← confidence_cutoff güncellendi
```

### Tablolar
```sql
ghost_signals     -- Reddedilen sinyaller (ham)
ghost_results     -- Simülasyon sonuçları (WIN/LOSS/OPEN)
ghost_suggestions -- AI Brain'e threshold önerileri
coin_configs      -- Coin başına parametre (confidence_cutoff, sl_atr, tp_atr)
```

### Test
```bash
# Ghost signals birikmiş mi?
python3 -c "
from database import get_conn
with get_conn() as c:
    print('ghost_signals:', c.execute('SELECT COUNT(*) FROM ghost_signals').fetchone()[0])
    print('simulated:', c.execute('SELECT COUNT(*) FROM ghost_signals WHERE simulated=1').fetchone()[0])
    print('ghost_results:', c.execute('SELECT COUNT(*) FROM ghost_results').fetchone()[0])
"

# Manuel ghost cycle
python3 ghost_learner.py cycle
```

---

## 4. Paper Trade Pipeline

### Sinyal Akış Şeması
```
market_scanner → trigger_engine → ai_decision_engine
                                        │
                    VETO ───────────────┤→ ghost_signals (maybe_ghost_log)
                                        │→ paper_results (PAPER_TRACK_REJECTED)
                    ALLOW ──────────────┤→ execution_engine.open_trade()
```

### Paper Results Yaşam Döngüsü
```
save_paper_result()  → status='pending'
        ↓
process_pending_paper_results()  (her bot iterasyonunda)
        ↓
finalize_paper_row() → Binance kline → _simulate_path()
        ↓
update_paper_result() → status='completed'
        ↓
AIDecisionEngine.learn_from_paper_outcome()
```

---

## 5. Import Sağlığı (2026-05-21)

```bash
python3 -c "
mods = ['config','database','core.accounting','core.paper_tracker',
        'core.ghost_learning','core.ai_decision_engine',
        'execution_engine','signal_engine','ai_brain',
        'dashboard_service','ml_signal_scorer','ghost_learner']
for m in mods:
    try: __import__(m); print(f'OK  {m}')
    except Exception as e: print(f'ERR {m}: {e}')
"
```

---

## 6. Deployment

```bash
# Kod güncelle
cd /root/trade_engine/trade-engine
git pull origin main

# Migration
source .venv/bin/activate
python3 scripts/migrate_accounting_schema.py

# Sadece bot restart (dashboard dokunma)
systemctl restart aurvex-bot
journalctl -u aurvex-bot -n 50 --no-pager
```

---

## 7. Risk Sistemi

| Kalite | Risk Çarpanı |
|---|---|
| S | 2.0x |
| A+ | 1.5x |
| A | 1.0x |
| B | 0.5x |
| C / D | Trade açılmaz |

```python
# 5 risk governor (core/risk_engine.py):
check_daily_loss_limit(balance)       # DAILY_MAX_LOSS_PCT=5.0
check_consecutive_losses()            # CIRCUIT_BREAKER_LOSSES=3
check_coin_cooldown(symbol)
check_max_open_trades()               # MAX_OPEN_TRADES=5
check_correlated_exposure(symbol, open_trades)
```

---

## 8. Config Referansı

```python
EXECUTION_MODE    = "paper"   # paper | live
TRADE_THRESHOLD   = 72.0      # Bu altı trade açma
TELEGRAM_THRESHOLD= 65.0
WATCHLIST_THRESHOLD=60.0
DATA_THRESHOLD    = 55.0
SL_ATR_MULT       = 1.2
TP1_R = 1.0  TP2_R = 2.0  TP3_R = 3.0
GHOST_WEIGHT      = 0.30
PAPER_TRACK_REJECTED_CANDIDATES = True
PAPER_TRACK_HORIZON_HOURS       = 8.0
```

---

## 9. Sık Hatalar

| Hata | Çözüm |
|---|---|
| `no such table: paper_results` | `python3 -c "import database; database.init_db()"` |
| `no such column: total_fee` | `python3 scripts/migrate_accounting_schema.py` |
| `_TRADE_COLUMNS cache eski` | `python3 -c "import database; database._TRADE_COLUMNS = None"` |
| `invalid character '—'` | `sed -i 's/[""]/"/g' file.py` |
| Bot yanlış dizin | `WorkingDirectory=/root/trade_engine/trade-engine` |
| Duplicate process | `pkill -f scalp_bot.py && systemctl start aurvex-bot` |
| ghost collect returns 0 | Normal — data classify_signal() hook'tan direkt geliyor |

---

## 10. Agent Görev Bölümü

Claude Code oturumu açıldığında:

```bash
# 1. Import sağlığı
python3 -c "import config,database,core.paper_tracker,execution_engine,ai_brain; print('OK')"

# 2. Ghost durumu
python3 -c "
from database import get_conn
with get_conn() as c:
    gs = c.execute('SELECT COUNT(*),SUM(simulated) FROM ghost_signals').fetchone()
    gr = c.execute('SELECT COUNT(*),AVG(virtual_pnl_r) FROM ghost_results').fetchone()
    print(f'Ghost signals: {gs[0]} ({gs[1]} simulated)')
    print(f'Ghost results: {gr[0]} avg_r={gr[1]:.2f}' if gr[0] else 'Ghost results: 0')
"

# 3. Paper durumu
python3 -c "
from database import get_conn
with get_conn() as c:
    r = dict(c.execute('SELECT status,COUNT(*) FROM paper_results GROUP BY status').fetchall() or [])
    print('Paper results:', r)
"
```

---

## 11. Roadmap

### ✅ Tamamlanan
- [x] Ghost Learning 2.0 DB fonksiyonları
- [x] classify_signal() → ghost hook
- [x] ghost_worker thread (scalp_bot'ta)
- [x] nightly_optimize_coins() ghost suggestion reader
- [x] coin_configs tablosu (get_coin_config / save_coin_config)

### 🔄 Devam Eden
- [ ] ghost_learner.py collect_ghosts() — signal_candidates yerine ghost_signals'ı baz al
- [ ] ml_signal_scorer pipeline entegrasyonu (standalone, bağlı değil)
- [ ] Multi-TF confluence (trend_engine → trigger_engine)

### 📋 Gelecek
- [ ] Live trading geçiş (ön koşul: 500+ paper result, WR > %52)
- [ ] Dashboard ghost stats widget
