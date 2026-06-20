# LOOP 1 RAPORU — Sade & Kâra-Odaklı Engine

> Branch: `claude/beautiful-bardeen-g2bkmk` (oturum mandatlı branch; görev dokümanı
> `loop1-cleanup` adını öneriyordu — aynı kısıt: **main'e merge YOK**, Batuhan onaylar).
> Kapsam: yalnız paper/shadow + geri-alınabilir karantina + **ölç-raporla** (davranış değişmez).
> Canlı trade / API key / live gate'e **dokunulmadı** (`git status` ile doğrulandı).

## Karantinaya alınan (geri alınabilir — `git mv`, R100 saf taşıma)
`archive/dead_code_loop1/` altına taşındı; canlı import grafiğinde **0 referans**:

| Dosya | Satır | Neden ölü |
|---|---:|---|
| `scalp_bot.py` | 1014 | Eski monolit. Canlı yolda 0 Python import; yalnız string/yorum/proses-adı referansları (`pgrep -f scalp_bot.py`, `sig.source="scalp_bot"`, docstring). |
| `ml_signal_scorer.py` (kök) | 421 | `core/ml_signal_scorer.py` ile duplike. **Tüm** canlı kod `core.ml_signal_scorer` kullanıyor; kök modülün 0 importer'ı var. |
| `live_tracker.py` (kök) | 173 | Tek referans `scalp_bot.py`'deki bir **yorum** satırı (o da karantinada). Canlı import yok. |
| `ghost_learner.py` (kök) | 641 | 0 referans. Canlı yol farklı-adlı `core.ghost_learning` kullanır (DOKUNULMADI). |
| **Toplam** | **2249** | Canlı ağaçtan çıkarılan satır. |

## Geri alınan (verify_later)
- **Yok.** 4 taşımanın hiçbiri import/collection kırmadı.

## Funnel (son 24s)
- Bu **ephemeral ortamda canlı funnel verisi YOK**: taze `trading.db`'de `signal_events`
  tablosu yok (hiç engine çalışmamış) → `funnel_report.py` temiz mesajla `0 events` döndü.
- `scripts/funnel_report.py` **uçtan uca doğrulandı**: gerçek writer `save_signal_event`
  ile tohumlanan geçici DB'de stage sayıları + top reject reasons doğru raporlandı
  (örn. 97 olay → SCANNED 40, TREND_REJECTED 18, TRIGGER_REJECTED 8, AI_VETOED 4 …).
- Script **read-only**; engine davranışını değiştirmez.
- Template'ten 3 zorunlu düzeltme (yoksa repoda çalışmaz):
  `event_type → stage` (gerçek kolon), `conn=get_conn() → with get_conn()` (@contextmanager),
  ISO-8601 sınır doğruluğu için `datetime(created_at) >= datetime('now', ?)`.

## Trade-starvation kök sebep ölçümü
- **base TRADE_THRESHOLD:** `45.0` (scalp; `SCALP_MODE` prod varsayılanı `True`) / `55.0` (non-scalp).
- **canlı trade_threshold (bu ortam):** set DEĞİL — Redis `None`, `system_state` tablosu yok →
  engine base 45.0'a düşer. **Bu ortamda ghost-lock yok** (engine hiç çalışmamış).
- **Eşik floor çelişkisi — DOĞRULANDI (yapısal):**
  - `core/ai_decision_engine.py:352` → `new_threshold = max(60.0, old_threshold - 2.0)` (floor **60**).
  - `DynamicThresholds["trade"]` okurken `config.TRADE_THRESHOLD` döner; yazım (`__setitem__`)
    yalnız in-memory fallback'e gider ve config tarafından gölgelenir. Değer asıl olarak
    `system_state` + Redis'e yazılır (satır 378–389) ve `TRADE_THRESHOLD↔trade_threshold`
    **Redis-first dinamik param** olduğu için `config.TRADE_THRESHOLD`'a 60 olarak geri döner.
  - Sonuç: floor (60) base'in (45) **ÜSTÜNDE**. Scalp'te ghost-tuner ilk "daha çok trade"
    (gwr>0.65) kararında eşiği `max(60, 45-2)=60`'a **yükseltir** (niyetin tersi, +15 daha katı)
    ve ≥60'a kilitler — base 45'e bir daha inemez → trade kısılır.
- **TREND_REJECTED payı:** bu ortamda **ölçülemez** (0 olay). Yapısal düşürme yolu doğrulandı:
  `core/services/trend_service.py:29` `direction == "NO TRADE"` → `TREND_REJECTED/NO_TRADE`.
  Kaynak: `TrendEngine.analyze` (`core/trend_engine.py`) rejim/RSI sınıflandırıcısı (Loop 2 hedefi).

## Telegram duplicate-sender
- **Otomatik sender çifti YOK** — kanonik tek kaynak: `core/services/notification_service.py`
  event-driven → `telegram_delivery.send_trade_open` (L79-80) / `send_trade_close` (L108-109).
- `telegram_delivery.py` = formatter + sender (`format_trade_open` tek kaynak).
- `telegram_manager.py` = gelen komut/UI; giden mesaj için **delege eder** (`format_trade_open`'u
  import eder, enjekte edilen `send_fn`'i kullanır); kendi `send_trade_open/close`'u **yok**.
- `execution_engine.py`'de `send_trade_open` **bilerek yok** (`verify_fixes.py` bunu assert eder) —
  önceki fazda yapılmış de-dup hâlâ korunuyor.
- **Potansiyel çift-emit (Loop 2, davranışsal):** manuel force-open callback'i
  (`telegram_manager.py:633-635`) `send_fn(format_trade_open(...))` ile bir "açıldı" teyidi
  gönderir; aynı manuel açılış pipeline TRADE_OPENED event'ini de tetiklerse `NotificationService`
  de gönderir → tek açılışa iki mesaj. Hangisinin kanonik olduğu davranışsal → Loop 2.

## Testler
- `pytest --collect-only` before/after **temiz**: her ikisinde de `403 tests collected, 1 error`
  (tek fark zamanlama satırı 8.57s→5.67s, gürültü). Yeni hata yok.
- Hedefli runtime testleri (`test_scalp_filters`, `test_pipeline`, `test_ml_features`):
  4 dosya **yerinde** ve **karantinada** iken **birebir aynı** sonuç (7 failed / 2 passed).
  Hatalar `no such table: trades` — bu ephemeral container'da DB şeması init edilmemiş;
  **taşımalardan bağımsız, önceden var olan ortam sorunu**. Taşımalarım 0 yeni hata getirdi.

## Kalan riskler
- **Import-time side-effect:** `async_scalp_engine`/`app` import edilince DB erişimi
  (`no such table: system_state`) + Redis logger init tetikleniyor — kod kokusu, Loop 2 adayı.
- **Pre-existing collection error:** `archive/test_system_complete.py` kök `test_system_complete.py`
  ile aynı basename → tam collection'ı kesiyor (benim değişikliğim değil; Loop 2'de
  `--ignore=archive` / `norecursedirs` ile çözülebilir).

## Sistem şu an paper'da trade açabilir mi?
- **Yapısal olarak evet** (paper + `CONFIRMATION_MODE=False` → otonom), AMA kapılar:
  engine süreci çalışmalı + funnel geçilmeli.
- **Bu ephemeral ortamda: HAYIR** — engine çalışmıyor, taze DB (şema/state yok), Redis kapalı.
- Canlı bir kurulumda ghost-tuner bir kez tetiklenirse eşik floor çelişkisi trade'i **kısar**
  (tamamen bloklamaz).

## Loop 2 önerilen hedefler (sıralı)
1. **Eşik floor düzeltmesi** (`core/ai_decision_engine.py:352`): floor'u base ile hizala
   (scalp-aware) veya ghost-tuner'ı paper/scalp'te yalnız-gözlem moduna al.
2. **Rejim sınıflandırıcı NO-TRADE/RSI sınır bug'ı** (`core/trend_engine.py` `analyze`) —
   funnel'da TREND_REJECTED baskınsa.
3. **Trade-open emit konsolidasyonu**: kanoniği seç (NotificationService vs manuel teyit) →
   manuel açılışta çift mesajı önle.
4. Engine/app **import-time DB/Redis side-effect**'lerini kaldır.
5. `archive/test_system_complete.py` duplicate-basename collection kırılmasını çöz.
