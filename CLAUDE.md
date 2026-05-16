# AURVEX AI Trade Engine — Claude Code Rehberi
> **Son güncelleme:** 2026-05-16
> **Durum:** 19/19 modül OK — Paper trade pipeline tam çalışır
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
├── app.py                       # Flask dashboard (port 5000)
├── dashboard_service.py         # /api/* veri katmanı
├── telegram_delivery.py         # Bildirim formatlama + gönderim
├── ml_signal_scorer.py          # Voting Ensemble RF+GB
│
├── core/
│   ├── accounting.py            # PnL/fee formülleri — DOKUNMA
│   ├── ai_decision_engine.py    # ALLOW/VETO + paper öğrenim
│   ├── paper_tracker.py         # Paper simülasyonu (v5.2)
│   ├── ghost_learning.py        # Açılmayan sinyal takibi
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
│   ├── migrate_accounting_schema.py   # DB migration (idempotent)
│   ├── audit_pnl_consistency.py      # 12 noktalı audit
│   ├── backtest_engine.py            # Tarihsel backtest
│   └── monitor_paper_run.py          # Paper run izleme
│
├── aurvex-bot.service           # Systemd — scalp_bot.py  ← KULLAN BUNU
├── aurvex-dashboard.service     # Systemd — app.py
├── .env.example                 # Tüm env değişkeni şablonu
└── requirements.txt             # Python bağımlılıkları
```
---
## 2. Kritik Tasarım Kuralları
### 2.1 PnL Formülü (core/accounting.py — KESİNLİKLE DOKUNMA)
```python
# LONG:  pnl = (exit_price - entry_price) × qty - fee
# SHORT: pnl = (entry_price - exit_price) × qty - fee
# Kaldıraç PnL'i ÇARPMAZ — sadece margin/pozisyon büyüklüğünü etkiler
from core.accounting import calculate_realized_pnl, calculate_partial_close_pnl
```
Her PnL hesabı bu modülden geçer. Asla inline `(exit - entry) * qty` yazma.
### 2.2 Config Kuralı — os.getenv Yasak
```python
# DOĞRU:
from config import SL_ATR_MULT, PAPER_MODE, TRADE_THRESHOLD
# YANLIŞ — asla yapma:
import os
sl = float(os.getenv("SL_ATR_MULT", 1.2))
```
### 2.3 database.py v5.2 — Fonksiyon İmzaları
```python
# Trade kapama — keyword arg zorunlu (sıra karışmasın):
close_trade(trade_id, net_pnl=x, total_fee=y, reason=z, close_price=p)
# Paper results CRUD:
database.save_paper_result(data_dict)           # status='pending' olarak kaydeder
database.update_paper_result(id, updates_dict)  # status='completed' yapar
database.get_pending_paper_results(limit=35)    # finalize edilecek satırlar
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
### 2.5 paper_tracker — SL Önceliği (Conservative Assumption)
`_resolve_bar()` içinde bir mum hem SL hem TP'ye aynı anda değerse **SL önce
vurulmuş sayılır**. Bu worst-case tasarımıdır. Değiştirme.
---
## 3. Paper Trade Pipeline (v5.2)
### 3.1 Sinyal Akış Şeması
```
market_scanner.scan()
        │
        ▼ candidates listesi
trigger_engine.analyze()           → quality: S / A+ / A / B / C / D
        │
        ├─ quality C/D veya risk bloke
        │       └─► save_paper_result(tracked_from="candidate")   [PAPER TRACK]
        │
        ▼
ai_decision_engine.evaluate()      → ALLOW / VETO
        │
        ├─ VETO
        │       └─► save_paper_result(tracked_from="candidate")   [PAPER TRACK]
        │
        ├─ score < TELEGRAM_THRESHOLD (65)
        │       └─► save_paper_result(tracked_from="watchlist")   [PAPER TRACK]
        │
        ├─ Telegram gönderildi ama score < TRADE_THRESHOLD (72)
        │       └─► save_paper_result(tracked_from="telegram_gap")[PAPER TRACK]
        │
        ▼
execution_engine.open_trade()      → gerçek trade açılır
```
### 3.2 Paper Result Yaşam Döngüsü
```
scalp_bot.py ana döngüsü her iterasyonda:
    process_pending_paper_results(client, limit=30)
            │
            ▼
    finalize_paper_row(client, row)
            │
            ├─ client.futures_klines(symbol, interval="1m", ...)
            │
            ▼
    _simulate_path(klines, direction, entry, sl, tp1, start_ms, horizon_min)
            │
            ├─ LONG TP1 vuruldu  → first_touch="tp1",   hit_tp=1
            ├─ SL vuruldu önce   → first_touch="stop",  hit_stop_first=1
            └─ Horizon doldu     → first_touch="neither_horizon"
            │
            ▼
    update_paper_result(id, {hit_tp, hit_stop_first, ttm, mfe_r, mae_r,
                              setup_worked, would_have_won, status="completed"})
            │
            ▼
    AIDecisionEngine.learn_from_paper_outcome(...)   ← AI feedback loop
```
### 3.3 Paper Results Tablosu (v5.2'de init_db()'ye eklendi)
```sql
CREATE TABLE IF NOT EXISTS paper_results (
    id                      INTEGER PRIMARY KEY AUTOINCREMENT,
    signal_id               TEXT,
    candidate_id            TEXT,
    symbol                  TEXT NOT NULL,
    direction               TEXT NOT NULL,          -- LONG | SHORT
    preview_entry           REAL DEFAULT 0,
    preview_sl              REAL DEFAULT 0,
    preview_tp1             REAL DEFAULT 0,
    preview_tp2             REAL DEFAULT 0,
    preview_tp3             REAL DEFAULT 0,
    tracked_from            TEXT DEFAULT 'candidate', -- candidate|watchlist|telegram_gap
    horizon_minutes         REAL DEFAULT 240,
    reject_reason_snap      TEXT DEFAULT '',
    final_score_snap        REAL DEFAULT 0,
    leverage_hint           INTEGER DEFAULT 10,
    hit_tp                  INTEGER DEFAULT 0,
    hit_stop_first          INTEGER DEFAULT 0,
    time_to_move_minutes    REAL DEFAULT 0,
    max_favorable_excursion REAL DEFAULT 0,         -- MFE, R cinsinden
    max_adverse_excursion   REAL DEFAULT 0,         -- MAE, R cinsinden
    setup_worked            INTEGER DEFAULT 0,
    would_have_won          INTEGER DEFAULT 0,
    first_touch             TEXT DEFAULT '',
    skip_decision_correct   INTEGER DEFAULT 0,
    status                  TEXT DEFAULT 'pending', -- pending | completed
    finalized_at            TEXT,
    created_at              TEXT DEFAULT (datetime('now'))
)
```
> **Önemli:** Bu tablo artık `init_db()` ile otomatik oluşturuluyor.
> Eski DB'lerde yoksa: `python3 -c "import database; database.init_db()"`
---
## 4. Import Sağlığı (2026-05-16 — 19/19 OK)
```
config                   OK
database                 OK   ← v5.2: paper_results + signal_events DDL eklendi
core.accounting          OK
core.trigger_engine      OK
core.risk_engine         OK
core.trend_engine        OK
core.ai_decision_engine  OK
core.market_scanner      OK
execution_engine         OK
telegram_delivery        OK
signal_engine            OK
ml_signal_scorer         OK
ai_brain                 OK
dashboard_service        OK
core.paper_tracker       OK   ← merge conflict çözüldü, _simulate_path tam
core.ghost_learning      OK
core.trailing_engine     OK
core.watchdog            OK
core.signal_intelligence OK
```
Hızlı import testi:
```bash
cd /root/trade_engine/trade-engine
source .venv/bin/activate
python3 -c "
import sys; sys.path.insert(0, '.')
mods = ['config','database','core.accounting','core.paper_tracker',
        'execution_engine','signal_engine','ai_brain','dashboard_service']
for m in mods:
    try: __import__(m); print(f'OK  {m}')
    except Exception as e: print(f'ERR {m}: {e}')
"
```
---
## 5. DB Migration ve Audit
### Migration (sunucuda — idempotent)
```bash
cd /root/trade_engine/trade-engine
source .venv/bin/activate
python3 scripts/migrate_accounting_schema.py
python3 -c "import database; database._TRADE_COLUMNS = None; print('Cache sıfırlandı')"
```
### 12 Noktalı Audit
```bash
python3 scripts/audit_pnl_consistency.py
# Beklenen çıktı: 0 ERROR, ≤3 WARNING
```
### Paper Results Durumu
```bash
python3 -c "
import sys; sys.path.insert(0, '.')
import database, config
config.DB_PATH = '/root/trade_engine/trading.db'
conn = database.get_connection()
total = conn.execute('SELECT COUNT(*) FROM paper_results').fetchone()[0]
pending = conn.execute(\"SELECT COUNT(*) FROM paper_results WHERE status='pending'\").fetchone()[0]
done = conn.execute(\"SELECT COUNT(*) FROM paper_results WHERE status='completed'\").fetchone()[0]
wins = conn.execute(\"SELECT COUNT(*) FROM paper_results WHERE would_have_won=1\").fetchone()[0]
print(f'Total: {total}  Pending: {pending}  Done: {done}  Wins: {wins}')
if done > 0: print(f'Winrate: {wins/done*100:.1f}%')
conn.close()
"
```
---
## 6. Deployment
### 6.1 Kod Güncelleme (sunucuda)
```bash
cd /root/trade_engine/trade-engine
git pull origin main
```
```bash
source .venv/bin/activate
python3 scripts/migrate_accounting_schema.py
```
```bash
systemctl restart aurvex-bot
systemctl restart aurvex-dashboard
```
### 6.2 İlk Kurulum (sıfırdan)
```bash
cp aurvex-bot.service /etc/systemd/system/
cp aurvex-dashboard.service /etc/systemd/system/
systemctl daemon-reload
systemctl enable aurvex-bot aurvex-dashboard
systemctl start aurvex-bot aurvex-dashboard
```
### 6.3 Canlı İzleme
```bash
journalctl -u aurvex-bot -f -n 50
journalctl -u aurvex-dashboard -f -n 50
python3 scripts/monitor_paper_run.py 60
```
### 6.4 Servis Dosyası Seçimi
| Dosya | ExecStart | Durum |
|---|---|---|
| `aurvex-bot.service` | `scalp_bot.py` | **KULLAN** — tam paper pipeline |
| `aurvex-dashboard.service` | `app.py` | **KULLAN** — port 5000 |
| `ax-bot.service` | `scalp_bot_v3.py` | ESKİ — kullanma |
| `ax-dashboard.service` | `app.py` | ESKİ — kullanma |
---
## 7. Risk Sistemi
### 7.1 Kalite Bazlı Risk (RiskEngine.calculate)
| Kalite | Risk Çarpanı | Risk % (base=1.0) |
|---|---|---|
| S | 2.0x | %2.0 |
| A+ | 1.5x | %1.5 |
| A | 1.0x | %1.0 |
| B | 0.5x | %0.5 |
| C / D | 0 | Trade açılmaz |
### 7.2 Risk Governor'lar (core/risk_engine.py)
```python
check_daily_loss_limit(balance)                # DAILY_MAX_LOSS_PCT=5.0
check_consecutive_losses()                     # CIRCUIT_BREAKER_LOSSES=3
check_coin_cooldown(symbol)                    # coin_cooldown tablosu
check_max_open_trades()                        # MAX_OPEN_TRADES=5
check_correlated_exposure(symbol, open_trades) # MAX_CORRELATED_TRADES=2
# Tümü: True = trade açılabilir, False = bloke
```
---
## 8. Config Referansı
```python
# ── Mod ──────────────────────────────────────────────────
EXECUTION_MODE           = "paper"   # paper | live
PAPER_MODE               = True
DRY_RUN                  = True      # Canlıya geçene kadar True tut
LIVE_TRADING_ENABLED     = False
# ── Sinyal Eşikleri ──────────────────────────────────────
DATA_THRESHOLD           = 55.0   # Bu altı tamamen atla
WATCHLIST_THRESHOLD      = 60.0   # Bu altı watchlist'e al, paper track et
TELEGRAM_THRESHOLD       = 65.0   # Bu altı Telegram'a gönderme
TRADE_THRESHOLD          = 72.0   # Bu altı trade açma
# ── Strateji ─────────────────────────────────────────────
SL_ATR_MULT              = 1.2    # ATR tabanlı SL (1.5'ten sıkılaştırıldı)
TP1_R                    = 1.0    # 1R'da TP1
TP2_R                    = 2.0    # 2R'da TP2
TP3_R                    = 3.0    # 3R'da TP3
TP1_CLOSE_PCT            = 30     # TP1'de %30 kapat
TP2_CLOSE_PCT            = 50     # TP2'de %50 kapat
RUNNER_CLOSE_PCT         = 20     # %20 runner bırak
ADX_MIN_THRESHOLD        = 28
ALLOWED_QUALITIES        = ["S", "A+", "A", "B"]
MIN_RR                   = 1.5
# ── Risk ─────────────────────────────────────────────────
DAILY_MAX_LOSS_PCT       = 5.0
MAX_OPEN_TRADES          = 5
CIRCUIT_BREAKER_LOSSES   = 3
CIRCUIT_BREAKER_MINUTES  = 60
# ── Paper Tracking (paper modda hepsi True) ───────────────
PAPER_TRACK_REJECTED_CANDIDATES = True
PAPER_TRACK_WATCHLIST           = True
PAPER_TRACK_TELEGRAM_GAPS       = True
PAPER_TRACK_HORIZON_HOURS       = 8.0    # Simülasyon penceresi
# ── Ghost Learning ────────────────────────────────────────
GHOST_WEIGHT             = 0.30   # Ghost sonuçların gerçeğe ağırlığı
```
---
## 9. Sık Karşılaşılan Hatalar
| Hata Mesajı | Çözüm |
|---|---|
| `no such table: paper_results` | `python3 -c "import database; database.init_db()"` |
| `no such column: total_fee` | `python3 scripts/migrate_accounting_schema.py` |
| `close_trade() takes ...` | Keyword arg kullan: `close_trade(id, net_pnl=x, total_fee=y, ...)` |
| `_TRADE_COLUMNS` cache eski | `python3 -c "import database; database._TRADE_COLUMNS = None"` |
| `invalid character '—'` | Termius copy-paste smart-quote sorunu: `sed -i 's/[""]/"/g' file.py` |
| `ModuleNotFoundError: binance` | `pip install python-binance --break-system-packages` |
| Bot yanlış dizinden çalışıyor | `WorkingDirectory=/root/trade_engine/trade-engine` olmalı |
| Duplicate python process | `pkill -f scalp_bot.py` sonra `systemctl start aurvex-bot` |
| `<<<<<<< HEAD` syntax hatası | Merge conflict kaldı — `grep -rn '<<<<<<' .` ile bul, elle çöz |
| `QUARTERLY kontrat tarama` | `market_scanner.scan()` zaten `contractType=PERPETUAL` filtreler |
---
## 10. Yönetici / Ajan Görev Bölümü
Bir Claude Code oturumu açıldığında şu sıraya göre çalış:
### Yönetici (Oturum Başı — Her Zaman)
```bash
# 1. Import sağlığı
python3 -c "import sys; sys.path.insert(0,'.'); [(__import__(m), print('OK',m)) for m in ['config','database','core.paper_tracker','execution_engine','ai_brain']]"
# 2. Audit
python3 scripts/audit_pnl_consistency.py
# 3. Paper results durumu
python3 -c "
import sys; sys.path.insert(0,'.'); import database, config
conn = database.get_connection()
r = conn.execute('SELECT status, COUNT(*) FROM paper_results GROUP BY status').fetchall()
print(dict(r)); conn.close()
"
```
### Ajan 1 — Paper Pipeline Operasyonu
- `summarize_ghost_results()` çalıştır — winrate ve ghost_pnl raporla
- `pending > 200` ise `PAPER_TRACK_HORIZON_HOURS=4.0` yap, yük azalt
- `finalize_paper_row` hataları varsa `symbol` bazlı logla
### Ajan 2 — AI Öğrenme Döngüsü
- `ai_brain.py` nightly log: `/var/log/syslog | grep ai_brain`
- `learn_from_paper_outcome()` çağrı sayısını takip et
- `coin_optimization_results.csv` değişti mi kontrol et
### Ajan 3 — Dashboard & Monitoring
```bash
curl http://localhost:5000/api/health
curl http://localhost:5000/api/stats
```
- `bot_running: true` ve `bot_heartbeat_at < 120sn` olmalı
- `balance_ledger` toplam ile `paper_account.balance` karşılaştır
---
## 11. v5.2 Değişiklik Özeti
### Düzeltilen Sorunlar
| # | Sorun | Etki |
|---|---|---|
| 1 | **16 dosyada Git merge conflict** | Bot tamamen çalışmıyordu |
| 2 | **`paper_results` tablosu CREATE edilmiyordu** | Her paper save `no such table` hatası |
| 3 | **`GHOST_WEIGHT` config'de tanımsızdı** | ghost_learning import'ta hata riski |
### Eklenenler (0797c70b branchi entegrasyonu)
- `core/paper_tracker.py` → `_simulate_path()`, `finalize_paper_row()`, `process_pending_paper_results()` (gerçek Binance kline tabanlı simülasyon)
- `database.py` → `_PAPER_RESULTS_DDL`, `_SIGNAL_EVENTS_DDL`, her ikisi `init_db()`'ye bağlandı
- `core/ai_decision_engine.py` → `learn_from_paper_outcome()` (paper veriden AI öğrenimi)
- `config.py` → `GHOST_WEIGHT` eklendi
---
## 12. Sonraki Fazlar (ROADMAP)
### Faz A1 — ML Pipeline Entegrasyonu
`ml_signal_scorer.py` pipeline'a bağlı değil, standalone.
- `TriggerEngine.analyze()` çıktısına `ml_score: float` alanı ekle
- `AIDecisionEngine.evaluate()` içinde `ml_score`'u `ai_adj` hesabına dahil et
### Faz A2 — Live Tracker Feedback
- `execution_engine._finalize_trade()` içine `live_tracker.record_close()` ekle
- `ai_brain.py` bu veriden öğrensin
### Faz B1 — Çoklu TF Confluence
- `core/trend_engine.py` 4 TF (1m/5m/15m/1h) → tek `confluence_score`
- `trigger_engine.py` kalite kararına bu skoru dahil et
### Faz C — Canlı Tradee Geçiş
**Ön koşul: En az 500 tamamlanmış paper result, winrate > %52**
```bash
# .env içinde:
EXECUTION_MODE=live
LIVE_TRADING_ENABLED=True
DRY_RUN=False
CONFIRM_LIVE_TRADING=True
USE_BINANCE_PRIVATE_API=True
BINANCE_API_KEY=<gerçek_key>
BINANCE_API_SECRET=<gerçek_secret>
```
