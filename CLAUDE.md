# CLAUDE.md — AurvexAI Trade Engine

Gelecekteki Claude Code oturumları ve geliştiriciler için repo rehberi.
Otonom, hesap verebilir, production-grade bir kripto scalp trading platformu.

## Mimari — İKİ AYRI SÜREÇ

Sistem iki bağımsız Python süreci olarak çalışır (paylaşılan durum: SQLite + Redis):

| Süreç | Komut | Görevi |
|---|---|---|
| **Engine** | `python async_scalp_engine.py` | Trading motoru — tarar, sinyal üretir, **trade'leri açar/yönetir**, Friday/Ghost/Watchdog loop'ları |
| **Dashboard** | `python app.py` | Flask + SocketIO web arayüzü (`:5000`) — yalnız okur/komut iletir, trade AÇMAZ |

> ⚠️ **Kritik:** Dashboard tek başına çalışırken Komuta Merkezi nabzı `SORUN` (ENGINE/WEBSOCKET kırmızı) gösterir — bu doğrudur, engine süreci çalışmıyordur. Trade açılması için **engine süreci çalışmalıdır.**

## Trade Açma Yolu (pipeline) — KİM AÇAR?

Trade'leri **Friday açmaz**; event-driven pipeline açar:

```
ScannerService → SCANNED → TrendService → TREND_CHECKED → TriggerService
  → AIDecisionService (skor/kalite) → RiskService (korelasyon, VaR, ML gating)
  → ExecutionService → execution_engine.open_paper_trade()
```

Bir trade'in açılması için TÜM şu kapıların geçilmesi gerekir:
1. **Engine çalışıyor** olmalı (heartbeat canlı).
2. Mod: varsayılan `paper` + `CONFIRMATION_MODE=False` → paper'da risk kalkanları bypass → **otonom açılır** (manuel onay gerekmez). `confirmation_mode=true` ise her sinyal Telegram onayı bekler.
3. Sinyal funnel'ı geçer: `trade_threshold` + kalite (`EXECUTABLE_QUALITIES`) + trend + trigger + RSI/CVD + rejim + korelasyon + VaR + ML olasılık + **fiyat tazeliği guard'ı** (>120sn bayat fiyatla açılmaz).
4. Engelleyiciler kapalı: kill switch, circuit breaker, drawdown lock, coin cooldown, `is_paused`.

## Friday'in Rolü (CEO/Operatör — trader DEĞİL)

`core/friday_ceo.py`. Friday trade **açmaz**; sistemi **çalışır ve kârlı tutar**:
- Parametre ayarı (`trade_threshold`, `risk_pct`) — **param_gate** simülasyonundan geçer (backtest kanıtı yoksa uygulanmaz).
- Pause/resume, coin cooldown, otonom SysAdmin (sinyal kuraklığı, hata fırtınası, drawdown eskalasyonu).
- Her kararı `friday_decisions` tablosuna loglar; 24/72h sonra `outcome_score` ile sonucu ölçülür.
- Gemini/Anthropic **function calling** ile yapılandırılmış karar; LLM yoksa offline kural motoru.
- Reddedilen öneriler **shadow A/B** (`shadow_evaluations`) ile "uygulasaydık ne olurdu" diye izlenir.

## Komutlar

```bash
# Tüm stack (engine + dashboard + redis + prometheus + grafana)
docker compose up -d
docker compose up -d engine          # yalnız engine

# Yerel çalıştırma
python async_scalp_engine.py         # ENGINE — trade açan süreç
python app.py                        # Dashboard (:5000)

# Testler (CI bunu koşar)
python -m pytest tests/ -q

# Migration'lar (idempotent)
python scripts/migrate_friday_decisions.py
python scripts/migrate_daily_summary.py
python scripts/migrate_tenant_id.py
```

## Önemli Modüller

| Alan | Dosya |
|---|---|
| Config (Redis-first dinamik param) | `config.py` |
| DB katmanı (WAL, tek yazıcı `update_system_state`) | `database.py` |
| Hesap/PnL/**expectancy** | `core/accounting.py` |
| Param doğrulama kapısı (backtest gate) | `core/param_gate.py` |
| Live-readiness 5 kapı | `core/live_readiness.py` |
| Friday CEO | `core/friday_ceo.py`, `core/friday_decisions.py`, `core/shadow_eval.py` |
| Pipeline servisleri | `core/services/*.py` |
| Telegram (merkezi şablonlar) | `telegram_delivery.py`, `telegram_manager.py` |
| Dashboard servis + Komuta Merkezi | `dashboard_service.py`, `static/js/command_center.js` |

## Konvansiyonlar (ZORUNLU)

1. **DB yazımı tek noktadan:** `system_state` daima `database.update_system_state()` üzerinden (SQLite → Redis senkronu). Doğrudan SQL yazma yok.
2. **Bağlantı disiplini:** SQLite için `database.get_conn()` / `open_db()` (WAL + busy_timeout). Doğrudan `sqlite3.connect` yalnız migration scriptlerinde.
3. **Şema değişikliği:** önce `scripts/`'e idempotent migration (`CREATE TABLE IF NOT EXISTS` / `ALTER ... try/except`) + `init_db()`'ye aynı şema (tek kaynak DDL sabiti).
4. **Silme yok:** dosyalar `archive/` altına taşınır.
5. **Türkçe gerekçe yorumu:** kritik değişikliklere `# NEDEN: ...`.
6. **Para/yüzde/R formatı sabit:** `$1,234.50` / `+1.57%` / `+1.27R` (`telegram_delivery.fmt_*`).

## Sayı Formatı & Renk Anlamı (UI)
- Altın = kâr/aktif, bakır-kırmızı `#c0533e` = zarar/risk, gümüş-mavi `#7e9cc0` = SHORT/nötr, soluk yeşil `#5fa97a` = sağlık.

## Faz Haritası (tamamlandı)
1. Kararlılık (Redis-first config, WAL, chaos, eventlet→threading)
2. Friday 2.0 (karar günlüğü, function calling, otonom ops, sabah brifingi)
3. Kanıta dayalı (expectancy, backtest gate, live-readiness)
4. Dashboard Komuta Merkezi (tek sayfa, 4 katman)
5. Telegram şablon sistemi + komut UX
6. Ekstralar (trade journal, tenant_id, korelasyon, setup replay, shadow A/B, Grafana, funding hunter)

Detaylı plan: `ROADMAP.md` / `docs/SCHEMA_NOTES.md`.

## Live Moda Geçiş
`/live` komutu **5 kapı geçilmeden izin vermez** (30g ≥100 trade, 30g & 14g expectancy>0, max DD<%8, uptime≥%99, manuel onay). `/live force` = boss bypass. Varsayılan `paper`.
