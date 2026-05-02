#!/bin/bash
# AURVEX.Ai Auto Deploy Script
# Kullanım: bash deploy.sh
set -e

echo "=== AURVEX.Ai Deploy Başlıyor ==="
cd /root/trade_engine

echo "[1/4] Git pull..."
git pull origin main

echo "[2/4] DB migrate..."
venv/bin/python3 -c "from database import init_db; init_db(); print('DB OK')"

echo "[3/4] Servisler yeniden başlatılıyor..."
systemctl restart aurvex-bot aurvex-dashboard
sleep 3

echo "[4/4] Durum kontrolü..."
systemctl is-active aurvex-bot && echo "aurvex-bot: AKTIF" || echo "aurvex-bot: HATA"
systemctl is-active aurvex-dashboard && echo "aurvex-dashboard: AKTIF" || echo "aurvex-dashboard: HATA"

echo "=== Deploy Tamamlandi ==="
