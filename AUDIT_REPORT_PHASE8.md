## Phase 8: Performance + Stability (Hız ve Kararlılık) Raporu

**Hedef:** Aurvex AI Trade Engine sisteminin genel performansını ve kararlılığını artırmak, potansiyel darboğazları tespit etmek ve iyileştirme önerileri sunmak.

**İncelenen Modüller:**

*   **`scalp_bot.py`:** Ana işlem döngüsü, modül entegrasyonları ve kontrol akışı.
*   **`config.py`:** Sistem genelindeki yapılandırma parametreleri ve eşikler.
*   **`watchdog.py`:** Servis sağlığı izleme ve otomatik kurtarma mekanizmaları.

**Tespit Edilen Alanlar ve İyileştirme Önerileri:**

### 1. `scalp_bot.py` (Ana Döngü Optimizasyonu)

Ana döngü (`main()` fonksiyonu), sistemin kalbidir ve performans ile kararlılık üzerinde doğrudan etkiye sahiptir.

*   **`time.sleep` Kullanımları:**
    *   **Mevcut Durum:** Döngü içinde çeşitli noktalarda sabit `time.sleep()` çağrıları bulunmaktadır (örn. `SCAN_INTERVAL`, devre kesici durumunda 10 saniye, günlük kayıp limiti aşıldığında 300 saniye). Bu sabit gecikmeler, bazen gereksiz beklemelere yol açarak sistemin reaksiyon süresini uzatabilir veya CPU kaynaklarını verimsiz kullanabilir.
    *   **Öneri:** Sabit gecikmeler yerine, özellikle I/O bekleyen durumlarda (API çağrıları, veritabanı işlemleri) daha dinamik veya olay tabanlı bekleme mekanizmaları (örneğin, `select` veya `asyncio` ile non-blocking I/O) kullanılabilir. Ancak `asyncio` büyük bir refactor gerektirecektir. Daha basit bir yaklaşım olarak, `SCAN_INTERVAL` gibi değerler, piyasa volatilitesine veya sistem yüküne göre dinamik olarak ayarlanabilir.

*   **Veritabanı Sorguları:**
    *   **Mevcut Durum:** Ana döngü içinde `get_daily_signal_count()`, `get_open_trades()` gibi veritabanı sorguları sıkça yapılmaktadır. Her döngüde veritabanına yapılan bu çağrılar, özellikle yüksek frekanslı taramalarda performans darboğazı oluşturabilir.
    *   **Öneri:** Sık erişilen ve değişmeyen veriler için basit bir önbellekleme mekanizması (örneğin, Python dictionary veya `functools.lru_cache`) kullanılabilir. `get_open_trades()` gibi fonksiyonlar için, sadece değişiklik olduğunda veritabanını sorgulayan veya daha hafif bir mekanizma ile güncelleyen bir yapı düşünülebilir.

*   **Modül Çağrıları ve Veri Akışı:**
    *   **Mevcut Durum:** Her ana döngü iterasyonunda `scanner.scan()`, `trend.analyze()`, `trigger.evaluate()`, `risk.assess()`, `ai_engine.decide()` gibi tüm modüller sırayla çağrılmaktadır. Bu ardışık işlem, her adımın tamamlanmasını beklediği için gecikmelere neden olabilir.
    *   **Öneri:** Bazı modüllerin (örneğin, `MarketScanner` veya `TrendEngine`) daha az sıklıkta çalıştırılması veya sonuçlarının bir sonraki adıma asenkron olarak iletilmesi düşünülebilir. Ancak mevcut mimari, ardışık işlem için tasarlanmıştır, bu nedenle bu, önemli bir mimari değişiklik gerektirebilir.

*   **Logging:**
    *   **Mevcut Durum:** `logging.basicConfig` ile `INFO` seviyesinde loglama yapılmaktadır. Üretim ortamında `DEBUG` seviyesindeki logların `INFO` veya `WARNING` seviyesine çekilmesi, disk I/O yükünü azaltarak performansı artırabilir.
    *   **Öneri:** Log seviyeleri, `config.py` üzerinden kolayca değiştirilebilir hale getirilmeli ve üretim ortamında gereksiz detaylı loglamadan kaçınılmalıdır.

### 2. `watchdog.py` (Servis Sağlığı ve Kurtarma)

`watchdog.py`, sistemin kararlılığını sağlayan kritik bir bileşendir.

*   **Restart Mekanizması:**
    *   **Mevcut Durum:** `watchdog.py`, bir servisin `MAX_RESTART_ATTEMPTS` (3) kez 5 dakika içinde yeniden başlatılmaya çalışıldığında alarm verir ve durur. Bu, servislerin sürekli çöküp kalkmasını engellemek için iyi bir mekanizmadır.
    *   **Öneri:** Yeniden başlatma denemeleri arasında kademeli (exponential backoff) bekleme süreleri uygulanabilir. Bu, geçici sorunlarda servise toparlanma şansı tanırken, kalıcı sorunlarda daha hızlı alarm verilmesini sağlar.

*   **Sağlık Kontrolü:**
    *   **Mevcut Durum:** Dashboard için HTTP sağlık kontrolü (`/api/health`) kullanılmaktadır. Bot servisi için ise sadece `systemctl is-active` kontrolü yapılmaktadır.
    *   **Öneri:** Bot servisi için de daha derinlemesine bir sağlık kontrolü eklenebilir. Örneğin, botun son ne zaman sinyal ürettiği veya son ne zaman bir API çağrısı yaptığı gibi metrikler izlenebilir. Bu, botun aktif olmasına rağmen işlevsel olarak takılıp kalmadığını doğrulamaya yardımcı olur.

### 3. `config.py` (Yapılandırma Parametreleri)

`config.py` içindeki parametreler, sistemin davranışını ve dolayısıyla performansını ve kararlılığını doğrudan etkiler.

*   **`SCAN_INTERVAL`:**
    *   **Mevcut Durum:** Tarama aralığı saniye cinsinden belirlenir. Çok düşük bir değer, Binance API limitlerine takılmaya veya CPU kullanımını artırmaya neden olabilir.
    *   **Öneri:** Bu değerin, API kısıtlamaları ve sistemin işleme kapasitesi göz önünde bulundurularak optimize edilmesi önemlidir. Piyasa koşullarına göre dinamik olarak ayarlanabilen bir `SCAN_INTERVAL` mekanizması, hem performansı hem de adaptasyonu artırabilir.

*   **`MAX_COINS_PER_SCAN_LOOP`:**
    *   **Mevcut Durum:** Her tarama döngüsünde işlenecek maksimum coin sayısını belirler. Bu değerin yüksek olması, döngü başına işlem süresini artırabilir.
    *   **Öneri:** Bu parametre, sistemin anlık işlem kapasitesi ve istenen reaksiyon süresi arasında bir denge kuracak şekilde ayarlanmalıdır. Daha az coin ile daha sık tarama, bazı durumlarda daha iyi performans sağlayabilir.

*   **Risk Yönetimi Parametreleri (`MAX_OPEN_TRADES`, `MAX_CORRELATED_TRADES`, `MAX_PORTFOLIO_EXPOSURE_PCT`):**
    *   **Mevcut Durum:** Bu parametreler doğrudan performansla ilgili olmasa da, sistemin genel kararlılığını ve risk yönetimini etkiler. Yanlış yapılandırıldığında, beklenmedik kayıplara veya fırsatların kaçırılmasına yol açabilir.
    *   **Öneri:** Bu parametrelerin, geçmiş veriler ve backtest sonuçları kullanılarak dikkatlice optimize edilmesi ve piyasa koşullarına göre adaptif olarak ayarlanabilmesi, sistemin uzun vadeli kararlılığı için kritik öneme sahiptir.

### Genel İyileştirme Önerileri

*   **Asenkron Programlama (`asyncio`):** Python'ın `asyncio` kütüphanesi, özellikle I/O yoğun işlemlerde (API çağrıları, veritabanı etkileşimleri) performansı önemli ölçüde artırabilir. Ancak mevcut kod tabanının `asyncio`'ya geçirilmesi büyük bir refactor gerektirecektir.
*   **Uygulama Profil Oluşturma:** `cProfile` veya `line_profiler` gibi Python araçları kullanılarak uygulamanın çalışma zamanı profili çıkarılabilir. Bu, performans darboğazlarını kesin olarak belirlemek ve hedefe yönelik optimizasyonlar yapmak için en etkili yöntemdir.
*   **Veritabanı İndeksleme:** Sık sorgulanan veritabanı tablolarına (örneğin, `trades` tablosundaki `status`, `close_time`, `symbol` gibi kolonlara) uygun indeksler eklenmesi, sorgu sürelerini önemli ölçüde azaltabilir.

Bu faz ile sistemin performans ve kararlılık potansiyeli analiz edilmiş ve iyileştirme alanları belirlenmiştir. Bir sonraki faza geçmeye hazırım.
