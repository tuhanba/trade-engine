# 📊 Aurvex AI Trading Engine — Sipariş Defteri Mikroyapı Analizi ve L2 Telemetri

Bu doküman, sipariş defteri derinlik analizlerini, OBI metriklerini, Block Trade Footprint taramalarını ve Swing Failure Pattern (SFP) limit emir optimizasyonunu açıklar.

---

## ⚖️ Order Book Imbalance (OBI)

Sistem, fiyatın anlık yön eğilimini ve emir defteri baskısını ölçmek için Binance Futures L2 derinlik verilerini (Top 20 kademe alıcı/satıcı) analiz eder:

- **OBI Formülü**:
  $$\text{OBI} = \frac{\text{Bid Depth}_{\text{top20}} - \text{Ask Depth}_{\text{top20}}}{\text{Bid Depth}_{\text{top20}} + \text{Ask Depth}_{\text{top20}}}$$
- **Metrik Analizi**:
  - OBI **> 0.4**: Alıcılar baskın, fiyat yukarı ivmelenmek isteyebilir.
  - OBI **< -0.4**: Satıcılar baskın (likidite duvarları yukarıda yığılmış), fiyat aşağı baskılanabilir.
- **Sinyal Veto**: Eğer Long sinyali geldiği anda OBI < -0.4 ise (karşı yönde güçlü satış baskısı/duvarı varsa) veya Short sinyalinde OBI > 0.4 ise işlem risk motoru tarafından veto edilir.

---

## 👣 Block Trade Footprint (Kurumsal Emilim)

Büyük cüzdanların (Balinaların) veya kurumsal oyuncuların anlık işlem yönlerini yakalamak için Binance Futures anlık işlem akışı (Recent Trades) sürekli izlenir:

- **Kriter**: Değeri **50,000 USDT** üzerindeki büyük tekil işlemler (Block Trades) filtrelenir.
- **Analiz**: Son 100 işlem içerisindeki alım ve satım yönlü block trade'lerin toplam hacmi karşılaştırılır.
- **Absorption (Emilim) Engeli**: Eğer fiyat bir destek seviyesine inerken ve Long sinyali üretilmişken, piyasada yoğun şekilde 50,000 USDT üzeri aktif satış blokları gerçekleşiyorsa (Institutional Selling), fiyatın düşüş trendi emilinceye kadar sinyal bloke edilir.

---

## 🎯 Swing Failure Pattern (SFP - Stop Hunt Sweep)

Fiyatın, yakın geçmişteki belirgin destek veya direnç seviyelerini anlık olarak "süpürüp" (sweep/iğne atıp) tersine döndüğü tuzak mum formasyonlarını tespit eder:

```
          [Direnç Seviyesi]
──────────────────▲──────────────────
                  │   ▲
                  │   │ (Stop Hunt Sweep)
                  │   ▼
            ┌─────┴─────┐
            │           │  [Kapanış Direncin Altında]
            └─────┬─────┘
                  │
```

- **SFP LONG**: Fiyatın geçmiş bir dip seviyesinin altına iğne atıp, mum kapanışını bu dip seviyesinin üzerinde yapması durumudur (Sanal alıcıların stopları patlatılmıştır).
- **SFP SHORT**: Fiyatın geçmiş bir tepe seviyesinin üzerine iğne atıp, mum kapanışını bu tepe seviyesinin altında yapması durumudur.
- **Limit Giriş Optimizasyonu (SFP Limit Entry)**: SFP tespit edildiğinde, sistem market emriyle girmek yerine, iğne atılan uç noktaya yakın bir seviyeden **LIMIT** emir tahtaya bırakır. Bu sayede işlem maliyeti (slippage) düşürülür ve risk-reward oranı maksimize edilir.
