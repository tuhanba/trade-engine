#!/bin/bash
# AURVEX.Ai Deploy — bash deploy.sh
BRANCH=claude/fix-scoring-telegram-ux-YuTYB
PY=/usr/bin/python3
DIR=/root/trade_engine/trade-engine

echo "=== AX DEPLOY BASLIYOR ==="
cd $DIR

echo "[1/6] Servisler durduruluyor..."
systemctl stop aurvex-bot aurvex-dashboard aurvex-watchdog 2>/dev/null || true
sleep 3
echo "Servisler durduruldu."

echo "[2/6] Git pull..."
git fetch origin
git reset --hard origin/$BRANCH
echo "Kod guncellendi."

echo "[3/6] Migration..."
$PY scripts/migrate_accounting_schema.py
echo "Migration tamam."

echo "[4/6] Service dosyalari guncelleniyor..."
cp aurvex-bot.service /etc/systemd/system/
cp aurvex-dashboard.service /etc/systemd/system/
cp aurvex-watchdog.service /etc/systemd/system/ 2>/dev/null || true
sed -i "s|/usr/local/bin/python3|$PY|g" /etc/systemd/system/aurvex-bot.service
sed -i "s|/usr/local/bin/python3|$PY|g" /etc/systemd/system/aurvex-dashboard.service
systemctl daemon-reload
echo "Servisler guncellendi."

echo "[5/6] Servisler baslatiliyor..."
systemctl start aurvex-bot
sleep 3
systemctl start aurvex-dashboard
sleep 3
systemctl start aurvex-watchdog 2>/dev/null || true
sleep 2

echo "[6/6] Durum kontrol..."
systemctl is-active aurvex-bot     && echo "BOT:       OK" || echo "BOT:       FAIL"
systemctl is-active aurvex-dashboard && echo "DASHBOARD: OK" || echo "DASHBOARD: FAIL"
systemctl is-active aurvex-watchdog  && echo "WATCHDOG:  OK" || echo "WATCHDOG:  SKIP"
wget -qO- http://localhost:5000/api/health 2>/dev/null || echo "Dashboard henuz hazir degil"

echo "=== TAMAMLANDI ==="
