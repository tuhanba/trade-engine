# 🔍 Aurvex AI Trading Engine — Market Tarayıcı ve Sinyal Motoru

Bu doküman, sistemin piyasaları tarama döngüsünü, indikatör hesaplama yöntemlerini ve sinyal puanlama (scoring) modellerini detaylandırır.

---

## 🔄 Market Tarama Döngüsü (Scanner Loop)

`async_scalp_engine.py` altındaki asenkron tarayıcı, sistemin ana lokomotifidir:
1. **İzleme Listesi (Watchlist)**: Binance Futures üzerinde aktif işlem gören ve hacim kriterlerini karşılayan coin çiftleri (örn. USDT çiftleri) dinamik belirlenir.
2. **Asenkron Paralellik**: `AsyncMarketDataService` modülü, tüm coinlerin fiyat ticker'larını ve son kline mumlarını asenkron olarak çeker ve RAM'de önbelleğe alır.
3. **Döngü Sıklığı**: Varsayılan olarak 45 saniyede bir tüm izleme listesi baştan sona taranarak teknik analiz filtrelerinden geçirilir.

---

## 📊 Teknik İndikatör Hesaplamaları

Sinyal motoru (`SignalEngine`), her coin için çoklu zaman dilimlerinde (5m, 15m, 1h, 4h) veri toplayarak aşağıdaki indikatörleri hesaplar:

- **Relative Strength Index (RSI)**: Fiyatın aşırı alım/satım durumlarını ölçer.
- **Bollinger Bands (BB)**: Fiyatın oynaklık sınırlarını ve ortalamadan sapmasını belirler. Bandın dışına taşan iğneler sinyal adayı olarak işaretlenir.
- **Average True Range (ATR)**: Dinamik TP/SL mesafelerini ayarlamak için piyasa oynaklığını (volatilite) ölçer.
- **Cumulative Volume Delta (CVD) & Divergence**: 
  - Alıcı ve satıcıların hacimsel gücünü biriktirerek CVD eğrisini çıkarır.
  - Fiyat yükselirken CVD düşüyorsa (ayı uyumsuzluğu) veya fiyat düşerken CVD yükseliyorsa (boğa uyumsuzluğu) güçlü bir dönüş sinyali üretilir.

---

## 📈 Çoklu Zaman Dilimli Confluence ve Sinyal Puanlama

Sistem, tek bir zaman dilimine bağlı kalmamak için **Çoklu Zaman Dilimli Trend Hizalaması (MTF Confluence)** uygular:

1. **Trend Filtresi (1h ve 4h)**: 1 saatlik ve 4 saatlik grafiklerde EMA-20 ve EMA-50 ortalamaları karşılaştırılarak makro trend yönü (BULLISH, BEARISH, NEUTRAL) belirlenir.
2. **Trend Uyum Cezası**: Makro trend LONG iken gelen bir SHORT sinyali veya BEARISH trenddeyken gelen bir LONG sinyalinin puanı otomatik düşürülür (C1 Market Regime Cezası).
3. **Confluence Skor Çarpanı**: 5m, 15m ve 1h grafiklerindeki yönlerin uyuşma derecesine göre sinyale bir `Confluence Score` (2 ila 4 arası) verilir.
4. **Kalite Sınıflandırması**: Sinyaller, toplam puanlarına göre sınıflandırılır:
   - **A+ / A**: Çok güçlü trend hizalaması ve hacim desteği (Doğrudan işleme uygun).
   - **B**: Orta kalite sinyal (Watchlist veya Ghost tracker'a alınabilir).
   - **C / D**: Zayıf sinyaller (Doğrudan elenir).
