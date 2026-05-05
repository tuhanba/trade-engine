# AURVEX.Ai — Sunucu Kurulum Rehberi v3.0

> **Sistem:** AX Trading Engine | 10/10 Kalite Filtresi (S / A+ / A)
> **Önemli:** Repo dizini `/root/trade-engine` (tire ile) olmalıdır.

---

## Sunucu Gereksinimleri

| Bileşen | Minimum | Önerilen |
|---|---|---|
| OS | Ubuntu 22.04 LTS | Ubuntu 22.04 LTS |
| RAM | 1 GB | 2 GB |
| CPU | 1 vCPU | 2 vCPU |
| Disk | 10 GB | 20 GB |
| Python | 3.10+ | 3.11 |

---

## ADIM 1 — Sistemi Güncelle

```bash
apt-get update -y && apt-get upgrade -y
apt-get install -y git python3 python3-pip python3-venv nginx curl
```

---

## ADIM 2 — Repoyu Klonla

```bash
cd /root
git clone https://github.com/tuhanba/trade-engine.git trade-engine
cd /root/trade-engine
```

> **Not:** Dizin adı `trade-engine` (tire ile) olmalıdır.

---

## ADIM 3 — .env Dosyasını Oluştur

```bash
cat > /root/trade-engine/.env << 'EOF'
BINANCE_API_KEY=buraya_binance_api_key
BINANCE_API_SECRET=buraya_binance_api_secret
TELEGRAM_BOT_TOKEN=buraya_telegram_bot_token
TELEGRAM_CHAT_ID=buraya_telegram_chat_id
SECRET_KEY=scalp2026
PAPER_BALANCE=250.0
RISK_PCT=1.0
EXECUTION_MODE=paper
DB_PATH=/root/trade-engine/trading.db
EOF
```

---

## ADIM 4 — Python Sanal Ortam Kur

```bash
cd /root/trade-engine
python3 -m venv venv
source venv/bin/activate
pip install --upgrade pip
pip install -r requirements.txt
```

---

## ADIM 5 — Veritabanını Başlat

```bash
cd /root/trade-engine
source venv/bin/activate
python3 -c "from database import init_db; init_db(); print('DB hazır')"
```

---

## ADIM 6 — Log Klasörü Oluştur

```bash
mkdir -p /root/trade-engine/logs
touch /root/trade-engine/logs/{bot,dashboard,telegram,watchdog,error}.log
```

---

## ADIM 7 — nginx Yapılandır

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
nginx -t && systemctl enable nginx && systemctl restart nginx
ufw allow 80/tcp 2>/dev/null || true
```

---

## ADIM 8 — Systemd Servislerini Kur

```bash
cp /root/trade-engine/aurvex-bot.service       /etc/systemd/system/
cp /root/trade-engine/aurvex-dashboard.service /etc/systemd/system/
cp /root/trade-engine/aurvex-watchdog.service  /etc/systemd/system/
cp /root/trade-engine/aurvex-telegram.service  /etc/systemd/system/
systemctl daemon-reload
systemctl enable aurvex-bot aurvex-dashboard aurvex-watchdog aurvex-telegram
```

---

## ADIM 9 — Servisleri Başlat

```bash
systemctl start aurvex-dashboard
sleep 3
systemctl start aurvex-bot
sleep 3
systemctl start aurvex-watchdog
sleep 2
systemctl start aurvex-telegram
```

---

## ADIM 10 — Durum Kontrolü

```bash
systemctl status aurvex-bot.service --no-pager
systemctl status aurvex-dashboard.service --no-pager
systemctl status aurvex-telegram.service --no-pager
curl -s http://127.0.0.1:5000/api/health | python3 -m json.tool
```

---

## Güncelleme (Sonraki Seferler)

```bash
cd /root/trade-engine
git pull origin main
bash deploy.sh
```

> `deploy.sh` otomatik olarak: servisleri durdurur → DB yedekler → migration çalıştırır → servisleri başlatır → 7 API endpoint'i test eder.

---

## Servisler

| Servis | Dosya | Log |
|---|---|---|
| Scalp Bot | `scalp_bot_v3.py` | `logs/bot.log` |
| Dashboard | `app.py` | `logs/dashboard.log` |
| Watchdog | `watchdog.py` | `logs/watchdog.log` |
| Telegram Bot | `telegram_bot.py` | `logs/telegram.log` |

---

## Telegram Komutları

| Komut | Açıklama |
|---|---|
| `/status` | Bot, dashboard, DB, Binance durumu |
| `/live` | Aktif açık tradeler |
| `/signals` | Son scalp sinyalleri |
| `/watchlist` | B kalite izleme listesi |
| `/stats` | Winrate, PnL, sinyal istatistikleri |
| `/last` | Son kapanan trade |
| `/risk` | Risk ayarları ve kalite filtreleri |
| `/health` | Servis sağlık kontrolü |
| `/restart_info` | Servis durumları |
| `/help` | Tüm komutlar |

---

## API Endpoint'leri

| Endpoint | Açıklama |
|---|---|
| `GET /api/health` | Sistem sağlık durumu |
| `GET /api/stats` | İstatistikler |
| `GET /api/live` | Aktif tradeler |
| `GET /api/scalp_signals` | Son sinyaller |
| `GET /api/watchlist` | B kalite watchlist |
| `GET /api/last` | Son kapanan trade |
| `GET /api/risk` | Risk ayarları |
| `POST /api/reset` | Kasa sıfırla |

---

## Faydalı Komutlar

```bash
# Canlı log takibi
tail -f /root/trade-engine/logs/bot.log
tail -f /root/trade-engine/logs/telegram.log

# Servis logları
journalctl -u aurvex-bot.service -f
journalctl -u aurvex-telegram.service -f

# Servis yeniden başlatma
systemctl restart aurvex-bot.service
systemctl restart aurvex-telegram.service

# Tüm servisleri yeniden başlat
systemctl restart aurvex-bot aurvex-dashboard aurvex-watchdog aurvex-telegram

# API testi
curl http://localhost:5000/api/health | python3 -m json.tool
curl http://localhost:5000/api/risk   | python3 -m json.tool
```

---

## Kalite Sistemi (10/10)

| Kalite | Davranış |
|---|---|
| S / A+ / A | Trade açılır |
| B | Watchlist'e alınır, öğrenme sistemine kaydedilir |
| C / D | Veto edilir, trade açılmaz |

---

## Sorun Giderme

### Dashboard açılmıyor
```bash
systemctl status aurvex-dashboard
journalctl -u aurvex-dashboard -n 50 --no-pager
```

### Bot sinyal üretmiyor
```bash
journalctl -u aurvex-bot -n 100 --no-pager | grep -E "ALLOW|VETO|ERROR|sinyal"
```

### Telegram bot çalışmıyor
```bash
systemctl status aurvex-telegram
tail -50 /root/trade-engine/logs/telegram.log
```

### DB sıfırla
```bash
curl -X POST http://localhost:5000/api/reset \
  -H "Content-Type: application/json" \
  -d '{"initial_balance": 250, "keep_ai_learning": true}'
```

---

## Sistem Parametreleri

| Parametre | Değer |
|---|---|
| Trade Kalitesi | S, A+, A |
| Watchlist | B |
| Veto | C, D |
| Max Açık Trade | 10 |
| Risk/Trade | %1 |
| Günlük Max Kayıp | %5 |
| Circuit Breaker | 3 kayıp → 60 dk duraklama |
| Trade Eşiği | 70 |
| Max Sinyal/Gün | 40 |
