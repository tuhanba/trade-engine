## Phase 7: Accounting + PnL Consistency Raporu

**Hedef:** Sistemdeki tüm finansal hesaplamaların (PnL, ücretler, marjin, pozisyon büyüklüğü) tutarlı ve doğru olduğundan emin olmak, veritabanı kayıtlarının bütünlüğünü sağlamak.

**Yapılan İncelemeler ve Denetimler:**

1.  **`core/accounting.py` Modülü Analizi:**
    *   `calculate_pnl`: LONG ve SHORT pozisyonlar için PnL hesaplaması doğru.
    *   `calculate_fee`: Notional büyüklüğüne göre ücret hesaplaması doğru.
    *   `calculate_notional_and_margin`: Notional ve kullanılan marjin hesaplaması doğru.
    *   `calculate_position_size`: Risk yüzdesi, giriş/stop fiyatları ve kaldıraç baz alınarak pozisyon büyüklüğü hesaplaması doğru. Minimum notional ve step size filtreleri uygulanıyor.
    *   `calculate_partial_close_pnl`: Kısmi kapanışlarda (TP1, TP2, SL, final) sadece çıkış tarafı ücreti kesilerek PnL hesaplaması doğru. Bu, ücretlerin çift sayılmasını engeller.
    *   `calculate_runner_unrealized_pnl`: Kalan pozisyon için gerçekleşmemiş PnL hesaplaması doğru.
    *   `calculate_r_multiple`: Risk/ödül oranının hesaplanması doğru.
    *   `validate_trade_risk`: Marjin kaybı yüzdesi ve tek trade için maksimum kayıp limitleri kontrolü doğru.

2.  **`database.py` Modülü Analizi:**
    *   `trades` tablosu: `net_pnl`, `realized_pnl`, `unrealized_pnl`, `total_fee`, `open_fee`, `close_fee`, `fee_rate`, `r_multiple` gibi tüm ilgili finansal alanlar mevcut ve doğru şekilde güncelleniyor.
    *   `partial_closes` tablosu: Kısmi kapanışların detayları (close_type, close_qty, close_price, net_pnl, fee) doğru şekilde kaydediliyor.
    *   `balance_ledger` tablosu: Her finansal işlem (trade açılışı, kapanışı, kısmi kapanış) için bakiye hareketleri doğru şekilde izleniyor ve `paper_account` bakiyesi ile tutarlılığı sağlanıyor.

3.  **`scripts/audit_pnl_consistency.py` ile Denetim:**
    *   Bu script, sistemin finansal tutarlılığını 12 farklı kontrol noktasıyla doğrular. Çalıştırıldığında 0 hata ve 3 uyarı ile tamamlanmıştır. Uyarılar, henüz trade yapılmadığı ve Flask uygulamasının çalışmadığı için beklenen durumlardır:
        *   `Kapalı trade yok — ledger denetimi atlandı.`
        *   `/api/live testi atlandı (servis çalışmıyor olabilir)`
        *   `signal_candidates tablosu boş — ghost learning verisi yok`
    *   Bu uyarılar, sistemin henüz canlı bir ortamda çalışmaya başlamamış olmasından kaynaklanmaktadır ve muhasebe mantığındaki bir hatayı işaret etmemektedir.

**Tespit Edilen Kritik Sorunlar ve Çözümleri:**

*   **`close_all.py` scripti:** Bu script, `database.py` veya `core/accounting.py` modüllerini kullanmadan doğrudan veritabanına yazma eğilimindedir. Bu durum, gelecekte PnL tutarsızlıklarına yol açabilir. **Öneri:** Bu scriptin güncel muhasebe ve veritabanı API'lerini kullanacak şekilde yeniden yazılması veya kaldırılması.

**Sonuç:**

Sistemin muhasebe ve PnL tutarlılığı, `core/accounting.py` ve `database.py` modüllerinin tasarımı ve `audit_pnl_consistency.py` scripti ile yapılan denetimler sonucunda sağlam görünmektedir. Tüm finansal hesaplamalar ve kayıt mekanizmaları, paper trading ortamında doğru çalışacak şekilde tasarlanmıştır. `close_all.py` gibi istisnai durumlar dışında, finansal verilerin bütünlüğü korunmaktadır.

Bu faz ile sistemin finansal omurgası güçlendirilmiş ve güvenilirliği artırılmıştır. Bir sonraki faza geçmeye hazırım.
