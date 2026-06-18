# 👻 Aurvex AI Trading Engine — Ghost Learning Filtreleri ve Parametre Kalibrasyonu

Bu doküman, sistemin otonom parametre kalibrasyonu ve kağıt üzerinde sinyal takibi sağlayan **Ghost Learning** yapısını açıklar.

---

## 💡 Ghost Learning Nedir?

Ghost Learning, reddedilen (VETO edilen) veya izlemeye alınan (WATCH) sinyallerin arka planda sanal olarak açılmış gibi takip edildiği **simüle edilmiş bir öğrenme katmanıdır**. 

Sistem, gerçek parayla işlem açmadığı veya risk limitlerinden ötürü elediği sinyallerin "Eğer girilseydi ne olurdu?" sorusunu yanıtlayarak kendi filtrelerini kalibre eder.

---

## 📈 MFE ve MAE Takip Mekanizması

Ghost Learning motoru, takip ettiği her sanal işlem için iki kritik metriği sürekli kaydeder:

1. **MFE (Maximum Favorable Excursion)**: Pozisyonun açık kaldığı süre boyunca gördüğü en yüksek karlı fiyat seviyesidir (Long için en yüksek tepe, Short için en düşük dip).
2. **MAE (Maximum Adverse Excursion)**: Pozisyonun açık kaldığı süre boyunca gördüğü en yüksek zararlı fiyat seviyesidir (Long için en düşük dip, Short için en yüksek tepe).

Bu oranlar, coine özel TP ve SL mesafelerinin ne kadar doğru ayarlandığını ölçmek için altın standarttır.

---

## 🪙 Coine Özel Profil Oluşturma (Coin Profiling)

Toplanan MFE/MAE ve sanal TP/SL istatistikleri, veritabanındaki `coin_profiles` tablosuna işlenir:
- Her coin çifti (örn: BTCUSDT, SOLUSDT) için ayrı bir win-rate (kazanma oranı) ve ortalama R-payoff değeri hesaplanır.
- **Dinamik Eşik Güncellemeleri**: `core/advanced_risk_engine.py` modülü, bir coine sinyal geldiğinde o coinin profil tablosundaki `blended_win_rate` değerini kontrol eder. Win-rate > 0.60 ise kaldıraç artırılır, win-rate < 0.35 ise kaldıraç yarıya indirilir veya işlem bloke edilir.

---

## ⚙️ Optuna ile Hiperparametre Optimizasyonu

Ghost Learning verileri biriktikçe, sistem belirli aralıklarla (örn: 24 saatte bir veya 50 yeni trade kapandığında) `test_auto_opt.py` modülü üzerinden **Optuna** optimizasyon kütüphanesini tetikler:
- **Hedef Fonksiyon (Objective)**: Tarihsel ve sanal işlemlerin getiri eğrisini maksimize etmek, maksimum drawdown (kayıp) oranını minimize etmek.
- **Optimize Edilen Değişkenler**: RSI sınırları, Bollinger Bandı genişliği, CVD filtre hassasiyeti ve trailing stop katsayıları.
- Optimizasyon sonucunda elde edilen en iyi parametre setleri otomatik olarak `database.py` üzerindeki sistem durumuna yazılarak sisteme canlı olarak enjekte edilir.
