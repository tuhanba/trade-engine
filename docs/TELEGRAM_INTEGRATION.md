# 📳 Aurvex AI Trading Engine — Telegram Bildirim ve Komut Merkezi

Bu doküman, Telegram asenkron bildirim kuyruğunu, kilitlenmeyen worker modelini, supervisor watchdog yapısını ve interaktif komut yönetim sistemini detaylandırır.

---

## 📥 Kuyruk Yönetimi ve Asenkron Gönderim (Lock-Free Queue)

Telegram mesajlarının gönderimi, ana ticaret motorunu bloke etmemek için asenkron bir kuyruk yapısı üzerinden yürütülür:

- **Bileşenler**: `telegram_delivery.py` içerisindeki `_Queue` sınıfı, `deque` yapısı ve bir arka plan worker thread'i (`tg-queue-worker`).
- **Kilit Altında Uykuda Kalmanın Önlenmesi**: Kuyrukta zamanı gelmemiş mesajlar olduğunda, bekleme uykusu (`time.sleep`) kilit bloğu dışında (`with self._lock:` haricinde) çalıştırılır. Bu sayede, uykuda olunduğu sürece diğer süreçlerin yeni mesaj eklemesi (Push) engellenmez.
- **Kilitsiz Retry Yapısı (No-Block Retry)**: Mesaj gönderiminde geçici bir ağ hatası oluştuğunda, worker thread `time.sleep` ile bekletilmez. Hatalı mesaj, üstel artış gösteren bir bekleme süresiyle (`retry_at = now + backoff`) doğrudan kuyruğun arkasına atılır. Worker thread kuyruktaki diğer sağlıklı mesajları gecikmeden göndermeye devam eder.

---

## 🐕 Supervisor/Watchdog Thread (Sağlık Denetimi)

Telegram worker thread'inin beklenmeyen bir hata, ağ kopması veya kütüphane kilitlenmesiyle çökmesi durumunda bildirimlerin kalıcı olarak kesilmesini önlemek üzere bir izleme sistemi entegre edilmiştir:

- `tg-queue-supervisor` adında ikinci bir thread çalıştırılır.
- Her 5 saniyede bir worker thread'in (`tg-queue-worker`) hayatta olup olmadığını (`is_alive()`) kontrol eder.
- Eğer worker çökmüşse veya yanıt vermiyorsa, supervisor yeni bir worker thread'i otonom olarak başlatır (Self-Healing) ve bekleyen mesajları kaldığı yerden göndermeye devam ettirir.

---

## 🎛️ İnteraktif Telegram Komut Arayüzü

Yatırımcı veya operatör, botu doğrudan Telegram üzerinden yönetebilir (`telegram_manager.py`):

- **/friday komutu**: Friday AI CEO'sunu çağırır ve interaktif butonlardan oluşan bir kontrol paneli açar.
- **İnteraktif Kontroller**:
  - **Teşhis**: Anlık sistem sağlık ve bağlantı taramasını tetikler.
  - **Grafik**: Matplotlib ile çizilen anlık bakiye gelişim eğrisini (Equity Curve) ve açık pozisyonları görsel rapor olarak gönderir.
  - **Rapor**: edge-tts ile sentezlenen sesli günlük finansal durumu boss'una iletir.
  - **Veto Butonları**: Friday'in ürettiği veto kararlarına boss'un onay vermesi veya reddetmesi için inline buton onay bariyerleri sunar.
- **Çoklu Chat Gönderimi (Multi-channel Routing - Faz 3)**: `TELEGRAM_CHAT_ID` parametresinde virgülle ayrılmış birden fazla chat ID tanımlıysa, tüm sistem bildirimleri paralel olarak tanımlanan tüm sohbet ve gruplara aynı anda iletilir.
