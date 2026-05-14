## Phase 5: Backtest + Signal Replay Engine Raporu

**Hedef:** Geçmiş sinyalleri farklı strateji parametreleriyle simüle ederek en uygun ayarları (SL, TP, Risk) belirlemek.

**Yapılan Geliştirmeler:**

1.  **`core/signal_replay.py` Modülü Oluşturuldu:**
    *   **Single Signal Replay:** Belirli bir sinyali, `paper_results` tablosundaki MFE (Max Favorable Excursion) ve MAE (Max Adverse Excursion) verilerini kullanarak yeni SL ve TP çarpanlarıyla simüle eder.
    *   **Batch Backtest:** Seçilen bir grup sinyal üzerinde toplu simülasyon yaparak stratejinin genel performansını (Win Rate vb.) hesaplar.
    *   **Parameter Optimization:** Belirli bir örneklem üzerinde farklı SL/TP kombinasyonlarını deneyerek en yüksek başarı oranını veren "en iyi parametreleri" bulur.

2.  **Veri Odaklı Simülasyon:**
    *   Simülasyonlar, sadece varsayımsal değil, `paper_tracker` tarafından kaydedilen gerçek fiyat hareketlerinin ekstrem noktalarına (MFE/MAE) dayanır. Bu, kline verisi olmadan da yüksek doğrulukta backtest yapılmasını sağlar.

**Sonuç:**

Sistem artık kendi geçmişini "tekrar oynatarak" (replay) hangi parametrelerin daha kârlı olacağını bilimsel bir şekilde test edebilmektedir. Bu, AI Decision Engine'in parametre optimizasyonu (Phase 4'te temelleri atılan) için sağlam bir test ortamı sağlar.

**Geliştirme Önerileri:**

*   **Kline Entegrasyonu:** Daha hassas simülasyonlar için veritabanına veya harici bir kaynağa 1 dakikalık kline verisi entegre edilerek, mum içindeki fiyat hareketleri de simüle edilebilir.
*   **Equity Curve Görselleştirme:** Backtest sonuçlarının kümülatif kâr/zarar grafiği (Equity Curve) dashboard üzerinden görselleştirilebilir.

Bu faz ile sistemin optimizasyon yeteneği simülasyon katmanıyla güçlendirilmiştir. Bir sonraki faza geçmeye hazırım.
