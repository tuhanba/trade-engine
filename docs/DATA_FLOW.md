# 🌊 Aurvex AI Trading Engine — Sinyal ve Veri Akışı

Bu doküman, sistemde bir sinyal adayının doğuşundan Binance üzerinde işleme dönüşmesine, takip edilmesine ve kapatılmasına kadar geçen tüm yaşam döngüsünü adım adım açıklar.

---

## 🔄 Yaşam Döngüsü Özeti (Adım Adım)

```
[Tarama Başlangıcı]
       │
       ▼
[Market Tarayıcı (Scanner)] ───► İndikatörler (RSI, CVD, Bollinger vb.) hesaplanır.
       │
       ▼
[Sinyal Eşik Kontrolü] ────► Sinyal Skoru hesaplanır (Trade Threshold aşılmalı).
       │
       ▼
[AI Karar Mekanizması] ───► Friday Debate: CRO (Risk) ve CTA (Teknik) oylaması.
       │
       ▼
[Makro & Trend Filtresi] ─► 8h Funding Rate ve BTC trend hizalaması.
       │
       ▼
[Risk Engine Bariyeri] ───► VaR limit kontrolü ve Pearson Korelasyon Kalkanı.
       │
       ▼
[Execution Modülü] ──────► Limit Chase ile Binance Futures'ta emrin gönderilmesi.
       │
       ▼
[Pozisyon Takibi] ───────► Trailing Stop ve Breakeven otomasyonu tetiklenir.
       │
       ▼
[Pozisyon Kapanışı] ─────► TP/SL tetiklenmesi, Q-Learning modelinin güncellenmesi.
```

---

## Adım Detayları

### 1. Market Tarama (Scanner Loop)
`async_scalp_engine.py` içerisinde yer alan `_scanner_loop` her 45 saniyede bir tetiklenir:
- İzleme listesindeki coinlerin 5m/15m/1h/4h OHLCV verileri toplanır.
- `SignalEngine` vasıtasıyla indikatör değerleri ve Cumulative Volume Delta (CVD) eğimleri hesaplanır.

### 2. Sinyal Üretimi ve Sınıflandırma
Eğer teknik parametreler sinyal oluşturma koşullarını (örn. Bollinger bandı aşımı + CVD uyumsuzluğu) karşılıyorsa bir sinyal adayı (`Signal Candidate`) oluşturulur.
- Sinyal kalitesi (A, B, C vb.) ve confluence skoruna göre ham bir `score` hesaplanır.
- Skor `config.WATCHLIST_THRESHOLD` değerinin üzerindeyse bir sonraki aşamaya geçer.

### 3. Yapay Zeka Konsensüsü (Friday Debate)
Sinyal adayı `core/ai_decision_engine.py` modülüne aktarılır:
- **CTA (Baş Teknik Analist)**: Teknik grafik formasyonunu ve indikatörleri doğrular.
- **CRO (Baş Risk Yöneticisi)**: Kasa büyüklüğü ve piyasa oynaklığı açısından risk değerlendirmesi yapar.
- Ajanlar oylarını kullanır. Ortak konsensüs kararı (VETO, WATCH veya ALLOW) belirlenir. Karar "ALLOW" ise akış devam eder.

### 4. Risk ve Portföy Filtreleri
Risk motoru (`core/risk_engine.py`) son kontrolleri yapar:
- **Portföy VaR Sınırı**: Yeni işlemle birlikte portföyün %99 VaR değeri limitleri aşıyorsa pozisyon büyüklüğü ölçeklendirilir (scale-down) veya veto edilir.
- **Korelasyon Kalkanı**: Yeni coinin açık pozisyonlardaki diğer coinlerle olan Pearson korelasyon katsayısı > 0.75 ise ve yönü aynıysa (Long/Long) işlem engellenir.
- **Daily Drawdown Guard**: Günlük kayıp limitine ulaşıldıysa yeni pozisyon girişi engellenir.

### 5. Emir Gönderimi ve Kovalamaca (Limit Chase)
Pozisyon açma kararı onaylandıktan sonra `core/live_execution.py` modülü devreye girer:
- Kasa bakiyesi ve Kelly Kriterine göre belirlenen lot büyüklüğüyle **LIMIT** emir gönderilir.
- Emir tamamen dolana kadar fiyat kovalama (Limit Chase) mekanizması çalışır. Gecikme > 300ms ise latency clutch devreye girer ve slippage engellenir.

### 6. Pozisyon Takibi (Management)
İşlem açıldıktan sonra `core/trailing_engine.py` modülü devralır:
- Fiyat TP1 hedefine ulaştığında, pozisyonun bir kısmı kapatılır ve stop seviyesi giriş fiyatına çekilir (Breakeven).
- Fiyat ilerledikçe ATR tabanlı trailing stop (takip eden zarar kes) seviyesi yukarı/aşağı güncellenir.

### 7. Kapanış ve Öğrenme (Post-Trade Feedback)
Pozisyon TP, SL veya manuel müdahaleyle kapandığında:
- Bakiye defteri ve işlem günlükleri `database.py` üzerinde güncellenir.
- WebSocket aracılığıyla dashboard arayüzüne `trade_closed` olayı gönderilir.
- Kapanan işlemden elde edilen PnL (R-multiple) verisiyle `core/rl_meta_learner.py` Q-Learning modeli güncellenerek sistemin bir sonraki rejimlerde daha iyi kararlar alması sağlanır.
