#!/bin/bash
# ═══════════════════════════════════════════════════════════
# AX Trade Engine v5.1 — Sunucu Deploy + Systemd (7/24)
# Termius'a yapıştır ve çalıştır
# ═══════════════════════════════════════════════════════════

set -e
echo "═══════════════════════════════════════════════"
echo "  AX Trade Engine v5.1 — Server Deploy"
echo "═══════════════════════════════════════════════"

REMOTE_DIR="/root/trade_engine"
REPO_URL="https://github.com/tuhanba/trade-engine.git"

# 1. Sistem gereksinimleri
echo ""
echo ">>> 1. Sistem gereksinimleri kuruluyor..."
apt-get update -qq
apt-get install -y -qq python3 python3-pip python3-venv git screen curl logrotate

# 2. Repo klonla veya güncelle
echo ""
echo ">>> 2. Git repo..."
if [ -d "$REMOTE_DIR/.git" ]; then
    cd $REMOTE_DIR
    git fetch origin
    git reset --hard origin/main
    echo "REPO GUNCELLENDI"
else
    rm -rf $REMOTE_DIR
    git clone $REPO_URL $REMOTE_DIR
    echo "REPO KLONLANDI"
fi
cd $REMOTE_DIR

# 3. .env oluştur
echo ""
echo ">>> 3. .env oluşturuluyor..."
if [ ! -f "$REMOTE_DIR/.env" ]; then
cat > $REMOTE_DIR/.env << 'ENVEOF'
BINANCE_API_KEY=9fND0AUNhBGyRUmvygaWYZYh70HJyypRrOhP1AZclXGmSLEpOXpDqCZG8yELAF2L
BINANCE_API_SECRET=CG9BLe4BVEba8eNLWdTyQx9EX8StZQTAObNOZyO7BzOpogOhpBb5eM8Nk0hWf3n5
TELEGRAM_BOT_TOKEN=8404489471:AAEU3uk-i_IWj4EcHXlf4Zt8-PkpIPAAc54
TELEGRAM_CHAT_ID=958182551
EXECUTION_MODE=paper
AX_MODE=execute
LIVE_TRADING_ENABLED=False
DRY_RUN=True
RISK_PCT=1.0
MAX_OPEN_TRADES=5
DAILY_MAX_LOSS_PCT=5.0
MAX_LEVERAGE=20
MAX_MARGIN_LOSS_PCT=0.40
DEFAULT_FEE_RATE=0.0004
MAX_CONSECUTIVE_LOSSES=5
COIN_COOLDOWN_MINUTES=60
TP1_CLOSE_PCT=40
TP2_CLOSE_PCT=30
RUNNER_CLOSE_PCT=30
TRAIL_ATR_MULT=1.5
BREAKEVEN_ENABLED=True
BREAKEVEN_OFFSET_PCT=0.05
INITIAL_PAPER_BALANCE=250.0
MAX_HOLD_MINUTES=240
SECRET_KEY=ax_secret_prod_2026
DASHBOARD_PORT=5000
SCAN_INTERVAL=60
MIN_VOLUME_USD=5000000
ENVEOF
echo ".env oluşturuldu."
else
    echo ".env zaten mevcut — atlanıyor."
fi

# 4. Python venv + dependencies
echo ""
echo ">>> 4. Python dependencies kuruluyor..."
cd $REMOTE_DIR
python3 -m venv .venv
source .venv/bin/activate
pip install --upgrade pip -q
pip install -r requirements.txt -q
echo "DEPENDENCIES OK"

# 5. DB Migration
echo ""
echo ">>> 5. DB Migration..."
python scripts/migrate_accounting_schema.py

# 6. Audit
echo ""
echo ">>> 6. Audit..."
python scripts/audit_pnl_consistency.py || echo "AUDIT UYARI - devam ediliyor..."

# 7. Binance API testi
echo ""
echo ">>> 7. Binance API testi..."
python -c "
import requests
try:
    r = requests.get('https://fapi.binance.com/fapi/v1/ping', timeout=5)
    print('Binance API: OK (' + str(r.status_code) + ')')
except:
    print('Binance API: ENGELLI - CoinGecko fallback aktif olacak')
try:
    r = requests.get('https://api.coingecko.com/api/v3/ping', timeout=5)
    print('CoinGecko API: OK')
except:
    print('CoinGecko API: ENGELLI')
"

# 8. Systemd servisleri kur
echo ""
echo ">>> 8. Systemd servisleri kuruluyor..."
# Eski screen oturumlarını temizle
screen -ls 2>/dev/null | grep -oP '\d+\.ax_' | xargs -I{} screen -X -S {} quit 2>/dev/null || true

# Systemd dosyalarını kopyala
cp $REMOTE_DIR/systemd/ax-bot.service /etc/systemd/system/ax-bot.service
cp $REMOTE_DIR/systemd/ax-dashboard.service /etc/systemd/system/ax-dashboard.service
systemctl daemon-reload

# Servisleri başlat
systemctl enable ax-dashboard ax-bot
systemctl restart ax-dashboard
sleep 2
systemctl restart ax-bot
echo "Systemd servisleri başlatıldı."

# 9. Log rotation ayarla
echo ""
echo ">>> 9. Log rotation ayarlanıyor..."
cat > /etc/logrotate.d/ax-trade-engine << 'LOGEOF'
/var/log/ax_bot.log /var/log/ax_dashboard.log {
    daily
    rotate 7
    compress
    delaycompress
    missingok
    notifempty
    create 0644 root root
}
LOGEOF
echo "Log rotation ayarlandı."

# 10. Kontrol
echo ""
echo ">>> 10. Servis durumları..."
sleep 3
systemctl status ax-dashboard --no-pager -l || true
echo ""
systemctl status ax-bot --no-pager -l || true

# 11. Firewall
echo ""
echo ">>> 11. Firewall (port 5000)..."
ufw allow 5000/tcp 2>/dev/null || iptables -A INPUT -p tcp --dport 5000 -j ACCEPT 2>/dev/null || true
echo "Port 5000 açıldı."

echo ""
echo "═══════════════════════════════════════════════"
echo "  ✅ DEPLOYMENT TAMAMLANDI!"
echo ""
echo "  Dashboard: http://143.198.90.104:5000"
echo ""
echo "  Komutlar:"
echo "    Bot log:       journalctl -u ax-bot -f"
echo "    Dashboard log: journalctl -u ax-dashboard -f"
echo "    Bot restart:   systemctl restart ax-bot"
echo "    Bot durdur:    systemctl stop ax-bot"
echo "    Durum:         systemctl status ax-bot ax-dashboard"
echo "═══════════════════════════════════════════════"
