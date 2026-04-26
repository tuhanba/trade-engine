#!/bin/bash
# AX Systemd Servis Kurulumu
set -e

TRADE_DIR=/root/trade_engine

echo "=== AX Servis Kurulumu ==="

# Servis dosyalarını kopyala
cp $TRADE_DIR/ax-bot.service       /etc/systemd/system/ax-bot.service
cp $TRADE_DIR/ax-dashboard.service /etc/systemd/system/ax-dashboard.service

# Eski app.py process'ini durdur (zaten çalışıyorsa)
pkill -f "python3.*app.py" 2>/dev/null || true
sleep 1

# Systemd yenile
systemctl daemon-reload

# Servisleri etkinleştir ve başlat
systemctl enable ax-bot ax-dashboard
systemctl start ax-bot ax-dashboard

sleep 3

# Durum göster
echo ""
echo "=== Servis Durumu ==="
systemctl status ax-bot --no-pager -l | head -20
echo ""
systemctl status ax-dashboard --no-pager -l | head -20

echo ""
echo "=== Log (son 20 satır) ==="
journalctl -u ax-bot -n 20 --no-pager
