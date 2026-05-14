# trade-engine Sunucu Kurulum ve Güncelleme Rehberi (Termius İçin)

**Yazar:** Manus AI  
**Tarih:** 12 Mayıs 2026

Bu rehber, `tuhanba/trade-engine` deposunu Termius gibi bir SSH istemcisi aracılığıyla sunucunuza kurmak veya mevcut bir kurulumu güvenli bir şekilde güncellemek için gerekli tüm adımları ve komutları içermektedir. Özellikle mevcut verilerinizi koruyarak (zararsız) geçiş yapmaya odaklanılmıştır.

## 1. Sunucu Gereksinimleri

Kuruluma başlamadan önce sunucunuzun aşağıdaki minimum gereksinimleri karşıladığından emin olun:

| Bileşen | Minimum | Önerilen |
|---|---|---|
| OS | Ubuntu 22.04 LTS | Ubuntu 22.04 LTS |
| RAM | 1 GB | 2 GB |
| CPU | 1 vCPU | 2 vCPU |
| Disk | 10 GB | 20 GB |
| Python | 3.10+ | 3.11 |

## 2. İlk Kurulum Adımları

Eğer botu ilk kez kuruyorsanız aşağıdaki adımları takip edin:

### Adım 1: Sunucuya Bağlan

Termius veya tercih ettiğiniz SSH istemcisi ile sunucunuza bağlanın:

```bash
ssh root@SUNUCU_IP
```

### Adım 2: Sistemi Güncelle ve Gerekli Paketleri Kur

Sistemi güncelleyin ve gerekli paketleri yükleyin:

```bash
apt-get update -y && apt-get upgrade -y
apt-get install -y git python3 python3-pip python3-venv nginx curl
```

### Adım 3: Repoyu Klonla

Depoyu `/root` dizinine klonlayın. Mevcut bir kurulumunuz varsa, bu adımı atlayıp doğrudan güncelleme bölümüne geçebilirsiniz.

```bash
cd /root
git clone https://github.com/tuhanba/trade-engine.git trade_engine
cd /root/trade_engine
```

### Adım 4: .env Dosyasını Oluştur

`.env` dosyası, botun çalışması için gerekli API anahtarları ve yapılandırma ayarlarını içerir. Bu dosya depoda bulunmadığı için manuel olarak oluşturulmalıdır. Aşağıdaki komutla dosyayı açın ve içeriği yapıştırın, kendi değerlerinizle doldurun:

```bash
nano /root/trade_engine/.env
```

**Örnek .env İçeriği:**

```env
BINANCE_API_KEY=buraya_binance_api_key
BINANCE_API_SECRET=buraya_binance_api_secret
TELEGRAM_BOT_TOKEN=buraya_telegram_bot_token
TELEGRAM_CHAT_ID=buraya_telegram_chat_id
SECRET_KEY=scalp2026
PAPER_BALANCE=250.0
RISK_PCT=1.0
EXECUTION_MODE=paper
DB_PATH=/root/trade_engine/trading.db
```

> **Önemli Not:** `EXECUTION_MODE=paper` ile başlamanız şiddetle tavsiye edilir. Gerçek işlem için `live` olarak değiştirmeniz gerekir. `DB_PATH` değerinin doğru olduğundan emin olun.

Kaydetmek için: `Ctrl+X` → `Y` → `Enter`

### Adım 5: Python Sanal Ortam Kurulumu ve Bağımlılıkları Yükleme

Proje bağımlılıklarını izole etmek için bir sanal ortam oluşturun ve yükleyin:

```bash
cd /root/trade_engine
python3 -m venv venv
source venv/bin/activate
pip install --upgrade pip
pip install -r requirements.txt
```

### Adım 6: Veritabanını Sıfırla (Dikkatli Olun!)

**Bu adım, mevcut trade ve sinyal verilerini silecektir.** Eğer mevcut verilerinizi korumak istiyorsanız, bu adımı atlayın veya `trading.db` dosyasının yedeğini alın. AI Brain öğrenme verileri bu işlemden etkilenmez.

```bash
cd /root/trade_engine
source venv/bin/activate
python3 reset_db.py
```

Komut sizden onay isteyecektir. Devam etmek için `evet` yazın.

### Adım 7: Log Klasörü Oluştur

Botun loglarını kaydetmesi için gerekli klasörü oluşturun:

```bash
mkdir -p /root/trade_engine/logs
```

### Adım 8: Systemd Servislerini Kur

Botun arka planda çalışmasını sağlamak için systemd servislerini yapılandırın:

```bash
cp /root/trade_engine/aurvex-bot.service       /etc/systemd/system/
cp /root/trade_engine/aurvex-dashboard.service /etc/systemd/system/
cp /root/trade_engine/aurvex-watchdog.service  /etc/systemd/system/
systemctl daemon-reload
systemctl enable aurvex-dashboard
systemctl enable aurvex-bot
systemctl enable aurvex-watchdog
```

### Adım 9: Nginx Kurulumu ve Yapılandırması

Dashboard arayüzüne web üzerinden erişim sağlamak için Nginx web sunucusunu kurun ve yapılandırın:

```bash
cat > /etc/nginx/sites-available/aurvex << 'EOF'
server {
    listen 80;
    server_name _;
    location / {
        proxy_pass http://127.0.0.1:5000;
        proxy_http_version 1.1;
        proxy_set_header Upgrade $http_upgrade;
        proxy_set_header Connection "upgrade";
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_read_timeout 86400;
    }
}
EOF

unlink /etc/nginx/sites-enabled/default 2>/dev/null || true
ln -sf /etc/nginx/sites-available/aurvex /etc/nginx/sites-enabled/aurvex
nginx -t
systemctl enable nginx
systemctl restart nginx
ufw allow 80/tcp 2>/dev/null || true
```

### Adım 10: Servisleri Başlat

Tüm servisleri başlatın:

```bash
systemctl start aurvex-dashboard
sleep 3
systemctl start aurvex-bot
sleep 3
systemctl start aurvex-watchdog
```

### Adım 11: Durum Kontrolü

Servislerin düzgün çalışıp çalışmadığını kontrol edin. Hepsi `active (running)` göstermelidir:

```bash
systemctl status aurvex-dashboard --no-pager
systemctl status aurvex-bot --no-pager
systemctl status nginx --no-pager
```

API kontrolü için:

```bash
curl -s http://127.0.0.1:5000/api/ax_status | python3 -m json.tool
```

### Adım 12: Dashboard'a Eriş

Tarayıcınızdan sunucunuzun IP adresini ziyaret ederek dashboard'a erişebilirsiniz:

```
http://SUNUCU_IP
```

## 3. Mevcut Kurulumu Güncelleme

Eğer botunuz zaten kuruluysa ve sadece yeni kodları çekmek istiyorsanız, aşağıdaki adımları izleyin. Bu işlem mevcut verilerinizi koruyacaktır:

```bash
cd /root/trade_engine
git pull origin main
systemctl restart aurvex-bot aurvex-dashboard
```

> **Not:** `git pull` komutu, yerel olarak yaptığınız değişiklikleri (örneğin `.env` dosyasını) koruyacaktır. Ancak, `requirements.txt` dosyasında yeni bağımlılıklar varsa, `pip install -r requirements.txt` komutunu tekrar çalıştırmanız gerekebilir.

## 4. Log Takibi

Botun ve dashboard'un loglarını izlemek için:

```bash
# Bot logları (canlı)
journalctl -u aurvex-bot -f

# Dashboard logları (canlı)
journalctl -u aurvex-dashboard -f

# Dosya bazlı loglar
tail -f /root/trade_engine/logs/bot.log
tail -f /root/trade_engine/logs/dashboard.log
```

## 5. Servis Komutları

Bot servislerini yönetmek için:

```bash
# Durdur
systemctl stop aurvex-bot
systemctl stop aurvex-dashboard

# Yeniden başlat
systemctl restart aurvex-bot
systemctl restart aurvex-dashboard

# Tüm servisleri yeniden başlat
systemctl restart aurvex-bot aurvex-dashboard aurvex-watchdog nginx
```

## 6. Sorun Giderme

Sık karşılaşılan sorunlar ve çözümleri:

### Dashboard açılmıyor

```bash
systemctl status aurvex-dashboard
journalctl -u aurvex-dashboard -n 50 --no-pager
```

### Bot sinyal üretmiyor

```bash
journalctl -u aurvex-bot -n 100 --no-pager | grep -E "ALLOW|VETO|ERROR|sinyal"
```

### Veritabanı bozuldu / sıfırla

**Bu işlem mevcut trade ve sinyal verilerini silecektir.**

```bash
cd /root/trade_engine && source venv/bin/activate
python3 reset_db.py
systemctl restart aurvex-bot aurvex-dashboard
```

### Tüm sistemi sıfırdan kur

**Bu işlem tüm mevcut kurulumu silip yeniden kuracaktır.**

```bash
bash /root/trade_engine/setup_all.sh
```

## Referanslar

[1]: https://github.com/tuhanba/trade-engine "GitHub - tuhanba/trade-engine"
[2]: https://github.com/tuhanba/trade-engine/blob/main/SETUP.md "trade-engine SETUP"
