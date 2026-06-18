# ⚡ Aurvex AI Trading Engine — İşlem Gönderim Motoru ve Güvenilirlik Çözümleri

Bu doküman, canlı/sanal işlem gönderim mekanizmalarını, endpoint hot-swap yapılarını, latency clutch sistemini ve Redis tabanlı yedek veri kurtarma (fallback) shims'lerini detaylandırır.

---

## 📈 Canlı ve Sanal İşlem Gönderimi (Execution Mode)

Sistem iki farklı modda çalışabilir:
1. **Paper Mode (Sanal)**: Gerçek bakiye riske atılmadan Binance fiyat tahtası üzerinde sanal TP/SL emirleri simüle edilir. Veriler veritabanına kaydedilir.
2. **Live Mode (Canlı)**: Binance API anahtarlarıyla gerçek kaldıraçlı kontrat emirleri gönderilir.
- **Limit Chase (Fiyat Kovalayan Limit Emir)**: Sistem, slipaj maliyetlerini en aza indirmek için market emri yerine LIMIT emir gönderir. Eğer fiyat emirden uzaklaşırsa, emir iptal edilip güncel fiyata yakın bir seviyeden tekrar gönderilerek kovalanır.

---

## 🔌 API Hot-Swap (Yedek Sunucu Geçişi)

Yüksek frekanslı işlemlerde Binance API uç noktalarının (endpoint) erişilebilirliği kritiktir. Canlı bağlantı gecikmelerini aşmak için **Binance Endpoint Hot-Swap** mekanizması entegre edilmiştir:

- Binance futures isteklerinin gecikme süresi (latency) izlenir.
- Eğer son istek gecikmesi **> 300ms** olursa veya API zaman aşımı (timeout) hatası verirse, sistem otomatik olarak yedek Binance API uç noktalarına sırayla geçiş yapar:
  1. `fapi.binance.com` (Varsayılan)
  2. `fapi.binance.co` (Yedek 1)
  3. `fapi.binance.info` (Yedek 2)
  4. `fapi.binance.net` (Yedek 3)
- Geçiş işlemi çalışma zamanında, botu kapatıp açmaya gerek kalmadan gerçekleştirilir.

---

## 🛡️ Latency-Arbitrage Clutch (Gecikme Debriyajı)

Ağ gecikmesinin çok yüksek olduğu veya Binance sunucularının yavaş yanıt verdiği durumlarda slipaj (slippage) riskini önlemek için **Latency-Arbitrage Clutch** devrededir:

- Sistem gecikmesi **> 300ms** değerinin üzerine çıktığında, Limit Chase fonksiyonundaki `allow_market_fallback` parametresi otonom olarak `False` konumuna getirilir.
- Bu sayede, emrin dolmaması halinde piyasa fiyatından (market order) zorla doldurma mekanizması kapatılarak, yüksek slipajlı kötü bir fiyattan işleme girilmesi engellenir. İşlem kesinlikle limit kovalama ile sınırlandırılır.

---

## 💾 Redis Hot-Swap Backups (Veritabanı Yedekleri)

SQLite veya PostgreSQL ilişkisel veritabanlarının kilitlenmesi veya erişilemez olması durumunda (örn: `OperationalError: database is locked`) sistemin kilitlenmesini önleyen bir Redis shim katmanı yazılmıştır:

- Veritabanına yazılan tüm kritik durum verileri (`active_trades`, `system_state`, `signal_candidates`) eş zamanlı olarak Redis bellek içi veri tabanında yedek anahtarlara (`backup:trades:{id}`, vb.) yazılır.
- SQL veritabanı kilitlendiği anda okuma istekleri (Lookup) otomatik olarak Redis yedeklerinden karşılanır. Bu sayede ilişkisel veritabanı uykudan uyanana kadar botun veri akışı kesintiye uğramaz.
