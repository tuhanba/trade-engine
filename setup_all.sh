#!/bin/bash
# AURVEX.Ai — Master Kurulum Scripti
# Tek komutla tüm sistemi kurar ve başlatır
# Kullanım: bash /root/trade_engine/setup_all.sh

set -e
REPO="/root/trade_engine"
VENV="$REPO/venv"
LOGS="$REPO/logs"

echo "╔══════════════════════════════════════╗"
echo "║   AURVEX.Ai — Master Kurulum         ║"
echo "║   FAZ 1 — AX Trading Engine          ║"
echo "╚══════════════════════════════════════╝"

# 0. Log klasörü
mkdir -p "$LOGS"
echo "   ✓ Log klasörü: $LOGS"

# 1. Repo güncelle
echo ""
echo "▶ [1/8] Repo güncelleniyor..."
cd "$REPO"
git fetch origin
git reset --hard origin/main
echo "   ✓ Repo güncellendi: $(git log -1 --format='%h %s')"

# 2. .env kontrolü
echo ""
echo "▶ [2/8] Ortam değişkenleri kontrol ediliyor..."
if [ ! -f "$REPO/.env" ]; then
    echo "   ⚠ .env dosyası bulunamadı — örnek oluşturuluyor..."
    cat > "$REPO/.env" << 'ENVEOF'
BINANCE_API_KEY=your_key_here
BINANCE_API_SECRET=your_secret_here
TELEGRAM_BOT_TOKEN=your_token_here
TELEGRAM_CHAT_ID=your_chat_id_here
SECRET_KEY=scalp2026
PAPER_BALANCE=250.0
RISK_PCT=1.0
EXECUTION_MODE=paper
DB_PATH=/root/trade_engine/trading.db
ENVEOF
    echo "   ⚠ .env oluşturuldu — lütfen düzenleyin: nano $REPO/.env"
else
    echo "   ✓ .env mevcut"
fi

# 3. Python bağımlılıkları
echo ""
echo "▶ [3/8] Python bağımlılıkları kuruluyor..."
if [ ! -d "$VENV" ]; then
    echo "   Sanal ortam oluşturuluyor..."
    python3 -m venv "$VENV"
fi
"$VENV/bin/pip" install --quiet --upgrade pip
"$VENV/bin/pip" install --quiet \
    flask \
    flask-socketio \
    python-dotenv \
    python-binance \
    pandas \
    requests \
    ta \
    2>/dev/null
echo "   ✓ Bağımlılıklar güncellendi"

# 4. DB başlat ve sıfırla
echo ""
echo "▶ [4/8] Veritabanı başlatılıyor ve sıfırlanıyor..."
"$VENV/bin/python3" -c "
import sys; sys.path.insert(0,'$REPO')
from database import init_db; init_db()
print('   ✓ DB tabloları hazır')
"
# Eski verileri temizle, temiz başlangıç yap
echo "   Eski veriler temizleniyor (AI Brain korunuyor)..."
"$VENV/bin/python3" -c "
import sys, sqlite3, os
sys.path.insert(0,'$REPO')
from config import DB_PATH, COIN_UNIVERSE
if os.path.exists(DB_PATH):
    with sqlite3.connect(DB_PATH) as conn:
        for tbl in ['trades','signal_candidates','scalp_signals','daily_summary','weekly_summary','dashboard_snapshots','ai_logs','coin_market_memory']:
            try: conn.execute(f'DELETE FROM {tbl}')
            except: pass
        try:
            conn.execute("DELETE FROM system_state WHERE key='paper_balance'")
            conn.execute("INSERT INTO system_state (key,value,updated_at) VALUES ('paper_balance','10000.0',datetime('now'))")
        except: pass
        try:
            conn.execute("DELETE FROM system_state WHERE key='circuit_breaker_until'")
            conn.execute("DELETE FROM system_state WHERE key='paused'")
        except: pass
        conn.commit()
    print(f'   ✓ Veriler temizlendi, bakiye \$10,000 sıfırlandı')
from coin_library import init_coin_library
init_coin_library()
print(f'   ✓ {len(COIN_UNIVERSE)} coin parametresi yüklendi')
"

# 5. nginx kur ve yapılandır
echo ""
echo "▶ [5/8] nginx kuruluyor..."
apt-get install -y nginx -q 2>/dev/null
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
echo "   ✓ nginx hazır — http://$(curl -s ifconfig.me 2>/dev/null || echo 'SUNUCU_IP')"

# 6. Servis dosyalarını kur
echo ""
echo "▶ [6/8] Systemd servisleri kuruluyor..."
cp "$REPO/aurvex-dashboard.service" /etc/systemd/system/
cp "$REPO/aurvex-bot.service" /etc/systemd/system/
systemctl daemon-reload
systemctl enable aurvex-dashboard
systemctl enable aurvex-bot
echo "   ✓ Servis dosyaları kopyalandı"

# 7. Logrotate ayarla
echo ""
echo "▶ [7/8] Logrotate ayarlanıyor..."
cp "$REPO/aurvex-bot.logrotate" /etc/logrotate.d/aurvex
chmod 644 /etc/logrotate.d/aurvex
echo "   ✓ Logrotate: /etc/logrotate.d/aurvex (14 günlük rotasyon)"

# 8. Servisleri başlat
echo ""
echo "▶ [8/8] Servisler başlatılıyor..."
systemctl restart aurvex-dashboard
sleep 3
systemctl restart aurvex-bot
sleep 4

# Durum kontrolü
echo ""
echo "════════════════════════════════════════"
echo "   DURUM KONTROLÜ"
echo "════════════════════════════════════════"

SVC_DASHBOARD=$(systemctl is-active aurvex-dashboard 2>/dev/null || echo "inactive")
SVC_BOT=$(systemctl is-active aurvex-bot 2>/dev/null || echo "inactive")
SVC_NGINX=$(systemctl is-active nginx 2>/dev/null || echo "inactive")

echo ""
[ "$SVC_DASHBOARD" = "active" ] && echo "   ✅ aurvex-dashboard: AKTİF" || echo "   ❌ aurvex-dashboard: $SVC_DASHBOARD"
[ "$SVC_BOT"       = "active" ] && echo "   ✅ aurvex-bot:       AKTİF" || echo "   ❌ aurvex-bot: $SVC_BOT"
[ "$SVC_NGINX"     = "active" ] && echo "   ✅ nginx:            AKTİF" || echo "   ❌ nginx: $SVC_NGINX"

echo ""
echo "   Dashboard API kontrolü..."
sleep 2
curl -s http://127.0.0.1:5000/api/ax_status \
    | python3 -c "import sys,json; d=json.load(sys.stdin); print('   ✅ API: OK  bakiye='+str(d.get('data',{}).get('balance','?'))+'$')" \
    2>/dev/null \
    || echo "   ⚠ API henüz hazır değil — 10sn bekleyip tekrar deneyin"

SERVER_IP=$(curl -s ifconfig.me 2>/dev/null || echo 'SUNUCU_IP')
echo ""
echo "╔══════════════════════════════════════╗"
echo "║   Kurulum tamamlandı!                ║"
echo "╠══════════════════════════════════════╣"
echo "   Dashboard: http://$SERVER_IP"
echo "╠══════════════════════════════════════╣"
echo "║   Log izlemek için:                  ║"
echo "║   journalctl -u aurvex-bot -f        ║"
echo "   tail -f $LOGS/bot.log"
echo "╚══════════════════════════════════════╝"
