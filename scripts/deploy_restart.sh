#!/bin/bash
# ═══════════════════════════════════════════════════════════════════════════
# AurvexAI — Clean Deploy & Restart Script v2.0
# Kullanım: bash scripts/deploy_restart.sh [--skip-pull] [--hard-reset]
#
#   --skip-pull   → git pull yapma (sadece restart et)
#   --hard-reset  → git reset --hard origin/main (local değişiklikleri sıfırla)
#   --no-migrate  → DB migration yapma
#   --bot-only    → Sadece bot servisini yeniden başlat
#   --dash-only   → Sadece dashboard servisini yeniden başlat
# ═══════════════════════════════════════════════════════════════════════════

set -euo pipefail

# ── Ayarlar ────────────────────────────────────────────────────────────────
BASE="${BASE:-/root/trade_engine/trade-engine}"
VENV="${VENV:-/root/trade_engine/.venv/bin/python3}"
PIP="${PIP:-/root/trade_engine/.venv/bin/pip}"
BOT_SERVICE="${BOT_SERVICE:-ax-bot}"
DASH_SERVICE="${DASH_SERVICE:-ax-dashboard}"
BRANCH="${BRANCH:-main}"
DB_PATH="$BASE/trading.db"

# ── Flags ───────────────────────────────────────────────────────────────────
SKIP_PULL=false
HARD_RESET=false
NO_MIGRATE=false
BOT_ONLY=false
DASH_ONLY=false

for arg in "$@"; do
    case $arg in
        --skip-pull)   SKIP_PULL=true ;;
        --hard-reset)  HARD_RESET=true ;;
        --no-migrate)  NO_MIGRATE=true ;;
        --bot-only)    BOT_ONLY=true ;;
        --dash-only)   DASH_ONLY=true ;;
    esac
done

# ── Renkler ─────────────────────────────────────────────────────────────────
BOLD='\033[1m'; NC='\033[0m'
RED='\033[0;31m'; GRN='\033[0;32m'; YLW='\033[1;33m'
CYN='\033[0;36m'; MAG='\033[0;35m'; DIM='\033[2m'

ok()    { echo -e "  ${GRN}✅ $1${NC}"; }
err()   { echo -e "  ${RED}❌ $1${NC}"; }
warn()  { echo -e "  ${YLW}⚠️  $1${NC}"; }
info()  { echo -e "  ${CYN}ℹ️  $1${NC}"; }
step()  { echo -e "\n${BOLD}${MAG}━━━ $1 ━━━${NC}"; }

ERRORS=0
fail() { err "$1"; ERRORS=$((ERRORS+1)); }

# ── Banner ──────────────────────────────────────────────────────────────────
clear
echo -e "${BOLD}${CYN}"
cat << 'BANNER'
  ╔═══════════════════════════════════════════════╗
  ║    AurvexAI Deploy & Restart v2.0            ║
  ╚═══════════════════════════════════════════════╝
BANNER
echo -e "${NC}  📅 $(date '+%Y-%m-%d %H:%M:%S UTC')"
echo -e "  📁 $BASE"
echo -e "  🌿 Branch: $BRANCH"
[ "$SKIP_PULL" = true ]  && echo -e "  ${YLW}⚡ Git pull: atlanıyor${NC}"
[ "$HARD_RESET" = true ] && echo -e "  ${RED}⚠️  Hard reset: AÇIK${NC}"
[ "$NO_MIGRATE" = true ] && echo -e "  ${YLW}⚡ DB migration: atlanıyor${NC}"
[ "$BOT_ONLY" = true ]   && echo -e "  ${CYN}🤖 Sadece bot restart${NC}"
[ "$DASH_ONLY" = true ]  && echo -e "  ${CYN}📊 Sadece dashboard restart${NC}"

# ── 1. Dizin ve Ortam Kontrolü ──────────────────────────────────────────────
step "1. ORTAM KONTROLÜ"

if [ ! -d "$BASE" ]; then
    err "Proje dizini bulunamadı: $BASE"
    echo -e "  ${DIM}BASE ortam değişkeni ile ayarla: BASE=/path/to/project bash ...${NC}"
    exit 1
fi
ok "Proje dizini: $BASE"

if [ ! -f "$VENV" ]; then
    err "Python venv bulunamadı: $VENV"
    echo -e "  ${DIM}VENV=/path/to/python bash ... ile ayarla${NC}"
    exit 1
fi
PYVER=$($VENV --version 2>&1)
ok "Python: $PYVER"

cd "$BASE"
CURRENT_BRANCH=$(git rev-parse --abbrev-ref HEAD 2>/dev/null || echo "unknown")
CURRENT_COMMIT=$(git rev-parse --short HEAD 2>/dev/null || echo "unknown")
info "Mevcut branch: $CURRENT_BRANCH  commit: $CURRENT_COMMIT"

# ── 2. Servisleri Durdur ────────────────────────────────────────────────────
step "2. SERVİSLERİ DURDUR"

stop_service() {
    local svc="$1"
    if systemctl is-active --quiet "$svc" 2>/dev/null; then
        systemctl stop "$svc" && ok "$svc durduruldu" || warn "$svc durdurulamadı"
    else
        info "$svc zaten durmuş"
    fi
}

if [ "$DASH_ONLY" = false ]; then
    stop_service "$BOT_SERVICE"
fi
if [ "$BOT_ONLY" = false ]; then
    stop_service "$DASH_SERVICE"
fi

# Bot prosesini double-check — servis dışında çalışıyorsa da öldür
for script in "scalp_bot.py" "async_scalp_engine.py"; do
    BOT_PID=$(pgrep -f "$script" 2>/dev/null || true)
    if [ -n "$BOT_PID" ]; then
        warn "Artık süreç bulundu ($script PID: $BOT_PID) — öldürülüyor..."
        kill -TERM $BOT_PID 2>/dev/null || true
        sleep 2
        kill -9 $BOT_PID 2>/dev/null || true
        ok "Artık süreç temizlendi: $script"
    fi
done

# Lock dosyasını temizle
LOCK_FILE=$(python3 -c "import tempfile,os; print(os.path.join(tempfile.gettempdir(),'aurvex_bot.lock'))" 2>/dev/null || echo "/tmp/aurvex_bot.lock")
[ -f "$LOCK_FILE" ] && { rm -f "$LOCK_FILE"; ok "Lock dosyası temizlendi: $LOCK_FILE"; }

# ── 3. Git Pull ─────────────────────────────────────────────────────────────
step "3. KOD GÜNCELLEME"

if [ "$SKIP_PULL" = true ]; then
    info "Git pull atlanıyor (--skip-pull)"
else
    # Stash varsa uyar
    STASH_CNT=$(git stash list 2>/dev/null | wc -l || echo 0)
    [ "$STASH_CNT" -gt 0 ] && warn "Stash'de değişiklik var: $STASH_CNT"

    if [ "$HARD_RESET" = true ]; then
        warn "Hard reset uygulanıyor — local değişiklikler SİLİNECEK"
        git fetch origin "$BRANCH" 2>&1 | tail -1
        git reset --hard "origin/$BRANCH"
        ok "Hard reset tamamlandı"
    else
        # Güvenli pull
        git fetch origin "$BRANCH" 2>&1 | tail -1
        BEHIND=$(git rev-list HEAD..origin/"$BRANCH" --count 2>/dev/null || echo 0)
        if [ "$BEHIND" -gt 0 ]; then
            info "$BEHIND yeni commit var — merge ediliyor..."
            git merge --ff-only "origin/$BRANCH" 2>&1 | tail -2
            ok "Kod güncellendi"
        else
            ok "Kod güncel (0 commit geride)"
        fi
    fi

    NEW_COMMIT=$(git rev-parse --short HEAD 2>/dev/null || echo "unknown")
    info "Yeni commit: $NEW_COMMIT"
fi

# -- Systemd Senkronizasyonu --
if [ -d "$BASE/systemd" ]; then
    info "Systemd dosyaları güncelleniyor..."
    cp -f "$BASE/systemd/"*.service /etc/systemd/system/ 2>/dev/null || true
    systemctl daemon-reload 2>/dev/null || true
fi

# ── 4. Paket Kurulumu ───────────────────────────────────────────────────────
step "4. PAKET KONTROLÜ"

if [ -f "$BASE/requirements.txt" ]; then
    # Eksik paket var mı kontrol et
    MISSING_OUT=$($PIP install -r "$BASE/requirements.txt" --dry-run 2>&1 || true)
    if echo "$MISSING_OUT" | grep -q "Would install"; then
        MISSING_COUNT=$(echo "$MISSING_OUT" | grep -c "Would install" || true)
        info "${MISSING_COUNT} yeni paket kurulacak..."
        $PIP install -r "$BASE/requirements.txt" -q && ok "Paketler güncellendi" || warn "Paket kurulumu hatalı (devam ediliyor)"
    else
        ok "Tüm paketler güncel"
    fi
else
    warn "requirements.txt bulunamadı"
fi

# ── 5. DB Migration ─────────────────────────────────────────────────────────
step "5. VERİTABANI MİGRATİON"

if [ "$NO_MIGRATE" = true ]; then
    info "DB migration atlanıyor (--no-migrate)"
else
    # DB yedek al
    if [ -f "$DB_PATH" ]; then
        BACKUP="${DB_PATH}.bak.$(date '+%Y%m%d_%H%M%S')"
        cp "$DB_PATH" "$BACKUP"
        ok "DB yedeklendi: $(basename $BACKUP)"
        # Eski yedekleri temizle (5'ten fazlasını sil)
        ls -t "${DB_PATH}.bak."* 2>/dev/null | tail -n +6 | xargs rm -f 2>/dev/null || true
    fi

    # init_db + migrate_db
    $VENV -c "
import sys; sys.path.insert(0, '.')
import database
database.init_db()
if hasattr(database, 'migrate_db'):
    database.migrate_db()
print('OK')
" 2>&1 && ok "DB migration tamamlandı" || { fail "DB migration hatası!"; }

    # v6 migration scripti varsa çalıştır
    if [ -f "$BASE/scripts/migrate_v6.py" ]; then
        $VENV "$BASE/scripts/migrate_v6.py" 2>&1 | tail -3 && ok "v6 migration tamamlandı" || warn "v6 migration hata (devam ediliyor)"
    fi
fi

# ── 6. Syntax Kontrolü ──────────────────────────────────────────────────────
step "6. SYNTAX KONTROLÜ"

check_syntax() {
    local f="$1"
    local name=$(basename $f)
    if $VENV -m py_compile "$f" 2>/dev/null; then
        ok "$name"
    else
        fail "$name — syntax hatası!"
        $VENV -m py_compile "$f" 2>&1 | head -3
    fi
}

[ -f "$BASE/async_scalp_engine.py" ] && check_syntax "$BASE/async_scalp_engine.py"
[ -f "$BASE/execution_engine.py" ]  && check_syntax "$BASE/execution_engine.py"
[ -f "$BASE/app.py" ]               && check_syntax "$BASE/app.py"
[ -f "$BASE/config.py" ]            && check_syntax "$BASE/config.py"
[ -f "$BASE/database.py" ]          && check_syntax "$BASE/database.py"
[ -f "$BASE/telegram_delivery.py" ] && check_syntax "$BASE/telegram_delivery.py"

if [ "$ERRORS" -gt 0 ]; then
    err "Syntax hataları var! Başlatma iptal edildi."
    exit 1
fi

# ── 7. Servisleri Başlat ────────────────────────────────────────────────────
step "7. SERVİSLERİ BAŞLAT"

start_service() {
    local svc="$1"
    local label="$2"
    if systemctl list-unit-files --type=service 2>/dev/null | grep -q "^${svc}.service"; then
        systemctl start "$svc" && ok "$label başlatıldı" || fail "$label başlatılamadı"
    else
        warn "$svc servisi bulunamadı — systemctl'ye kayıtlı değil"
        info "Manuel başlatmak için:"
        if [ "$svc" = "$BOT_SERVICE" ]; then
            echo -e "  ${DIM}nohup $VENV $BASE/async_scalp_engine.py >> $BASE/trade_engine.json.log 2>&1 &${NC}"
        else
            echo -e "  ${DIM}nohup $VENV $BASE/app.py >> $BASE/logs/dashboard.log 2>&1 &${NC}"
        fi
    fi
}

if [ "$DASH_ONLY" = false ]; then
    start_service "$BOT_SERVICE" "🤖 Bot"
    sleep 2
fi
if [ "$BOT_ONLY" = false ]; then
    start_service "$DASH_SERVICE" "📊 Dashboard"
    sleep 2
fi

# ── 8. Sağlık Kontrolü ──────────────────────────────────────────────────────
step "8. SAĞLIK KONTROLÜ"

sleep 3   # Servislerin başlaması için bekle

check_service_health() {
    local svc="$1"
    local label="$2"
    if systemctl is-active --quiet "$svc" 2>/dev/null; then
        ok "$label çalışıyor"
        # Son 5 log satırı
        journalctl -u "$svc" -n 5 --no-pager 2>/dev/null | tail -3 | while read line; do
            echo -e "    ${DIM}$line${NC}"
        done
    else
        fail "$label çalışmıyor!"
        journalctl -u "$svc" -n 10 --no-pager 2>/dev/null | tail -5 | while read line; do
            echo -e "    ${RED}$line${NC}"
        done
    fi
}

if [ "$DASH_ONLY" = false ]; then
    check_service_health "$BOT_SERVICE" "Bot"
fi
if [ "$BOT_ONLY" = false ]; then
    check_service_health "$DASH_SERVICE" "Dashboard"
fi

# Dashboard HTTP kontrolü
FLASK_PORT=$(cd "$BASE" && $VENV -c "import config; print(getattr(config,'FLASK_PORT',5000))" 2>/dev/null || echo 5000)
sleep 2
HTTP_STATUS=$(curl -s -o /dev/null -w "%{http_code}" "http://localhost:$FLASK_PORT/api/health" 2>/dev/null || echo "000")
if [ "$HTTP_STATUS" = "200" ]; then
    ok "Dashboard API yanıt veriyor (HTTP $HTTP_STATUS)"
else
    warn "Dashboard API henüz yanıt vermiyor (HTTP $HTTP_STATUS) — başlıyor olabilir"
fi

# DB heartbeat kontrolü
HB=$(cd "$BASE" && $VENV -c "
import sys; sys.path.insert(0,'.')
import database
v = database.get_state('bot_heartbeat') or database.get_bot_status().get('heartbeat',{}).get('value','')
print(v[:19] if v else 'YOK')
" 2>/dev/null || echo "?")
info "Son bot heartbeat: $HB"

# ── 9. Özet ─────────────────────────────────────────────────────────────────
step "9. ÖZET"

FINAL_COMMIT=$(git rev-parse --short HEAD 2>/dev/null || echo "unknown")
echo -e "\n  ${BOLD}Deploy Özeti${NC}"
echo -e "  Branch:  $CURRENT_BRANCH → $FINAL_COMMIT"
echo -e "  Hata:    $([ $ERRORS -eq 0 ] && echo "${GRN}0 hata${NC}" || echo "${RED}$ERRORS hata${NC}")"

if [ "$ERRORS" -eq 0 ]; then
    echo -e "\n  ${GRN}${BOLD}✅ Deploy başarıyla tamamlandı!${NC}"
    echo -e "\n  📋 Faydalı komutlar:"
    echo -e "  ${DIM}journalctl -u $BOT_SERVICE -f          # Bot logları (canlı)${NC}"
    echo -e "  ${DIM}tail -f $BASE/trade_engine.json.log           # Dosya logları${NC}"
    echo -e "  ${DIM}bash $BASE/aurvex_maintain.sh --fix     # Sağlık kontrolü${NC}"
    echo -e "  ${DIM}python3 scripts/simulate_past_signals.py --days 3  # Simülasyon${NC}"
else
    echo -e "\n  ${RED}${BOLD}⚠️  Deploy tamamlandı ama $ERRORS hata var!${NC}"
    echo -e "  ${DIM}Logları kontrol et: journalctl -u $BOT_SERVICE -n 50${NC}"
fi

echo ""
