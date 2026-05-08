#!/bin/bash
# AURVEX.Ai Deploy — bash deploy.sh
BRANCH=claude/fix-scoring-telegram-ux-YuTYB
PY=/usr/bin/python3
DIR=/root/trade_engine/trade-engine

echo "=== AX DEPLOY ==="
cd $DIR

echo "[1/4] Git pull..."
git fetch origin
git reset --hard origin/$BRANCH

echo "[2/4] Migration..."
$PY scripts/migrate_accounting_schema.py

echo "[3/4] Servisler yeniden başlatılıyor..."
sed -i "s|/usr/local/bin/python3|$PY|g" /etc/systemd/system/aurvex-bot.service
sed -i "s|/usr/local/bin/python3|$PY|g" /etc/systemd/system/aurvex-dashboard.service
systemctl daemon-reload
systemctl restart aurvex-bot aurvex-dashboard
sleep 5

echo "[4/4] Durum..."
systemctl is-active aurvex-bot && echo "BOT: OK" || echo "BOT: FAIL"
systemctl is-active aurvex-dashboard && echo "DASH: OK" || echo "DASH: FAIL"
wget -qO- http://localhost:5000/api/health 2>/dev/null || echo "Dashboard hazir degil"
echo "=== TAMAMLANDI ==="
