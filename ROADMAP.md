# AX Scalp Engine — Geliştirme Yol Haritası

> Bu doküman, mevcut sistemin derinlemesine analizi sonucunda hazırlanmıştır.  
> Öneriler; teknik olgunluk, risk yönetimi ve ticari sürdürülebilirlik açısından önceliklendirilmiştir.

---

## Faz A — Kritik Eksikler (Hemen Yapılmalı)

### A1. ML Sinyal Skoru Entegrasyonu

`ml_signal_scorer.py` (410 satır, Voting Ensemble RF+GB) mevcut `core/` pipeline'ına **bağlı değil**. Bu modül `scalp_bot.py`'de hiç çağrılmıyor. Entegrasyon yapıldığında her sinyal, geçmiş 30+ trade'den öğrenilmiş bir **0-100 güven skoru** alır ve bu skor AI Decision Engine'e ek girdi olarak beslenir.

**Yapılacak:** `TriggerEngine.analyze()` çıktısına `ml_score` alanı eklenmeli, `AIDecisionEngine.evaluate()` içinde bu skora göre `ai_adj` düzeltmesi yapılmalı.

---

### A2. Live Tracker Entegrasyonu

`live_tracker.py` (252 satır) açık trade'lerin 15 saniyelik tick verilerini kaydediyor ancak bu veriler `ai_brain.py`'deki postmortem analizine **geri beslenmemiş**. Kapanan her trade için `trade_analysis` tablosuna `efficiency`, `sl_tightness`, `missed_gain` yazılmalı ve bu değerler `AIDecisionEngine.learn_from_trade()` çağrısına parametre olarak iletilmeli.

**Yapılacak:** `execution_engine._finalize()` içinde `live_tracker.record_close()` çağrısı eklenmeli.

---

### A3. Coin Library Entegrasyonu

`coin_library.py` (310 satır) her coin için `sl_atr_mult`, `tp_atr_mult`, `max_leverage`, `min_adx`, `preferred_direction`, `fakeout_rate` gibi **coin bazlı parametreler** tutuyor. Ancak `core/risk_engine.py` bu profilleri okumadan sabit değerler kullanıyor.

**Yapılacak:** `RiskEngine.calculate()` içinde `coin_library.get_coin_profile(symbol)` çağrısı eklenmeli; SL/TP çarpanları coin profiline göre dinamik seçilmeli.

---

## Faz B — Performans İyileştirmeleri (1-2 Hafta)

### B1. Çoklu Zaman Dilimi Confluence Skoru

Şu an `core/trend_engine.py` 1m + 5m + 1h + 4h analiz yapıyor ancak bu dört zaman diliminin **aynı yönde hizalanma oranı** tek bir sayıya indirilmiyor. Confluence skoru (0-4 arası) hesaplanmalı ve bu skor `trigger_engine.py`'deki setup kalitesi kararına dahil edilmeli.

| Confluence | Setup Kalitesi Etkisi |
|---|---|
| 4/4 hizalı | A+ garantisi |
| 3/4 hizalı | A veya B |
| 2/4 hizalı | B veya C |
| 1/4 hizalı | D (geç) |

---

### B2. Dinamik Kaldıraç Motoru

`execution_engine.py`'de kaldıraç sabit (`max_leverage` config'den). `ai_brain.py`'deki `suggest_leverage()` fonksiyonu (win_rate + profit_factor bazlı) `core/risk_engine.py`'ye taşınmalı. Yüksek güvenli sinyallerde kaldıraç artırılmalı, düşük win rate'li coinlerde otomatik düşürülmeli.

---

### B3. Breakeven ve Trailing Stop Otomasyonu

`execution_engine.py`'de trailing stop altyapısı mevcut ancak breakeven mantığı eksik. TP1'e ulaşıldığında SL otomatik olarak entry'e çekilmeli (breakeven). Bu, risk-free trade konseptini hayata geçirir ve drawdown'u önemli ölçüde azaltır.

---

### B4. Günlük Kayıp Limiti (Daily Drawdown Guard)

`config.py`'de `DAILY_MAX_LOSS_PCT = 3.0` tanımlı ancak `scalp_bot.py`'de bu değer **kontrol edilmiyor**. Günlük PnL hesaplanmalı; limit aşıldığında devre kesici otomatik devreye girmeli.

---

## Faz C — Gelişmiş Özellikler (1 Ay)

### C1. Piyasa Rejimi Adaptasyonu

`ai_brain.py`'deki `get_market_regime()` (BULLISH / BEARISH / CHOPPY / NEUTRAL) `core/ai_decision_engine.py`'ye kısmen entegre edildi ancak **rejime göre strateji değişimi** henüz yok. Önerilen davranışlar:

| Rejim | Strateji Değişimi |
|---|---|
| BULLISH | Sadece LONG sinyaller onaylanır, SHORT eşiği yükseltilir |
| BEARISH | Sadece SHORT sinyaller onaylanır, LONG eşiği yükseltilir |
| CHOPPY | Minimum setup kalitesi A+ olarak zorlanır, günlük limit yarıya indirilir |
| NEUTRAL | Standart kurallar geçerli |

---

### C2. On-Chain ve Funding Rate Tabanlı Makro Filtre

`core/trigger_engine.py`'de funding rate kontrolü mevcut ancak yalnızca anlık değer kullanılıyor. 8 saatlik funding rate ortalaması hesaplanmalı; sürekli negatif funding short sinyallerini, sürekli pozitif funding long sinyallerini destekler. Ek olarak open interest değişimi (OI spike) ani yön değişimlerini önceden tespit etmek için kullanılabilir.

---

### C3. Backtesting Modülü

Sistemin hiç backtesting altyapısı yok. Geçmiş OHLCV verisi üzerinde `core/` pipeline'ının tamamı simüle edilebilmeli. Bu, yeni parametre setlerinin canlıya alınmadan önce doğrulanmasını sağlar. Minimum gereksinim: 3 aylık 5m OHLCV verisi + tüm indikatör hesaplamaları.

---

### C4. Portfolio Heat Map Dashboard

Mevcut dashboard sinyal listesi gösteriyor ancak **portföy ısı haritası** yok. Hangi coinlerin hangi saatlerde ne kadar PnL ürettiğini gösteren interaktif bir heatmap (coin × saat matrisi) eklenmeli. Bu, manuel strateji kararlarını destekler.

---

## Faz D — Altyapı ve Güvenilirlik (Uzun Vadeli)

### D1. Çoklu Exchange Desteği

Sistem yalnızca Binance Futures'a bağlı. `execution_engine.py` soyutlanarak Bybit ve OKX adaptörleri eklenebilir. Bu, Binance'in API kısıtlamaları veya bakım dönemlerinde sistemin çalışmaya devam etmesini sağlar.

---

### D2. Redis Tabanlı State Yönetimi

Şu an tüm durum bilgisi SQLite'ta tutuluyor. Yüksek frekanslı okuma/yazma işlemleri için `daily_signals`, `recent_coins`, `circuit_breaker` gibi geçici durum verileri Redis'e taşınmalı. Bu, bot restart'larında state kaybını önler ve çok instance çalıştırma imkânı verir.

---

### D3. Webhook Tabanlı Trade Bildirimleri

`n8n_bridge.py` mevcut ancak `scalp_bot.py`'de `register_bot()` çağrısı yapılmıyor. n8n entegrasyonu aktif edildiğinde trade açılış/kapanış bildirimleri Telegram dışında e-posta, Discord veya özel webhook'lara da gönderilebilir.

---

### D4. Otomatik Model Yeniden Eğitimi

`ml_signal_scorer.py`'deki model manuel olarak eğitiliyor. Her 50 yeni trade sonrasında model otomatik yeniden eğitilmeli ve yeni model eski modelden daha iyi performans gösteriyorsa (CV accuracy karşılaştırması) otomatik olarak aktif hale getirilmeli.

---

## Öncelik Özeti

| Öncelik | Geliştirme | Tahmini Etki |
|---|---|---|
| **Kritik** | ML Sinyal Skoru entegrasyonu | Sinyal kalitesi +%15-20 |
| **Kritik** | Live Tracker → postmortem geri besleme | Parametre optimizasyonu daha hızlı |
| **Kritik** | Coin Library → Risk Engine entegrasyonu | Coin bazlı SL/TP optimizasyonu |
| **Yüksek** | Breakeven + trailing stop otomasyonu | Drawdown -%30 |
| **Yüksek** | Günlük kayıp limiti kontrolü | Risk yönetimi tamamlanır |
| **Yüksek** | Piyasa rejimi adaptasyonu | Choppy piyasada kayıp azalır |
| **Orta** | Confluence skoru | Sahte sinyal oranı düşer |
| **Orta** | Backtesting modülü | Parametre güvenilirliği artar |
| **Uzun Vadeli** | Redis state yönetimi | Çok instance desteği |
| **Uzun Vadeli** | Çoklu exchange desteği | Sistem dayanıklılığı artar |
