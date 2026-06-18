# 🔒 Aurvex AI Trading Engine — Dashboard Arayüzü ve Güvenlik Altyapısı

Bu doküman, web paneli arayüzünü, HTTP API mimarisini, Socket.IO WebSockets yapısını, IP whitelist kalkanını ve PIN kilit doğrulama sistemini detaylandırır.

---

## 🎨 Premium Glassmorphic Web Dashboard

Aurvex dashboard'u, kullanıcı deneyimini en üst seviyeye taşımak amacıyla modern **Glassmorphic (Buzlu Cam efekti)** ve karanlık mod (dark-mode) temasıyla tasarlanmıştır:

- **Canlı Durum Kartları**: Cüzdan bakiyesi, günlük PnL, açık pozisyon sayıları, win-rate oranı ve kâr faktörü (Profit Factor) dinamik gösterilir.
- **Sinyal Hunisi (Funnel Stages)**: Taranan coin sayısından elenenlere, risk onayından geçenlere ve işleme dönüşenlere kadar olan filtre süreçleri huni animasyonuyla görselleştirilir.
- **Canlı Konsol Raporu (Logs Terminal)**: Sunucu logları ve işlem detayları gerçek zamanlı akış halinde izlenebilir.
- **Friday Live Chat**: Web panelinin sağ alt köşesinde yer alan sohbet arayüzünden Friday AI ile sohbet edilebilir ve otonom görevler (bakım, teşhis, grafik vb.) tetiklenebilir.

---

## 🔒 Güvenlik Altyapısı

Dashboard'un internete açık ortamlarda güvenle barındırılabilmesi için çok katmanlı bir koruma yapısı kurulmuştur:

### 1. IP Whitelist Kalkanı (Erişim Kontrolü)
- `.env` dosyasındaki `ALLOWED_IPS` listesi üzerinden çalışır.
- Tanımlı IP adresleri dışındaki hiçbir IP'nin `/api/*` uç noktalarına erişmesine izin verilmez, doğrudan HTTP 403 Forbidden yanıtı dönülür.
- Nginx proxy arkasındaki kurulumlar için `X-Forwarded-For` ve `X-Real-IP` başlıkları doğrulanarak gerçek istemci IP'si saptanır.

### 2. PIN Kodu Kilit Ekranı (DASHBOARD_PIN)
- `.env` dosyasında `DASHBOARD_PIN` tanımlandığı durumlarda, dashboard'a ilk girişte şık buzlu cam efektli bir PIN kilit ekranı kullanıcıyı karşılar.
- Girilen PIN kodu doğrulanmadan hiçbir hassas finansal veri dashboard arayüzüne sızdırılmaz.
- PIN kodu istemci tarafında `localStorage` üzerinde güvenli şekilde saklanır.

### 3. HTTP API Yetkilendirme Denetimi
- Flask web sunucusunda (`app.py`), `@app.before_request` dekoratörüyle `/api/*` veya `/stream` yollarına gelen her HTTP isteği kesilir.
- İstekteki header (`X-Dashboard-PIN`), query parametreleri (`?pin=`) veya cookie (`dashboard_pin`) değerleri doğrulanır. Eşleşmeyen istekler anında HTTP 401 Unauthorized ile reddedilir.

### 4. WebSocket (Socket.IO) Güvenlik Doğrulaması
- Dashboard ile sunucu arasındaki canlı veri akışı (WebSockets) `websocket_events.py` üzerindeki `@socketio.on('connect')` olayıyla korunur.
- İstemci ilk el sıkışma (handshake) anında geçerli bir PIN parametresi göndermelidir. PIN doğrulanmadığı takdirde WebSocket bağlantısı sunucu tarafından doğrudan koparılır (rejection).
