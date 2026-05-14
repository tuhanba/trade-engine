#!/bin/bash
# ╔══════════════════════════════════════════════════════════════════════╗
# ║      AURVEX Trade Engine — Master Deploy Script v6.1               ║
# ║      server_deploy.sh + deploy.sh birleşik & optimize edildi       ║
# ║                                                                      ║
# ║  Kullanım:                                                           ║
# ║    bash deploy.sh              → Normal update + deploy             ║
# ║    bash deploy.sh --fresh      → Sıfırdan kur (.env ve DB korunur)  ║
# ║    bash deploy.sh --skip-deps  → Bağımlılıkları atla (hızlı)       ║
# ║    bash deploy.sh --status     → Sadece durum göster                ║
# ╚══════════════════════════════════════════════════════════════════════╝

set -euo pipefail

# ── Renkler ──────────────────────────────────────────────────────────────
RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'
BLUE='\033[0;34m'; CYAN='\033[0;36m'; BOLD='\033[1m'; NC='\033[0m'

ok()   { echo -e "${GREEN}  ✅ $1${NC}"; }
info() { echo -e "${CYAN}  ℹ  $1${NC}"; }
warn() { echo -e "${YELLOW}  ⚠  $1${NC}"; }
fail() { echo -e "${RED}  ❌ $1${NC}"; }
step() { echo -e "\n${BOLD}${BLUE}━━━ $1 ━━━${NC}"; }

# ── Argümanlar ────────────────────────────────────────────────────────────
FRESH=false; SKIP_DEPS=false; STATUS_ONLY=false
for arg in "$@"; do
    case $arg in
        --fresh)      FRESH=true ;;
        --skip-deps)  SKIP_DEPS=true ;;
        --status)     STATUS_ONLY=true ;;
    esac
done

# ── Sabitler ─────────────────────────────────────────────────────────────
REMOTE_DIR="/root/trade_engine"
REPO_URL="https://github.com/tuhanba/trade-engine.git"
VENV="$REMOTE_DIR/.venv"
PYTHON="$VENV/bin/python"
PIP="$VENV/bin/pip"
SERVICES=("ax-dashboard" "ax-bot")     # dashboard önce başlar
DASHBOARD_PORT=5000
SERVER_IP=$(curl -s --max-time 5 ifconfig.me 2>/dev/null || hostname -I | awk '{print $1}' || echo "UNKNOWN")
START_TIME=$(date +%s)

# ── Başlık ────────────────────────────────────────────────────────────────
echo ""
echo -e "${BOLD}${CYAN}╔════════════════════════════════════════════════════╗${NC}"
echo -e "${BOLD}${CYAN}║   AURVEX Trade Engine — Master Deploy v6.1         ║${NC}"
echo -e "${BOLD}${CYAN}║   $(date '+%Y-%m-%d %H:%M:%S %Z')                      ║${NC}"
echo -e "${BOLD}${CYAN}╚════════════════════════════════════════════════════╝${NC}"
echo ""
info "Server IP  : $SERVER_IP"
info "Repo       : $REPO_URL"
info "Deploy Dir : $REMOTE_DIR"
info "Flags      : fresh=$FRESH  skip-deps=$SKIP_DEPS  status-only=$STATUS_ONLY"

# ── STATUS ONLY modu ──────────────────────────────────────────────────────
if [ "$STATUS_ONLY" = true ]; then
    step "DURUM RAPORU"
    for SVC in "${SERVICES[@]}"; do
        if systemctl is-active --quiet "$SVC" 2>/dev/null; then
            ok "$SVC: RUNNING"
        else
            fail "$SVC: STOPPED/FAILED"
        fi
        journalctl -u "$SVC" -n 5 --no-pager 2>/dev/null || true
    done
    echo ""
    HEALTH=$(curl -s --max-time 5 "http://127.0.0.1:$DASHBOARD_PORT/api/health" 2>/dev/null || echo "{}")
    info "Health API: $HEALTH"
    exit 0
fi

# ════════════════════════════════════════════════════════════════════════
step "1/11 — ROOT YETKİSİ KONTROLÜ"
# ════════════════════════════════════════════════════════════════════════
if [ "$EUID" -ne 0 ]; then
    fail "Root yetkisi gerekli! Şu şekilde çalıştır: sudo bash deploy.sh"
    exit 1
fi
ok "Root yetkisi onaylandı"

# ════════════════════════════════════════════════════════════════════════
step "2/11 — SİSTEM PAKETLERİ"
# ════════════════════════════════════════════════════════════════════════
info "apt-get güncelleniyor..."
apt-get update -qq 2>/dev/null || warn "apt-get update başarısız, devam ediliyor"
info "Gerekli paketler kuruluyor..."
apt-get install -y -qq python3 python3-pip python3-venv git curl wget screen ufw logrotate 2>/dev/null \
    || warn "Bazı paketler kurulamadı"
PY_VER=$(python3 --version 2>&1)
ok "Sistem hazır — $PY_VER"

# ════════════════════════════════════════════════════════════════════════
step "3/11 — SERVİSLERİ DURDUR"
# ════════════════════════════════════════════════════════════════════════
for SVC in "${SERVICES[@]}"; do
    if systemctl is-active --quiet "$SVC" 2>/dev/null; then
        systemctl stop "$SVC" && ok "$SVC durduruldu"
    else
        info "$SVC zaten çalışmıyor"
    fi
done
# Eski screen oturumları temizle (eski deploy'lardan kalan)
screen -ls 2>/dev/null | grep -oP '\d+\.ax_\S+' | while read -r s; do
    screen -X -S "$s" quit 2>/dev/null && info "Screen kapatıldı: $s"
done || true
ok "Tüm servisler durduruldu"

# ════════════════════════════════════════════════════════════════════════
step "4/11 — GİT REPO GÜNCELLE"
# ════════════════════════════════════════════════════════════════════════
if [ "$FRESH" = true ]; then
    info "FRESH MODE — mevcut kurulum yedeklenip temizleniyor..."
    # Kritik dosyaları yedekle
    [ -f "$REMOTE_DIR/.env" ]       && cp "$REMOTE_DIR/.env"       /tmp/ax_env_backup       && info ".env  → /tmp/ax_env_backup"
    [ -f "$REMOTE_DIR/trading.db" ] && cp "$REMOTE_DIR/trading.db" /tmp/ax_db_backup        && info "trading.db → /tmp/ax_db_backup"
    rm -rf "$REMOTE_DIR"
    git clone "$REPO_URL" "$REMOTE_DIR"
    ok "Fresh clone tamamlandı"
    # Yedekleri geri yükle
    [ -f /tmp/ax_env_backup ]  && cp /tmp/ax_env_backup  "$REMOTE_DIR/.env"       && ok ".env restore edildi"
    [ -f /tmp/ax_db_backup ]   && cp /tmp/ax_db_backup   "$REMOTE_DIR/trading.db" && ok "trading.db restore edildi"
else
    if [ -d "$REMOTE_DIR/.git" ]; then
        cd "$REMOTE_DIR"
        info "Local değişiklikler stash'e alınıyor..."
        git stash 2>/dev/null || true
        git fetch origin
        git reset --hard origin/main
        # Stash'i geri getir (çakışırsa atla)
        git stash pop 2>/dev/null || true
        COMMIT_INFO=$(git log -1 --pretty=format:"%h — %s (%cr)" 2>/dev/null || echo "bilinmiyor")
        ok "Repo güncellendi: $COMMIT_INFO"
    else
        info "Repo bulunamadı, ilk clone yapılıyor..."
        git clone "$REPO_URL" "$REMOTE_DIR"
        ok "Repo clone tamamlandı"
    fi
fi
cd "$REMOTE_DIR"

# ════════════════════════════════════════════════════════════════════════
step "5/11 — .ENV KONTROL & GÜNCELLE"
# ════════════════════════════════════════════════════════════════════════
# Yardımcı: key yoksa ekle, varsa dokunma
add_env_if_missing() {
    local KEY="$1" VAL="$2"
    grep -q "^${KEY}=" "$REMOTE_DIR/.env" 2>/dev/null || echo "${KEY}=${VAL}" >> "$REMOTE_DIR/.env"
}

if [ ! -f "$REMOTE_DIR/.env" ]; then
    info ".env bulunamadı, yeni oluşturuluyor..."
    cat > "$REMOTE_DIR/.env" << 'ENVEOF'
# AURVEX Trade Engine — Production .env
# !! Bu dosyayı GitHub'a push ETME !!

# Binance API
BINANCE_API_KEY=9fND0AUNhBGyRUmvygaWYZYh70HJyypRrOhP1AZclXGmSLEpOXpDqCZG8yELAF2L
BINANCE_API_SECRET=CG9BLe4BVEba8eNLWdTyQx9EX8StZQTAObNOZyO7BzOpogOhpBb5eM8Nk0hWf3n5

# Telegram
TELEGRAM_BOT_TOKEN=8404489471:AAEU3uk-i_IWj4EcHXlf4Zt8-PkpIPAAc54
TELEGRAM_CHAT_ID=958182551

# Execution mod (paper = güvenli, live = gerçek para)
EXECUTION_MODE=paper
AX_MODE=execute
LIVE_TRADING_ENABLED=False
DRY_RUN=True

# Risk parametreleri
RISK_PCT=1.0
MAX_OPEN_TRADES=5
DAILY_MAX_LOSS_PCT=5.0
MAX_LEVERAGE=20
MAX_MARGIN_LOSS_PCT=0.40
DEFAULT_FEE_RATE=0.0004
MAX_CONSECUTIVE_LOSSES=5
COIN_COOLDOWN_MINUTES=60
MAX_CORRELATED_TRADES=3

# TP / Trailing
TP1_CLOSE_PCT=40
TP2_CLOSE_PCT=30
RUNNER_CLOSE_PCT=30
TRAIL_ATR_MULT=1.5
BREAKEVEN_ENABLED=True
BREAKEVEN_OFFSET_PCT=0.05

# Paper trading
INITIAL_PAPER_BALANCE=250.0
MAX_HOLD_MINUTES=240

# Dashboard / Flask
SECRET_KEY=ax_secret_prod_2026
FLASK_HOST=0.0.0.0
FLASK_PORT=5000

# Scan
SCAN_INTERVAL_SECONDS=60
MIN_VOLUME_USDT=5000000
MIN_MOVE_PCT=0.5
ENVEOF
    ok ".env oluşturuldu"
else
    info ".env mevcut — eksik değişkenler ekleniyor..."
    # v5.0 upgrade'de eklenen yeni değişkenler
    add_env_if_missing "AX_MODE"               "execute"
    add_env_if_missing "INITIAL_PAPER_BALANCE"  "250.0"
    add_env_if_missing "MAX_HOLD_MINUTES"       "240"
    add_env_if_missing "TP1_CLOSE_PCT"          "40"
    add_env_if_missing "TP2_CLOSE_PCT"          "30"
    add_env_if_missing "RUNNER_CLOSE_PCT"       "30"
    add_env_if_missing "TRAIL_ATR_MULT"         "1.5"
    add_env_if_missing "BREAKEVEN_ENABLED"      "True"
    add_env_if_missing "BREAKEVEN_OFFSET_PCT"   "0.05"
    add_env_if_missing "MAX_CORRELATED_TRADES"  "3"
    add_env_if_missing "FLASK_HOST"             "0.0.0.0"
    add_env_if_missing "SECRET_KEY"             "ax_secret_prod_2026"
    add_env_if_missing "SCAN_INTERVAL_SECONDS"  "60"
    add_env_if_missing "MIN_VOLUME_USDT"        "5000000"
    add_env_if_missing "MIN_MOVE_PCT"           "0.5"
    ok ".env güncel"
fi
chmod 600 "$REMOTE_DIR/.env"
info ".env izinleri: 600 (sadece root okuyabilir)"

# ════════════════════════════════════════════════════════════════════════
step "6/11 — PYTHON VENV & BAĞIMLILIKLAR"
# ════════════════════════════════════════════════════════════════════════
if [ "$SKIP_DEPS" = false ]; then
    info "Virtual environment oluşturuluyor: $VENV"
    python3 -m venv "$VENV"
    info "pip upgrade..."
    "$PIP" install --upgrade pip --quiet
    info "requirements.txt kuruluyor..."
    "$PIP" install -r "$REMOTE_DIR/requirements.txt" --quiet
    INSTALLED=$("$PIP" list --format=freeze 2>/dev/null | wc -l)
    ok "Bağımlılıklar kuruldu ($INSTALLED paket)"
else
    ok "Bağımlılıklar atlandı (--skip-deps)"
fi

# ════════════════════════════════════════════════════════════════════════
step "7/11 — VERİTABANI MİGRASYON"
# ════════════════════════════════════════════════════════════════════════
info "DB init & migration çalıştırılıyor..."
"$PYTHON" - << 'PYEOF'
import sys, os
sys.path.insert(0, os.getcwd())
try:
    import database
    database.init_db()
    added = database.migrate_db()
    print(f"  Eklenen kolonlar: {added}" if added else "  DB schema güncel, değişiklik yok")
    conn = database.get_connection()
    trade_count = conn.execute("SELECT COUNT(*) FROM trades").fetchone()[0]
    signal_count = conn.execute("SELECT COUNT(*) FROM signal_candidates").fetchone()[0]
    conn.close()
    print(f"  Mevcut trade: {trade_count}  |  Sinyal adayı: {signal_count}")
    print("  DB bağlantı testi: OK")
except Exception as e:
    print(f"  DB HATA: {e}")
    sys.exit(1)
PYEOF
ok "DB migration tamamlandı"

# ════════════════════════════════════════════════════════════════════════
step "8/11 — API KONEKTİVİTE TESTİ"
# ════════════════════════════════════════════════════════════════════════
# Binance Futures API
BINANCE=$(curl -s -o /dev/null -w "%{http_code}" --max-time 8 "https://fapi.binance.com/fapi/v1/ping" 2>/dev/null || echo "000")
if [ "$BINANCE" = "200" ]; then
    ok "Binance Futures API: ERİŞİLEBİLİR ✅"
else
    warn "Binance API: $BINANCE — sunucu kısıtlaması olabilir, fallback aktif olacak"
fi

# CoinGecko fallback testi (server_deploy.sh'dan alındı)
GECKO=$(curl -s -o /dev/null -w "%{http_code}" --max-time 8 "https://api.coingecko.com/api/v3/ping" 2>/dev/null || echo "000")
if [ "$GECKO" = "200" ]; then
    ok "CoinGecko API: ERİŞİLEBİLİR ✅"
else
    warn "CoinGecko API: $GECKO"
fi

# Telegram API
if [ -n "${TELEGRAM_BOT_TOKEN:-}" ]; then
    TG=$(curl -s -o /dev/null -w "%{http_code}" --max-time 8 \
        "https://api.telegram.org/bot${TELEGRAM_BOT_TOKEN}/getMe" 2>/dev/null || echo "000")
    [ "$TG" = "200" ] && ok "Telegram API: OK ✅" || warn "Telegram API: $TG"
fi

# ════════════════════════════════════════════════════════════════════════
step "9/11 — SYSTEMD SERVİSLERİ"
# ════════════════════════════════════════════════════════════════════════
mkdir -p "$REMOTE_DIR/systemd"

# ax-bot.service (inline yazılır — repo'da olmasa bile çalışır)
cat > /etc/systemd/system/ax-bot.service << EOF
[Unit]
Description=AURVEX Trade Engine — Scalp Bot
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=root
WorkingDirectory=$REMOTE_DIR
EnvironmentFile=$REMOTE_DIR/.env
ExecStart=$PYTHON $REMOTE_DIR/scalp_bot_v3.py
Restart=always
RestartSec=15
StartLimitInterval=300
StartLimitBurst=5
StandardOutput=journal
StandardError=journal
Environment=PYTHONUNBUFFERED=1

[Install]
WantedBy=multi-user.target
EOF

# ax-dashboard.service
cat > /etc/systemd/system/ax-dashboard.service << EOF
[Unit]
Description=AURVEX Trade Engine — Dashboard API
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=root
WorkingDirectory=$REMOTE_DIR
EnvironmentFile=$REMOTE_DIR/.env
ExecStart=$PYTHON $REMOTE_DIR/app.py
Restart=always
RestartSec=5
StartLimitInterval=60
StartLimitBurst=10
StandardOutput=journal
StandardError=journal
Environment=PYTHONUNBUFFERED=1

[Install]
WantedBy=multi-user.target
EOF

# Repo'daki systemd dizinine de kopyala (senkron)
cp /etc/systemd/system/ax-bot.service       "$REMOTE_DIR/systemd/ax-bot.service"
cp /etc/systemd/system/ax-dashboard.service "$REMOTE_DIR/systemd/ax-dashboard.service"

systemctl daemon-reload
systemctl enable ax-bot ax-dashboard 2>/dev/null
ok "Systemd service dosyaları yazıldı ve aktifleştirildi"

# ════════════════════════════════════════════════════════════════════════
step "10/11 — GÜVENLİK & FIREWALL & LOG ROTATION"
# ════════════════════════════════════════════════════════════════════════
# UFW (önce dene)
if command -v ufw &>/dev/null; then
    ufw allow ssh   2>/dev/null || true
    ufw allow "$DASHBOARD_PORT"/tcp 2>/dev/null || true
    info "UFW: SSH + port $DASHBOARD_PORT açıldı"
else
    iptables -A INPUT -p tcp --dport "$DASHBOARD_PORT" -j ACCEPT 2>/dev/null || true
    info "iptables: port $DASHBOARD_PORT açıldı"
fi

# Log rotation (server_deploy.sh + deploy.sh birleşimi)
cat > /etc/logrotate.d/aurvex-trade-engine << 'LOGEOF'
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
ok "Firewall ve log rotation ayarlandı"

# ════════════════════════════════════════════════════════════════════════
step "11/11 — SERVİSLERİ BAŞLAT & HEALTH CHECK"
# ════════════════════════════════════════════════════════════════════════
info "Dashboard başlatılıyor (önce)..."
systemctl restart ax-dashboard
sleep 4

info "Bot başlatılıyor..."
systemctl restart ax-bot
sleep 6

# ── Servis durum kontrolü ────────────────────────────────────────────
echo ""
info "Servis durumları:"
ALL_OK=true
for SVC in "${SERVICES[@]}"; do
    if systemctl is-active --quiet "$SVC"; then
        ok "$SVC: RUNNING ✅"
    else
        fail "$SVC: FAILED ❌"
        ALL_OK=false
        echo -e "${YELLOW}  Son 20 log satırı:${NC}"
        journalctl -u "$SVC" -n 20 --no-pager 2>/dev/null | sed 's/^/    /' || true
    fi
done

# ── HTTP Health Check ────────────────────────────────────────────────
echo ""
info "HTTP health check bekleniyor (8sn)..."
sleep 8

HEALTH_JSON=$(curl -s --max-time 10 "http://127.0.0.1:$DASHBOARD_PORT/api/health" 2>/dev/null || echo "{}")
HTTP_CODE=$(curl -s -o /dev/null -w "%{http_code}" --max-time 10 "http://127.0.0.1:$DASHBOARD_PORT/api/health" 2>/dev/null || echo "000")

if [ "$HTTP_CODE" = "200" ]; then
    ok "Dashboard HTTP: 200 OK ✅"
    echo "$HEALTH_JSON" | python3 -m json.tool 2>/dev/null | grep -E "(ok|db_connected|bot_alive|execution_mode|bot_status)" | sed 's/^/    /' || true
else
    warn "Dashboard HTTP: $HTTP_CODE — servis başlıyor olabilir"
    info "Manuel kontrol: curl http://127.0.0.1:$DASHBOARD_PORT/api/health"
fi

# ── Python Import Sanity Check ───────────────────────────────────────
echo ""
info "Python modül import testi..."
"$PYTHON" - 2>&1 << 'PYEOF' | while IFS= read -r line; do
    [[ "$line" == OK* ]]   && echo -e "  ${GREEN}✅ $line${NC}" \
    || [[ "$line" == FAIL* ]] && echo -e "  ${RED}❌ $line${NC}" \
    || echo -e "  ${CYAN}ℹ  $line${NC}"
done
import sys, os
sys.path.insert(0, os.getcwd())
checks = [
    ("config",                  "TP1_CLOSE_PCT"),
    ("database",                "get_dashboard_stats"),
    ("core.accounting",         "calculate_pnl"),
    ("core.trailing_engine",    "TrailingEngine"),
    ("core.ai_decision_engine", "classify_signal"),
    ("core.paper_tracker",      "register_candidate"),
    ("execution_engine",        "ExecutionEngine"),
    ("dashboard_service",       "get_health"),
]
failed = 0
for mod, attr in checks:
    try:
        m = __import__(mod, fromlist=[attr])
        getattr(m, attr)
        print(f"OK {mod}.{attr}")
    except Exception as e:
        print(f"FAIL {mod}.{attr}: {e}")
        failed += 1
if failed:
    sys.exit(1)
PYEOF
SANITY=$?
[ $SANITY -eq 0 ] && ok "Tüm modüller import OK ✅" || warn "Bazı import sorunları var — logları kontrol et"

# ── Toplam süre ──────────────────────────────────────────────────────
END_TIME=$(date +%s)
ELAPSED=$((END_TIME - START_TIME))

# ════════════════════════════════════════════════════════════════════════
echo ""
if [ "$ALL_OK" = true ] && [ "$HTTP_CODE" = "200" ]; then
    echo -e "${BOLD}${GREEN}╔════════════════════════════════════════════════════╗${NC}"
    echo -e "${BOLD}${GREEN}║       ✅ DEPLOYMENT BAŞARIYLA TAMAMLANDI!          ║${NC}"
    echo -e "${BOLD}${GREEN}║       Süre: ${ELAPSED}sn                                    ║${NC}"
    echo -e "${BOLD}${GREEN}╚════════════════════════════════════════════════════╝${NC}"
else
    echo -e "${BOLD}${YELLOW}╔════════════════════════════════════════════════════╗${NC}"
    echo -e "${BOLD}${YELLOW}║   ⚠  DEPLOYMENT TAMAMLANDI (uyarılar var)          ║${NC}"
    echo -e "${BOLD}${YELLOW}║   Süre: ${ELAPSED}sn — Logları kontrol et               ║${NC}"
    echo -e "${BOLD}${YELLOW}╚════════════════════════════════════════════════════╝${NC}"
fi
echo ""
echo -e "  ${BOLD}🌐 Dashboard :${NC} http://$SERVER_IP:$DASHBOARD_PORT"
echo -e "  ${BOLD}🔗 Health API:${NC} http://$SERVER_IP:$DASHBOARD_PORT/api/health"
echo -e "  ${BOLD}📊 Stats API :${NC} http://$SERVER_IP:$DASHBOARD_PORT/api/stats"
echo ""
echo -e "${BOLD}  📋 Günlük Komutlar:${NC}"
echo "    journalctl -u ax-bot -f                  → Bot canlı log"
echo "    journalctl -u ax-dashboard -f             → Dashboard canlı log"
echo "    systemctl status ax-bot ax-dashboard      → Servis durumu"
echo "    systemctl restart ax-bot                  → Bot yeniden başlat"
echo "    systemctl restart ax-dashboard            → Dashboard yeniden başlat"
echo "    systemctl stop ax-bot ax-dashboard        → Durdur"
echo "    bash deploy.sh --status                   → Hızlı durum raporu"
echo "    $PYTHON scripts/backtest_engine.py --limit 200  → Backtest"
echo ""
echo -e "${YELLOW}  ⚡ Hızlı yeniden deploy (deps atla):${NC} bash deploy.sh --skip-deps"
echo -e "${YELLOW}  🔄 Sıfırdan kur:${NC}                   bash deploy.sh --fresh"
echo ""
