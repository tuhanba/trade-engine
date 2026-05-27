#!/bin/bash
# ═══════════════════════════════════════════════════════════════════
# AurvexAI — Full System Fix & Service Registration Script v1.0
# Bu script sistemdeki tüm bilinen sorunları düzeltir:
#   1. Systemd servislerini kaydeder
#   2. DB migration çalıştırır
#   3. Log dizinini oluşturur
#   4. Lock dosyasını temizler
#   5. Servisleri başlatır
# ═══════════════════════════════════════════════════════════════════

set -euo pipefail

BASE="${BASE:-/root/trade_engine/trade-engine}"
VENV="${VENV:-/root/trade_engine/.venv/bin/python3}"

BOLD='\033[1m'; NC='\033[0m'
RED='\033[0;31m'; GRN='\033[0;32m'; YLW='\033[1;33m'
CYN='\033[0;36m'; MAG='\033[0;35m'

ok()   { echo -e "  ${GRN}✅ $1${NC}"; }
err()  { echo -e "  ${RED}❌ $1${NC}"; }
warn() { echo -e "  ${YLW}⚠️  $1${NC}"; }
info() { echo -e "  ${CYN}ℹ️  $1${NC}"; }
step() { echo -e "\n${BOLD}${MAG}━━━ $1 ━━━${NC}"; }

clear
echo -e "${BOLD}${CYN}"
cat << 'BANNER'
  ╔═══════════════════════════════════════════════╗
  ║   AurvexAI Full System Fix v1.0              ║
  ╚═══════════════════════════════════════════════╝
BANNER
echo -e "${NC}  📅 $(date '+%Y-%m-%d %H:%M:%S UTC')"
echo -e "  📁 $BASE"

# ── 1. Proje dizini kontrolü ──────────────────────────────────────
step "1. ORTAM KONTROLÜ"

if [ ! -d "$BASE" ]; then
    err "Proje dizini bulunamadı: $BASE"
    echo "  BASE=/doğru/yol bash fix_system.sh"
    exit 1
fi
ok "Proje dizini: $BASE"

if [ ! -f "$VENV" ]; then
    err "Python venv bulunamadı: $VENV"
    exit 1
fi
ok "Python venv: $VENV"

# ── 2. Log dizini oluştur ──────────────────────────────────────────
step "2. LOG DİZİNİ"
LOG_DIR="$BASE/logs"
mkdir -p "$LOG_DIR"
ok "Log dizini: $LOG_DIR"

# ── 3. Lock dosyasını temizle ──────────────────────────────────────
step "3. LOCK TEMİZLEME"
LOCK_FILE=$(python3 -c "import tempfile,os; print(os.path.join(tempfile.gettempdir(),'aurvex_bot.lock'))" 2>/dev/null || echo "/tmp/aurvex_bot.lock")
if [ -f "$LOCK_FILE" ]; then
    rm -f "$LOCK_FILE"
    ok "Lock dosyası temizlendi: $LOCK_FILE"
else
    info "Lock dosyası yok (normal)"
fi

# ── 4. Systemd servis dosyalarını kaydet ──────────────────────────
step "4. SERVİS KAYDI (systemd)"

SERVICES_REGISTERED=0

# Önce proje kökünü dene, sonra systemd/ dizinini
for svc_file in "$BASE/ax-bot.service" "$BASE/systemd/ax-bot.service"; do
    if [ -f "$svc_file" ]; then
        cp "$svc_file" /etc/systemd/system/ax-bot.service
        ok "ax-bot.service kopyalandı: $svc_file → /etc/systemd/system/"
        SERVICES_REGISTERED=$((SERVICES_REGISTERED+1))
        break
    fi
done

for svc_file in "$BASE/ax-dashboard.service" "$BASE/systemd/ax-dashboard.service"; do
    if [ -f "$svc_file" ]; then
        cp "$svc_file" /etc/systemd/system/ax-dashboard.service
        ok "ax-dashboard.service kopyalandı: $svc_file → /etc/systemd/system/"
        SERVICES_REGISTERED=$((SERVICES_REGISTERED+1))
        break
    fi
done

if [ "$SERVICES_REGISTERED" -gt 0 ]; then
    systemctl daemon-reload
    ok "systemctl daemon-reload tamamlandı"
    
    systemctl enable ax-bot ax-dashboard 2>/dev/null && ok "Servisler enable edildi" || warn "Servis enable hatası (devam ediliyor)"
else
    warn "Servis dosyaları bulunamadı — manuel kurulum gerekli"
fi

# ── 5. DB Migration ───────────────────────────────────────────────
step "5. VERİTABANI MİGRATİON"

DB_PATH="$BASE/trading.db"
if [ -f "$DB_PATH" ]; then
    BACKUP="${DB_PATH}.fix.$(date '+%Y%m%d_%H%M%S')"
    cp "$DB_PATH" "$BACKUP"
    ok "DB yedeklendi: $(basename $BACKUP)"
fi

cd "$BASE"
$VENV -c "
import sys; sys.path.insert(0, '.')
import database
database.init_db()
if hasattr(database, 'migrate_db'):
    database.migrate_db()
print('Migration OK')
" 2>&1 && ok "DB migration tamamlandı" || warn "DB migration hatası (devam ediliyor)"

# v6 migration
if [ -f "$BASE/scripts/migrate_v6.py" ]; then
    $VENV "$BASE/scripts/migrate_v6.py" 2>&1 | tail -2 && ok "v6 migration OK" || warn "v6 migration hatası"
fi

# ── 6. Servisleri başlat ──────────────────────────────────────────
step "6. SERVİSLERİ BAŞLAT"

start_or_manual() {
    local svc="$1"
    local cmd="$2"
    local log="$3"
    
    if systemctl list-unit-files --type=service 2>/dev/null | grep -q "^${svc}.service"; then
        systemctl stop "$svc" 2>/dev/null || true
        sleep 1
        systemctl start "$svc" && ok "$svc başlatıldı (systemd)" || {
            warn "$svc systemd ile başlatılamadı — nohup ile deneniyor"
            nohup $VENV $cmd >> "$log" 2>&1 &
            ok "$svc nohup ile başlatıldı (PID: $!)"
        }
    else
        warn "$svc systemd'de yok — nohup ile başlatılıyor"
        nohup $VENV $cmd >> "$log" 2>&1 &
        ok "$svc nohup ile başlatıldı (PID: $!)"
    fi
}

start_or_manual "ax-bot" \
    "$BASE/scalp_bot.py" \
    "$LOG_DIR/ax_bot.log"

sleep 2

start_or_manual "ax-dashboard" \
    "$BASE/app.py" \
    "$LOG_DIR/dashboard.log"

# ── 7. Sağlık Kontrolü ────────────────────────────────────────────
step "7. SAĞLIK KONTROLÜ"

sleep 4

# Bot process kontrolü
BOT_PID=$(pgrep -f "scalp_bot.py" 2>/dev/null || true)
if [ -n "$BOT_PID" ]; then
    ok "Bot çalışıyor (PID: $BOT_PID)"
else
    err "Bot çalışmıyor!"
fi

# Dashboard process kontrolü
DASH_PID=$(pgrep -f "app.py" 2>/dev/null || true)
if [ -n "$DASH_PID" ]; then
    ok "Dashboard çalışıyor (PID: $DASH_PID)"
else
    err "Dashboard çalışmıyor!"
fi

# HTTP health check
FLASK_PORT=$(cd "$BASE" && $VENV -c "import config; print(getattr(config,'FLASK_PORT',5000))" 2>/dev/null || echo 5000)
sleep 2
HTTP_STATUS=$(curl -s -o /dev/null -w "%{http_code}" "http://localhost:$FLASK_PORT/api/health" 2>/dev/null || echo "000")
if [ "$HTTP_STATUS" = "200" ]; then
    ok "Dashboard API OK (HTTP $HTTP_STATUS) → http://localhost:$FLASK_PORT"
else
    warn "Dashboard API henüz yanıt vermiyor (HTTP $HTTP_STATUS)"
fi

# Son bot log satırları
echo ""
info "Son bot log satırları:"
tail -5 "$LOG_DIR/ax_bot.log" 2>/dev/null | while read line; do
    echo "    $line"
done

# ── 8. Özet ──────────────────────────────────────────────────────
step "8. ÖZET"

echo ""
echo -e "  ${BOLD}Yararlı Komutlar:${NC}"
echo -e "  tail -f $LOG_DIR/ax_bot.log          # Bot logları (canlı)"
echo -e "  curl http://localhost:$FLASK_PORT/api/health   # Health check"
echo -e "  systemctl status ax-bot               # Servis durumu"
echo -e "  journalctl -u ax-bot -f               # Systemd logları"
echo ""
