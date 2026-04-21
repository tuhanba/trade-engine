#!/bin/bash
# AURVEX.Ai — Master Kurulum Scripti
# Tek komutla tüm sistemi kurar ve başlatır
# Kullanım: bash /root/trade_engine/setup_all.sh

set -e
REPO="/root/trade_engine"
VENV="$REPO/venv"

echo "╔══════════════════════════════════════╗"
echo "║   AURVEX.Ai — Master Kurulum         ║"
echo "╚══════════════════════════════════════╝"

# 1. Repo güncelle
echo ""
echo "▶ [1/7] Repo güncelleniyor..."
cd "$REPO"
git fetch origin
git reset --hard origin/main
echo "   ✓ Repo güncellendi"

# 2. nginx kur ve yapılandır
echo ""
echo "▶ [2/7] nginx kuruluyor..."
apt-get install -y nginx -q
cat > /etc/nginx/sites-available/aurvex << 'NGINXEOF'
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
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_read_timeout 86400;
    }
}
NGINXEOF
unlink /etc/nginx/sites-enabled/default 2>/dev/null || true
ln -sf /etc/nginx/sites-available/aurvex /etc/nginx/sites-enabled/aurvex
nginx -t
systemctl enable nginx
systemctl restart nginx
ufw allow 80/tcp 2>/dev/null || true
echo "   ✓ nginx hazır — http://$(curl -s ifconfig.me 2>/dev/null || echo '143.198.90.104')"

# 3. Python bağımlılıkları
echo ""
echo "▶ [3/7] Python bağımlılıkları kontrol ediliyor..."
if [ -d "$VENV" ]; then
    "$VENV/bin/pip" install --quiet flask flask-socketio python-dotenv python-binance scikit-learn pandas requests 2>/dev/null || true
    echo "   ✓ Bağımlılıklar güncellendi"
else
    echo "   ⚠ venv bulunamadı: $VENV"
fi

# 4. Dashboard servisi kur
echo ""
echo "▶ [4/7] Dashboard servisi kuruluyor..."
cp "$REPO/aurvex-dashboard.service" /etc/systemd/system/
echo "   ✓ aurvex-dashboard.service kopyalandı"

# 5. Bot servisi kur
echo ""
echo "▶ [5/7] Bot servisi kuruluyor..."
cp "$REPO/aurvex-bot.service" /etc/systemd/system/
echo "   ✓ aurvex-bot.service kopyalandı"

# 6. Servisleri yeniden yükle ve başlat
echo ""
echo "▶ [6/7] Servisler başlatılıyor..."
systemctl daemon-reload
systemctl enable aurvex-dashboard
systemctl enable aurvex-bot
systemctl restart aurvex-dashboard
sleep 3
systemctl restart aurvex-bot
sleep 3
echo "   ✓ Servisler başlatıldı"

# 7. Durum kontrolü
echo ""
echo "▶ [7/7] Durum kontrolü..."
echo ""
echo "─── Dashboard ───"
systemctl is-active aurvex-dashboard && echo "   ✅ aurvex-dashboard: AKTİF" || echo "   ❌ aurvex-dashboard: KAPALI"
echo ""
echo "─── Bot ───"
systemctl is-active aurvex-bot && echo "   ✅ aurvex-bot: AKTİF" || echo "   ❌ aurvex-bot: KAPALI"
echo ""
echo "─── nginx ───"
systemctl is-active nginx && echo "   ✅ nginx: AKTİF" || echo "   ❌ nginx: KAPALI"
echo ""
echo "─── Port 5000 ───"
sleep 2
curl -s http://127.0.0.1:5000/api/stats | python3 -c "import sys,json; d=json.load(sys.stdin); print('   ✅ Dashboard API: OK' if d.get('ok') else '   ⚠ Dashboard API: ' + str(d))" 2>/dev/null || echo "   ⚠ Dashboard henüz başlamıyor olabilir, 10sn bekleyin"

echo ""
echo "╔══════════════════════════════════════╗"
echo "║   Kurulum tamamlandı!                ║"
echo "║   Dashboard: http://143.198.90.104   ║"
echo "╚══════════════════════════════════════╝"
echo ""
echo "Log izlemek için:"
echo "  journalctl -u aurvex-bot -f"
echo "  journalctl -u aurvex-dashboard -f"
