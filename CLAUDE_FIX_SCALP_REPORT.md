# CLAUDE_FIX_SCALP_SIMPLIFY — Uygulama Raporu

Hedef: AurvexAI'ı "her şeyi yapmaya çalışan, hiç trade etmeyen" yapıdan sade,
hızlı bir scalp engine'e döndürmek. Yaklaşım dokümandaki felsefeye sadık:
**Beyin SİLİNMEZ, KAPATILIR (mode-gate). Geri dönülebilir.**

Tüm değişiklikler tek bir ana bayrağın (`SCALP_MODE`, env, varsayılan **True**)
arkasında. `SCALP_MODE=false` ile sistem **bit-bit eski "brain" davranışına**
döner (bu testle de doğrulanıyor: `tests/test_scalp_mode.py::test_scalp_mode_off_restores_legacy`).

> Canlı trade **değişmedi**: `LIVE_TRADING_ENABLED=False`, `PAPER_MODE=True`,
> `DRY_RUN=True` aynen korundu. Tüm iş paper modunda.

---

## Kodla uyuşmazlıklar — doküman kısmen güncel değildi

Uygulamadan önce iddialar koda karşı doğrulandı. Önemli bulgular:

1. **FAZ 1.1 / 1.2 (karar-motoru VETO'ları) paper modda ZATEN bypass'lı.**
   `core/ai_decision_engine.py::classify_signal` içinde `bypass_shields = ... or is_paper`
   (paper modda True). Konsensüs ANY-veto, Macro kalkanları, SentimentAgent FNG,
   `friday_macro_paused`, korelasyon kalkanı, choppy-kalite, reputation — hepsi
   paper'da zaten atlanıyor. Bu yüzden bu blok **değiştirilmedi** (canlı beyin +
   testleri korundu). Doküman hedefi (paper scalp trade'i bu VETO'lar öldürmesin)
   zaten karşılanıyordu.

2. **FAZ 1.3 (ML online-prob kapısı) artık sert kapı DEĞİL.** `execution_engine.py`
   içindeki blok trade'i reddetmiyor; yalnız risk çarpanı uyguluyor ("asla blok yok,
   öğrenmek için trade gerekir") ve **zaten `DYNAMIC_THRESHOLD_ENABLED`'a sarılı**.
   FAZ 3.1 ile bu bayrak scalp modda False olduğundan ML bloğu komple atlanıyor —
   ekstra kod gerekmedi.

3. **`RISK_PCT` / `TP2_R` üretimde `params` tablosundan geliyordu** (`_AI_PARAMS_MAP`),
   config literal'inden değil. `init_db` `params`'ı `risk_pct=1.5, tp_atr_mult=2.0`
   ile seed'liyor. Scalp değerleri (0.5 / 1.8) gerçekten etki etsin diye, scalp modda
   bu iki parametre için `params` okuması **bypass** edilip statik scalp varsayılanına
   düşürüldü (DB **mutasyonu yok** → geri dönülebilirlik korunur). `SL_ATR_MULT`
   dokümanda yer almadığından eskisi gibi `params`'tan okunur (1.2).

---

## Faz faz yapılanlar

| Faz | Eylem | Dosya |
|---|---|---|
| **0** | Branch `claude/determined-planck-m4bmhy` (görev gereği); paper/dry-run doğrulandı. DB yedeği gerekmedi (temiz checkout'ta DB yok). | — |
| **1.1** | Execution-engine **Pearson korelasyon blokeri** scalp modda kapatıldı (`not SCALP_MODE`). VaR tavanı korundu. ai_decision VETO'ları zaten paper-bypass (bkz. üst). | `execution_engine.py` |
| **1.3** | ML online-prob kapısı `DYNAMIC_THRESHOLD_ENABLED=False` ile (scalp) komple atlanıyor. | `config.py` (dolaylı) |
| **1.4** | Çelişen tarama filtreleri scalp modda kapalı: `SCALP_CVD_DIVERGENCE`, `CONNORS_RSI`, `ORDER_BOOK_WALL`, `EQUITY_CURVE_FILTER`, `MTF_TREND_ALIGN`, `SESSION_FILTER`, `SHORT_REQUIRES_BTC_BEARISH`. | `config.py` |
| **2** | Scalp kimliği: `MAX_HOLD_MINUTES=25`, `SCALP_TIME_DECAY_BREAKEVEN=8`, `MIN_SL_PCT=0.004`, `TP1_R=1.0`, `TP2_R=1.8`, `TP3_R=3.0`, `TP1/TP2/RUNNER_CLOSE=60/25/15` (toplam 100), `MIN_RR=1.1`, `MIN_EXPECTED_MFE_R=0.8`, `RISK_PCT=0.5`, `TRAIL_ATR_MULT=1.0`, `MIN_MOVE_PCT=0.2`. | `config.py` |
| **3.1** | `DYNAMIC_THRESHOLD_ENABLED=False` (scalp). `__getattr__` rejim/RL/starvation skalalaması bu bayrağa sarıldı — kapalıyken atlanır (manuel `/set` DB okuması korunur). | `config.py` |
| **3.2** | `SELF_HEALING_AUTO_APPLY`, `GHOST_WARMUP_ENABLED`, `DYNAMIC_KELLY_ENABLED` zaten `False` (doğrulandı). | `config.py` |
| **3.3** | `TRADE_THRESHOLD=45` tek statik değer. Otonom döngüler (ghost mutasyon dahil) eşiği oynatmıyor. | `config.py`, `async_scalp_engine.py` |
| **4** | Scalp modda atlanan loop'lar: `_ml_training_loop`, `_ai_brain_loop`, `_self_healing_optuna_loop`, `_optuna_tuning_loop`, 4 Friday loop. Ghost loop'unda sonuç-işleme KORUNDU; otonom eşik/ağırlık mutasyonu + decay KAPATILDI. Heartbeat/market-regime/execution/trailing/db-bakım korundu. FridayCeo nesnesi Telegram için ayakta. | `async_scalp_engine.py` |
| **5** | Aşağıda. | — |

### Korunan sert risk vetoları (doküman ile uyumlu)
`MAX_OPEN_TRADES`, `MAX_SAME_DIRECTION`, `DAILY_MAX_LOSS_PCT`,
`MAX_CONSECUTIVE_LOSSES`, volatilite spike, fiyat tazeliği guard'ı, portföy VaR.

---

## Test stratejisi (önemli karar)

`tests/` paketi (CI = `python -m pytest tests/`) **tam "brain" davranışını**
doğruluyor (rejim/RL dinamik eşikler, kalkanlar, order-book wall, starvation
gevşemesi). Bu yüzden `SCALP_MODE`, `conftest.py`'lerde **`EXECUTION_MODE=paper`
gibi `"false"` sabitlendi** — mevcut tüm beyin testleri yazıldığı gibi geçer.
Scalp kontratı ayrı `tests/test_scalp_mode.py` (9 test) ile açıkça doğrulanıyor.
Production env vermez → `SCALP_MODE` varsayılanı True (scalp açık).

**Sonuç:** `python -m pytest tests/` → **356 passed, 1 skipped, 0 failed.** Sıfır
regresyon. (Ortam: `pandas/numpy/sklearn/flask/...` + opsiyonel `pillow/matplotlib/
edge-tts/python-binance/ccxt/websocket-client` pip ile kuruldu.)

---

## FAZ 5 — doğrulama (operasyonel, kullanıcı adımı)

Bu sandbox ortamında **piyasa verisi ve geçmiş sinyal DB'si yok, ağ kısıtlı**.
Her iki backtest motoru da (`core/backtest_engine.py`, `scripts/backtest_engine.py`)
DB'deki geçmiş sinyalleri/trade'leri tekrar oynatıp kline'ları ağdan çeker — bu
yüzden scalp parametrelerini burada anlamlı doğrulayamazlar. Dürüst olmak gerekirse
bu adım sahte çalıştırılmadı.

Senin tarafında (canlıya geçmeden ÖNCE):
```
python -m core.backtest_engine          # geçmiş sinyal varsa
python async_scalp_engine.py            # ENGINE — paper modda 24-48 saat izle
# Telegram'dan trade açılışlarını izle. Beklenen: artık trade açılıyor.
```
Birim seviyesinde doğrulanan mekanik etki: scalp + paper'da efektif eşik
`max(35, 45-10) = 35` (legacy paper'da 45) → trade çıtası düştü; çelişen
filtreler ve korelasyon blokeri kapalı.

---

## Geri dönüş (reversibility)

Tek satır: ortam değişkeni **`SCALP_MODE=false`** → tüm scalp parametreleri,
filtre bayrakları, dinamik eşik skalalaması, korelasyon blokeri ve arka plan
döngüleri **eski haline** döner. DB'ye scalp değeri yazılmadı; mutasyon yok.

İnce ayar için her parametre yine kendi env değişkeniyle override edilebilir
(örn. `TRADE_THRESHOLD=50`, `MAX_HOLD_MINUTES=40`).

---

## Değişen dosyalar
- `config.py` — SCALP_MODE bayrağı, scalp parametreleri, filtre bayrakları, `__getattr__` rejim-skalalama gate'i, `RISK_PCT`/`TP2_R` params-bypass.
- `execution_engine.py` — Pearson korelasyon blokeri scalp-gate.
- `async_scalp_engine.py` — FAZ 4 loop gate'leri + ghost otonom mutasyon gate'i.
- `conftest.py`, `tests/conftest.py` — test paketi için `SCALP_MODE=false` sabitlemesi.
- `tests/test_scalp_mode.py` — **yeni**, scalp kontratı + geri-dönüş doğrulaması (9 test).
