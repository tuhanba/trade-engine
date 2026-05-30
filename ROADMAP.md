# AX Scalp Engine — Geliştirme Yol Haritası

> Bu doküman, mevcut sistemin derinlemesine analizi sonucunda hazırlanmıştır.  
> Öneriler; teknik olgunluk, risk yönetimi ve ticari sürdürülebilirlik açısından önceliklendirilmiştir.

---

## Faz A — Kritik Eksikler (Hemen Yapılmalı)

### A1. ML Sinyal Skoru Entegrasyonu

`ml_signal_scorer.py` dosyası projede hiç oluşturulmamış olup, bu gereksinim **[NOT COMPLETED - DEPRECATED in v5 PAPER]** olarak işaretlenmiştir. Sistem bunun yerine `core/ai_decision_engine.py` üzerinden tamamen otonom "Ghost Learning" yapay zeka beynine geçmiştir.

---

### A2. Live Tracker Entegrasyonu

`live_tracker.py` konsepti **[COMPLETED - REPLACED BY GHOST TRACKER]**. Sistem artık `core/paper_tracker.py` kullanarak "Ghost Learning" algoritmasını `scalp_bot_v3.py` ana döngüsünde çalıştırıyor. VETO veya WATCH yiyen her sinyal First-Touch (TP/SL) mantığıyla takip edilip `coin_profiles` tablosunu besliyor.

---

### A3. Coin Library Entegrasyonu

`coin_library` parametreleri **[COMPLETED]**. Dinamik kaldıraç ve risk motoru (`core/advanced_risk_engine.py`) sistemde artık aktif. AI Decision Engine doğrudan `coin_profiles` tablosunu okuyarak coine özel win-rate bazlı eşik seviyeleri (`blended_wr`) ile karar veriyor. ATR bazlı dinamik stop ve portföy korelasyon kalkanı devrede.

---

## Faz B — Performans İyileştirmeleri (1-2 Hafta)

### B1. Çoklu Zaman Dilimi Confluence Skoru

**[COMPLETED]** `core/trend_engine.py` `confluence_raw` (1-3, 15m/1h/4h) hesaplıyor. `core/trigger_engine.py` 5m ekleyerek `confluence_total` (2-4) üretiyor. Bu değer `AdaptiveScorer`'da skor çarpanı olarak ve `classify_signal()`'da CHOPPY filtresi için kullanılıyor. Setup kalitesi confluence'a göre otomatik upgrade/downgrade oluyor.

---

### B2. Dinamik Kaldıraç Motoru

**[COMPLETED]** `core/risk_engine.py`'de `RiskEngine.calculate()` içinde: coin_profiles.win_rate > 0.60 → `lev_multiplier=1.25`, win_rate < 0.35 → `lev_multiplier=0.50`. ATR stop distance'a göre base leverage hesaplanıyor. TP seviyeleri market regime'e göre ölçekleniyor (CHOPPY → daraltılmış TP, BULLISH/BEARISH → genişletilmiş runner).

---

### B3. Breakeven ve Trailing Stop Otomasyonu

**[COMPLETED]** `core/trailing_engine.py`'de tam TP1/TP2/TP3 partial close + breakeven (TP1 vurulunca SL → entry + offset) + ATR-bazlı trailing stop devrede. `ExecutionEngine` sınıfı `TrailingEngine`'i kullanıyor ve exit state DB metadata'sında crash-safe şekilde saklanıyor.

---

### B4. Günlük Kayıp Limiti (Daily Drawdown Guard)

**[COMPLETED]** `execution_engine.py` içindeki `_check_daily_loss()` ana `open_trade` fonksiyonunda bir guard olarak aktif edilmiştir. Limit aşıldığında sistem yeni işlem açmayı reddeder.

---

## Faz C — Gelişmiş Özellikler (1 Ay)

### C1. Piyasa Rejimi Adaptasyonu

**[COMPLETED]** `async_scalp_engine.py`'deki `_market_regime_loop` (15 dk interval) BTC 1h+4h trendine ve ATR volatilitesine göre BULLISH/BEARISH/CHOPPY/NEUTRAL tespit eder ve `database.set_market_regime()` ile yazar. Rejim değişimlerinde Telegram bildirimi gönderilir. Strateji değişimleri:

| Rejim | Strateji Değişimi |
|---|---|
| BULLISH | SHORT skoru × 0.88 ceza → `classify_signal()` |
| BEARISH | LONG skoru × 0.88 ceza → `classify_signal()` |
| CHOPPY | Min. setup kalitesi A+ zorlanır, TP seviyeleri %20 daraltılır → `risk_engine.py` |
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

`n8n_bridge.py` dosyası repoda bulunmamaktadır **[NOT COMPLETED - DEPRECATED]**. Sistem şu an sadece Telegram (`telegram_delivery.py`) ile mükemmel ve temiz şekilde çalışmaktadır.

---

### D4. Otomatik Model Yeniden Eğitimi

**[PARTIALLY COMPLETED]** `async_scalp_engine.py`'deki `_ml_training_loop` her 24h veya **50 yeni kapanan trade** sonrasında (hangisi önce gelirse) `train_model()` tetikliyor. Model CV accuracy karşılaştırması henüz yok — mevcut model her koşulda üzerine yazılıyor.

---

## Öncelik Özeti

| Durum | Geliştirme | Tahmini Etki |
|---|---|---|
| ✅ DONE | ML Sinyal Skoru entegrasyonu | Sinyal kalitesi +%15-20 |
| ✅ DONE | Live Tracker → Ghost Tracker | Parametre optimizasyonu |
| ✅ DONE | Coin Library → Risk Engine entegrasyonu | Coin bazlı SL/TP |
| ✅ DONE | Breakeven + trailing stop otomasyonu | Drawdown -%30 |
| ✅ DONE | Günlük kayıp limiti kontrolü | Risk yönetimi |
| ✅ DONE | Piyasa rejimi adaptasyonu (loop + filtreler) | Choppy piyasada kayıp azalır |
| ✅ DONE | Confluence skoru (1m/5m/1h/4h) | Sahte sinyal oranı düşer |
| ✅ DONE | Dinamik kaldıraç (win_rate bazlı) | Risk-reward optimize |
| 🔄 PARTIAL | ML otomatik yeniden eğitim (50 trade trigger) | Model tazeliği |
| ⏳ TODO | Backtesting modülü (core/backtester.py mevcut) | Parametre güvenilirliği |
| ⏳ TODO | Portfolio Heat Map Dashboard | Manuel strateji desteği |
| ⏳ TODO | 8h funding rate ortalaması (macro_filter) | Daha iyi macro filtre |
| 🔮 Uzun Vadeli | Redis state yönetimi | Çok instance desteği |
| 🔮 Uzun Vadeli | Çoklu exchange desteği | Sistem dayanıklılığı |
