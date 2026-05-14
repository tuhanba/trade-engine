# trade-engine Bağlantı Özeti

**Yazar:** Manus AI  
**Tarih:** 12 Mayıs 2026

GitHub deposuna başarıyla erişildi ve depo yerel çalışma alanına klonlandı. Yerel kopya `/home/ubuntu/trade-engine-work` dizinindedir; uzak depo `origin` olarak `https://github.com/tuhanba/trade-engine.git` adresine bağlıdır. Aktif dal `main`, son görülen commit ise `2526ddd` numaralı “Dashboard'a Sinyal Arşivi sekmesi ve API endpoint'i eklendi” kaydıdır.[1]

| Başlık | Durum |
|---|---|
| GitHub erişimi | Başarılı |
| Yerel klon | `/home/ubuntu/trade-engine-work` |
| Aktif dal | `main` |
| Uzak bağlantı | `origin` fetch/push hazır |
| Syntax kontrolü | `app.py`, `scalp_bot.py`, `database.py`, `dashboard_service.py` için başarılı |
| Hassas işlem | Binance/Telegram anahtarları olmadan canlı bağlantı başlatılmadı |

Depo, README içeriğine göre **AX Scalp Engine** adlı bir kripto scalp trade motorudur. Mimari akış `Market Scanner → Trend Engine → Trigger Engine → Risk Engine → AI Decision Engine → Data Layer → Dashboard + Telegram` şeklinde kurgulanmıştır; proje kuralı olarak Dashboard ve Telegram tarafına ham veri değil, yalnızca Data Layer’dan doğrulanmış veri gönderilmesi hedeflenmiştir.[2]

| Bileşen | Ana dosya | Görev özeti |
|---|---|---|
| Bot ana döngüsü | `scalp_bot.py` | Binance istemcisini, tarama motorunu, trend/trigger/risk/AI karar katmanlarını ve Telegram/işlem akışını orkestre eder. |
| Dashboard/API | `app.py` | Flask ve SocketIO tabanlı paneli, sağlık kontrolünü, sinyal arşivini, trade geçmişini ve istatistik endpointlerini sunar. |
| Veri katmanı | `database.py`, `core/data_layer.py` | SQLite şemalarını, sinyal/trade/paper sonuçlarını ve tekil `SignalData` modelini yönetir. |
| Risk ve işlem | `core/risk_engine.py`, `execution_engine.py` | SL/TP/RR, pozisyon büyüklüğü, paper trade açma ve açık trade yönetimi mantığını taşır. |
| Bildirim | `telegram_delivery.py`, `telegram_manager.py` | Telegram mesaj formatlama, kuyruklama, duplicate engelleme ve kontrol komutlarını sağlar. |

Kurulum dokümanı, üretim ortamı için Ubuntu 22.04, Python 3.10+, Nginx, systemd servisleri ve `.env` üzerinden Binance/Telegram anahtarları ile çalışmayı tarif eder. Güvenli başlangıç önerisi `EXECUTION_MODE=paper` modudur; gerçek işlem için ayrıca live moda geçilmesi gerekmektedir.[3]

> Önemli not: Depoda README içinde `.env.example` kopyalama adımı yazmasına rağmen yerel dosya kontrolünde `.env.example` bulunmadı. Bu nedenle kurulumdan önce manuel `.env` dosyası oluşturulmalı veya örnek ortam dosyası repoya eklenmelidir.

Şu anda bağlantı kurulmuş ve kod tabanı incelenmiştir. İsterseniz bir sonraki adımda bu depoyu **yerel dashboard olarak çalıştırabilir**, eksik `.env.example` dosyasını hazırlayabilir, testleri çalıştırabilir veya belirli bir hata/özelliği geliştirmeye başlayabilirim.

## References

[1]: https://github.com/tuhanba/trade-engine "GitHub - tuhanba/trade-engine"  
[2]: https://github.com/tuhanba/trade-engine/blob/main/README.md "trade-engine README"  
[3]: https://github.com/tuhanba/trade-engine/blob/main/SETUP.md "trade-engine SETUP"
