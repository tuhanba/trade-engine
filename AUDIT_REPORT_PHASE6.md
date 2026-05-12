## Phase 6: Coin Personality Engine Raporu

**Hedef:** Her coin'in kendine has davranışlarını analiz ederek, trade stratejisini o coine özel hale getirmek (Adaptif Karakterler).

**Yapılan Geliştirmeler:**

1.  **`core/coin_personality.py` Modülü Oluşturuldu:**
    *   **Personality Analysis:** Coinleri geçmiş verilerine (Win Rate, Fakeout Rate, MFE, MAE) göre kategorize eder:
        *   **The Faker:** Yüksek fakeout oranına sahip, stop avcısı coinler.
        *   **The Runner:** Güçlü trend takibi yapan, kârı koşturan coinler.
        *   **The Grinder:** Düşük başarı oranlı ama yüksek RR gerektiren coinler.
        *   **The Average Joe:** Standart davranış sergileyen coinler.
    *   **Adaptive Params:** Kişiliğe göre otomatik SL çarpanı, risk yüzdesi ve kaldıraç önerileri sunar.

2.  **`core/risk_engine.py` Entegrasyonu:**
    *   `RiskEngine` artık hesaplama yapmadan önce `CoinPersonalityEngine`'den adaptif parametreleri sorgular.
    *   Eğer bir coin "The Faker" ise otomatik olarak daha geniş bir stop (SL) ve daha düşük risk uygular.
    *   Eğer bir coin "The Runner" ise kâr hedefini (TP) büyütür ve güveni artırır.

3.  **`scalp_bot.py` Güncellemesi:**
    *   `RiskEngine` başlatılırken veritabanı yolu (`DB_PATH`) geçilerek kişilik analizinin veritabanına erişimi sağlandı.

**Sonuç:**

Sistem artık "tek bir strateji her coine uyar" yaklaşımından sıyrılarak, her coinin karakterine göre esneyebilen (adaptif) bir yapıya kavuşmuştur. Bu, özellikle volatil piyasalarda stop patlatma (fakeout) riskini azaltırken, trend yapan coinlerde kârı maksimize eder.

**Geliştirme Önerileri:**

*   **Dinamik Kişilik Geçişleri:** Bir coinin kişiliği zamanla değişebilir (örneğin boğa piyasasında "Runner" olan bir coin ayı piyasasında "Faker"a dönüşebilir). Bu geçişleri tespit eden bir "Regime Personality Link" eklenebilir.
*   **Görsel Profilleme:** Dashboard üzerinde her coinin "kişilik kartı" ve hangi traitlere (özelliklere) sahip olduğu gösterilebilir.

Bu faz ile sistemin adaptasyon kabiliyeti en üst seviyeye çıkarılmıştır. Bir sonraki faza geçmeye hazırım.
