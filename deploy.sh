#!/bin/bash
# deploy.sh — AX Faz 1 Kurulum Sırası
# Her satırı AYRI AYRI çalıştır.
# Sorun olursa bir sonrakine geçme.

# ─── 1. Repoya gir ───────────────────────────────────────────────
cd /root/trade-engine

# ─── 2. Branch'i çek ─────────────────────────────────────────────
git fetch origin

# ─── 3. Çalışma branch'ine geç ───────────────────────────────────
git checkout claude/setup-ax-database-Wjwr6

# ─── 4. Son değişiklikleri al ────────────────────────────────────
git pull origin claude/setup-ax-database-Wjwr6

# ─── 5. venv oluştur (yoksa) ─────────────────────────────────────
python3 -m venv /root/trade-engine/venv

# ─── 6. pip güncelle ─────────────────────────────────────────────
/root/trade-engine/venv/bin/pip install --upgrade pip

# ─── 7. Bağımlılıkları kur ───────────────────────────────────────
/root/trade-engine/venv/bin/pip install python-dotenv python-binance flask flask-socketio requests

# ─── 8. logs/ klasörünü oluştur ──────────────────────────────────
mkdir -p /root/trade-engine/logs

# ─── 9. .env dosyasını kontrol et ────────────────────────────────
cat /root/trade-engine/.env

# ─── 10. DB'yi başlat ────────────────────────────────────────────
/root/trade-engine/venv/bin/python3 -c "from database import init_db; init_db(); print('DB OK')"

# ─── 11. Coin profillerini yükle ─────────────────────────────────
/root/trade-engine/venv/bin/python3 -c "from coin_library import seed_initial_profiles; seed_initial_profiles(); print('Coins OK')"

# ─── 12. Bot servisini kopyala ───────────────────────────────────
cp /root/trade-engine/aurvex-bot.service /etc/systemd/system/aurvex-bot.service

# ─── 13. Dashboard servisini kopyala ─────────────────────────────
cp /root/trade-engine/aurvex-dashboard.service /etc/systemd/system/aurvex-dashboard.service

# ─── 14. n8n bridge servisini kopyala ────────────────────────────
cp /root/trade-engine/aurvex-n8n-bridge.service /etc/systemd/system/aurvex-n8n-bridge.service

# ─── 15. systemd'yi yenile ───────────────────────────────────────
systemctl daemon-reload

# ─── 16. Servisleri etkinleştir (boot'ta başlasın) ───────────────
systemctl enable aurvex-bot.service

# ─── 17. Dashboard'u etkinleştir ─────────────────────────────────
systemctl enable aurvex-dashboard.service

# ─── 18. Bot'u başlat ────────────────────────────────────────────
systemctl start aurvex-bot.service

# ─── 19. Dashboard'u başlat ──────────────────────────────────────
systemctl start aurvex-dashboard.service

# ─── 20. Bot durumunu kontrol et ─────────────────────────────────
systemctl status aurvex-bot.service

# ─── 21. Dashboard durumunu kontrol et ───────────────────────────
systemctl status aurvex-dashboard.service

# ─── 22. logrotate'i kur ─────────────────────────────────────────
cp /root/trade-engine/logrotate.conf /etc/logrotate.d/aurvex

# ─── 23. logrotate test et ───────────────────────────────────────
logrotate --debug /etc/logrotate.d/aurvex

# ─── 24. Canlı log izle (bot) ────────────────────────────────────
tail -f /root/trade-engine/logs/ax_bot.log
