#!/bin/bash
# AURVEX — Bot ve Dashboard'u yeniden başlat
set -e

TRADE_DIR=/root/trade_engine
cd $TRADE_DIR

echo "[restart] Servisler durduruluyor..."
systemctl stop ax-bot ax-dashboard 2>/dev/null || true
sleep 2

echo "[restart] Servis dosyaları güncelleniyor..."
cp $TRADE_DIR/ax-bot.service       /etc/systemd/system/ax-bot.service
cp $TRADE_DIR/ax-dashboard.service /etc/systemd/system/ax-dashboard.service
systemctl daemon-reload

echo "[restart] Servisler başlatılıyor..."
systemctl enable ax-bot ax-dashboard
systemctl start ax-bot ax-dashboard
sleep 3

echo ""
echo "=== Durum ==="
systemctl is-active ax-bot      && echo "✓ ax-bot      ÇALIŞIYOR" || echo "✗ ax-bot      DURDU"
systemctl is-active ax-dashboard && echo "✓ ax-dashboard ÇALIŞIYOR" || echo "✗ ax-dashboard DURDU"

echo ""
echo "[restart] TAMAM — http://143.198.90.104:5000"
