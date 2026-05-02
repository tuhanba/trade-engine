# AURVEX.Ai — Sunucu Kurulum Rehberi

> **Sistem:** AX Trading Engine v3.0 | 92 Coin | A+ / S Sınıfı Filtre

---

## ADIM 0 — Sunucu Gereksinimleri

| Bileşen | Minimum | Önerilen |
|---|---|---|
| OS | Ubuntu 22.04 LTS | Ubuntu 22.04 LTS |
| RAM | 1 GB | 2 GB |
| CPU | 1 vCPU | 2 vCPU |
| Disk | 10 GB | 20 GB |
| Python | 3.10+ | 3.11 |

---

## ADIM 1 — Sunucuya Bağlan

```bash
ssh root@SUNUCU_IP
```

---

## ADIM 2 — Sistemi Güncelle

```bash
apt-get update -y && apt-get upgrade -y
apt-get install -y git python3 python3-pip python3-venv nginx curl
```

---

## ADIM 3 — Repoyu Klonla

```bash
cd /root
git clone https://github.com/tuhanba/trade-engine.git trade_engine
cd /root/trade_engine
```

---

## ADIM 4 — .env Dosyasını Oluştur

```bash
nano /root/trade_engine/.env
```

Aşağıdaki içeriği yapıştır ve kendi değerlerinle doldur:

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

> **Not:** `EXECUTION_MODE=paper` ile başla. Gerçek işlem için `live` yap.

Kaydet: `Ctrl+X` → `Y` → `Enter`

---

## ADIM 5 — Python Sanal Ortam Kur

```bash
cd /root/trade_engine
python3 -m venv venv
source venv/bin/activate
pip install --upgrade pip
pip install -r requirements.txt
```

---

## ADIM 6 — Veritabanını Sıfırla (Temiz Başlangıç)

```bash
cd /root/trade_engine
source venv/bin/activate
python3 reset_db.py
```

Çıktı şöyle görünmeli:

```
==================================================
AX SİSTEM SIFIRLAMA
==================================================
Bu işlem trade ve sinyal verilerini siler.
AI Brain öğrenme verileri KORUNUR.

Devam etmek istiyor musunuz? (evet/hayır): evet

[RESET] DB: /root/trade_engine/trading.db
[RESET] Temizleniyor...
  ✅ trades temizlendi
  ✅ signal_candidates temizlendi
  ✅ scalp_signals temizlendi
  ✅ daily_summary temizlendi
  ✅ weekly_summary temizlendi
  ✅ Bakiye $10,000 olarak sıfırlandı
  ✅ 92 coin parametresi yüklendi

[RESET] ✅ Tamamlandı
```

---

## ADIM 7 — Log Klasörü Oluştur

```bash
mkdir -p /root/trade_engine/logs
```

---

## ADIM 8 — Systemd Servislerini Kur

```bash
cp /root/trade_engine/aurvex-bot.service       /etc/systemd/system/
cp /root/trade_engine/aurvex-dashboard.service /etc/systemd/system/
cp /root/trade_engine/aurvex-watchdog.service  /etc/systemd/system/
systemctl daemon-reload
systemctl enable aurvex-dashboard
systemctl enable aurvex-bot
systemctl enable aurvex-watchdog
```

---

## ADIM 9 — nginx Kur ve Yapılandır

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

---

## ADIM 10 — Servisleri Başlat

```bash
systemctl start aurvex-dashboard
sleep 3
systemctl start aurvex-bot
sleep 3
systemctl start aurvex-watchdog
```

---

## ADIM 11 — Durum Kontrolü

```bash
systemctl status aurvex-dashboard --no-pager
systemctl status aurvex-bot --no-pager
systemctl status nginx --no-pager
```

Hepsi `active (running)` göstermeli.

API kontrolü:

```bash
curl -s http://127.0.0.1:5000/api/ax_status | python3 -m json.tool
```

---

## ADIM 12 — Dashboard'a Eriş

Tarayıcıdan aç:

```
http://SUNUCU_IP
```

---

## Güncelleme (Sonraki Seferler)

Yeni kod geldiğinde tek komutla güncelle:

```bash
cd /root/trade_engine
git pull origin main
systemctl restart aurvex-bot aurvex-dashboard
```

---

## Log Takibi

```bash
# Bot logları (canlı)
journalctl -u aurvex-bot -f

# Dashboard logları (canlı)
journalctl -u aurvex-dashboard -f

# Dosya bazlı loglar
tail -f /root/trade_engine/logs/bot.log
tail -f /root/trade_engine/logs/dashboard.log
```

---

## Servis Komutları

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

### DB bozuldu / sıfırla
```bash
cd /root/trade_engine && source venv/bin/activate
python3 reset_db.py
systemctl restart aurvex-bot aurvex-dashboard
```

### Tüm sistemi sıfırdan kur
```bash
bash /root/trade_engine/setup_all.sh
```

---

## Sistem Parametreleri (Özet)

| Parametre | Değer |
|---|---|
| Coin Evreni | 92 coin (backtest kanıtlı) |
| Sinyal Kalitesi | S ve A+ sınıfı |
| Stop Loss | 1.2x ATR |
| TP1 / TP2 / TP3 | 1.0R / 2.0R / 3.0R |
| ADX Eşiği | 28 |
| Max Açık Trade | 2 |
| Günlük Max Kayıp | %3 |
| Circuit Breaker | 3 kayıp → 120 dk duraklama |
| AI Brain Adaptasyon | Her 30 dakikada bir |
