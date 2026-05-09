#!/bin/bash
# ═══════════════════════════════════════════════════════════════════
#  AURVEX Ai — Deploy  |  bash deploy.sh [--reset] [--branch=X]
# ═══════════════════════════════════════════════════════════════════
BRANCH="claude/fix-scoring-telegram-ux-YuTYB"
DIR="/root/trade_engine"

# ── Aşama 1: Git pull → /tmp'den çalıştır (bash tampon sorunu çözümü) ─
# bash deploy.sh'i başlangıçta tamponlar; git pull dosyayı güncelleyince
# bash eski içeriği çalıştırır. Çözüm: güncel dosyayı /tmp'ye kopyala.
if [ "${_AX_INNER:-0}" != "1" ]; then
    cd "$DIR"
    git config pull.rebase false 2>/dev/null || true
    git fetch origin "$BRANCH" -q
    if git show-ref --verify --quiet "refs/heads/$BRANCH"; then
        git checkout "$BRANCH" -q
    else
        git checkout -b "$BRANCH" "origin/$BRANCH" -q
    fi
    if ! git diff --quiet HEAD 2>/dev/null; then git stash -q; fi
    git pull origin "$BRANCH" -q
    # Güncel deploy.sh'i /tmp'ye kopyala ve oradan çalıştır
    cp "$DIR/deploy.sh" /tmp/_aurvex_deploy.sh
    export _AX_INNER=1
    exec bash /tmp/_aurvex_deploy.sh "$@"
fi

set -euo pipefail

BRANCH="claude/fix-scoring-telegram-ux-YuTYB"
PY="python3"
DIR="/root/trade_engine"
HEALTH_URL="http://localhost:5000/api/health"
RESET=false
START_TIME=$(date +%s)

for arg in "$@"; do
    case $arg in
        --reset)    RESET=true ;;
        --branch=*) BRANCH="${arg#*=}" ;;
    esac
done

# ── Renkler & ikonlar ───────────────────────────────────────────────
R="\033[0m"
BOLD="\033[1m"
DIM="\033[2m"
GREEN="\033[38;5;82m"
RED="\033[38;5;196m"
YELLOW="\033[38;5;220m"
BLUE="\033[38;5;75m"
CYAN="\033[38;5;51m"
GRAY="\033[38;5;245m"
WHITE="\033[38;5;255m"
PURPLE="\033[38;5;135m"

step()  { echo -e "\n${BOLD}${BLUE}  ┌─ ${WHITE}$1${R}"; }
ok()    { echo -e "${GREEN}  │  ✓ ${R}$1"; }
fail()  { echo -e "${RED}  │  ✗ ${R}${BOLD}$1${R}"; }
warn()  { echo -e "${YELLOW}  │  ! ${R}$1"; }
info()  { echo -e "${GRAY}  │    ${R}${DIM}$1${R}"; }
done_() { echo -e "${BLUE}  └─────────────────────────────${R}"; }

# ── Banner ──────────────────────────────────────────────────────────
clear
echo ""
echo -e "${BOLD}${PURPLE}"
echo "    ╔═══════════════════════════════════════╗"
echo "    ║                                       ║"
echo "    ║    ◈  AURVEX Ai  —  DEPLOY  ◈         ║"
echo "    ║                                       ║"
echo "    ╚═══════════════════════════════════════╝"
echo -e "${R}"
echo -e "${GRAY}    Branch  ${R}${CYAN}$BRANCH${R}"
echo -e "${GRAY}    Dizin   ${R}${WHITE}$DIR${R}"
echo -e "${GRAY}    Reset   ${R}$([ "$RESET" = true ] && echo -e "${YELLOW}EVET${R}" || echo -e "${GRAY}hayır${R}")"
echo -e "${GRAY}    Zaman   ${R}${DIM}$(date '+%H:%M:%S')${R}"
echo ""

# ── Dizin kontrolü ──────────────────────────────────────────────────
if [ ! -d "$DIR" ]; then
    echo -e "${RED}  ✗ Dizin bulunamadı: $DIR${R}"
    exit 1
fi
cd "$DIR"

# ═══════════════════════════════════════════════════════════════════
# ADIM 1 — Git
# ═══════════════════════════════════════════════════════════════════
step "Git  ·  Kod güncelleniyor"
git config pull.rebase false 2>/dev/null || true
info "merge stratejisi ayarlandı"

PREV=$(git rev-parse --short HEAD 2>/dev/null || echo "?")
git fetch origin "$BRANCH" 2>&1 | grep -E "->|error" | sed 's/^/  │    /' || true

if git show-ref --verify --quiet "refs/heads/$BRANCH"; then
    git checkout "$BRANCH" -q
else
    git checkout -b "$BRANCH" "origin/$BRANCH" -q
fi

if ! git diff --quiet 2>/dev/null; then
    warn "Yerel değişiklikler stash'leniyor"
    git stash -q
fi

git pull origin "$BRANCH" -q
NEW=$(git rev-parse --short HEAD)

ok "Güncellendi  ${GRAY}${PREV}${R} ${GRAY}→${R} ${CYAN}${NEW}${R}"
info "$(git log -1 --pretty='%s' 2>/dev/null)"
done_

# ═══════════════════════════════════════════════════════════════════
# ADIM 2 — Import kontrolü
# ═══════════════════════════════════════════════════════════════════
step "Import  ·  Modül sağlığı"

MODULES=('config' 'database' 'core.accounting' 'core.trigger_engine'
         'core.risk_engine' 'core.trend_engine' 'core.ai_decision_engine'
         'core.market_scanner' 'execution_engine' 'telegram_delivery'
         'signal_engine' 'ml_signal_scorer' 'ai_brain' 'dashboard_service')

FAIL_LIST=""
PASS=0
for m in "${MODULES[@]}"; do
    result=$($PY -c "import sys; sys.path.insert(0,'.'); __import__('$m')" 2>&1)
    if [ $? -eq 0 ]; then
        PASS=$((PASS+1))
    else
        FAIL_LIST="$FAIL_LIST\n    $m: $result"
    fi
done

if [ -z "$FAIL_LIST" ]; then
    ok "${PASS}/${#MODULES[@]} modül import başarılı"
else
    fail "Import hatası — rollback başlatılıyor"
    echo -e "$FAIL_LIST" | head -5
    git checkout "$PREV" -q 2>/dev/null || true
    done_
    echo -e "\n${RED}  ✗ DEPLOY BAŞARISIZ — eski sürüme döndürüldü${R}\n"
    exit 1
fi
done_

# ═══════════════════════════════════════════════════════════════════
# ADIM 3 — Servisler durdur
# ═══════════════════════════════════════════════════════════════════
step "Servisler  ·  Durduruluyor"
for svc in aurvex-bot aurvex-dashboard aurvex-watchdog; do
    if systemctl is-active --quiet "$svc" 2>/dev/null; then
        systemctl stop "$svc" 2>/dev/null || true
        ok "$svc durduruldu"
    else
        info "$svc zaten duruyordu"
    fi
done
sleep 2
done_

# ═══════════════════════════════════════════════════════════════════
# ADIM 4 — Migration
# ═══════════════════════════════════════════════════════════════════
step "Migration  ·  DB şema güncelle"
if $PY scripts/migrate_accounting_schema.py 2>/dev/null; then
    ok "Migration tamamlandı"
else
    warn "Migration uyarısı (devam ediliyor)"
fi

if [ "$RESET" = true ]; then
    echo ""
    echo -e "${YELLOW}  │  Dashboard sıfırlama seçildi.${R}"
    echo -ne "${YELLOW}  │  Trade/sinyal verileri silinecek. Emin misiniz? [y/N]: ${R}"
    read -r confirm
    if [[ "$confirm" =~ ^[Yy]$ ]]; then
        $PY reset_dashboard.py 2>/dev/null && ok "Dashboard sıfırlandı" || warn "Reset hatası"
    else
        info "Reset iptal edildi"
    fi
fi
done_

# ═══════════════════════════════════════════════════════════════════
# ADIM 5 — Servisleri başlat
# ═══════════════════════════════════════════════════════════════════
step "Başlatma  ·  Servisler ayağa kaldırılıyor"

for svcfile in aurvex-bot.service aurvex-dashboard.service aurvex-watchdog.service; do
    [ -f "$svcfile" ] && cp "$svcfile" /etc/systemd/system/ 2>/dev/null || true
done
systemctl daemon-reload 2>/dev/null || true

systemctl start aurvex-bot       && ok "aurvex-bot başlatıldı"       || fail "aurvex-bot BAŞLAMIYOR"
sleep 3
systemctl start aurvex-dashboard && ok "aurvex-dashboard başlatıldı" || fail "aurvex-dashboard BAŞLAMIYOR"
sleep 3
systemctl start aurvex-watchdog  2>/dev/null && ok "aurvex-watchdog başlatıldı" || info "watchdog yok / atlandı"
done_

# ═══════════════════════════════════════════════════════════════════
# ADIM 6 — Sağlık kontrolü
# ═══════════════════════════════════════════════════════════════════
step "Sağlık  ·  Sistem kontrol ediliyor"
sleep 4

for svc in aurvex-bot aurvex-dashboard; do
    if systemctl is-active --quiet "$svc" 2>/dev/null; then
        UPTIME=$(systemctl show "$svc" --property=ActiveEnterTimestamp --value 2>/dev/null | cut -d' ' -f2 || echo "")
        ok "$svc  ${GRAY}ÇALIŞIYOR${R}  ${DIM}$UPTIME${R}"
    else
        fail "$svc DURDU"
        systemctl status "$svc" --no-pager -n 3 2>/dev/null | grep -E "Error|error|failed|Active" | sed 's/^/  │    /' || true
    fi
done

echo ""
for i in 1 2 3; do
    HEALTH=$(curl -s --max-time 4 "$HEALTH_URL" 2>/dev/null || echo "")
    if echo "$HEALTH" | grep -q '"status"'; then
        ok "Dashboard API hazır  ${GRAY}→ $HEALTH_URL${R}"
        break
    fi
    [ $i -lt 3 ] && { warn "API bekleniyor ($i/3)..."; sleep 5; } || info "API başlıyor olabilir — birkaç saniye bekleyin"
done
done_

# ═══════════════════════════════════════════════════════════════════
# ÖZET
# ═══════════════════════════════════════════════════════════════════
ELAPSED=$(( $(date +%s) - START_TIME ))

echo ""
echo -e "${BOLD}${GREEN}"
echo "    ╔═══════════════════════════════════════╗"
echo "    ║        ✓  DEPLOY TAMAMLANDI           ║"
echo "    ╚═══════════════════════════════════════╝"
echo -e "${R}"
echo -e "${GRAY}    Commit  ${R}${CYAN}${NEW}${R}"
echo -e "${GRAY}    Süre    ${R}${WHITE}${ELAPSED}s${R}"
echo -e "${GRAY}    Log     ${R}${DIM}journalctl -u aurvex-bot -f${R}"
echo -e "${GRAY}    Takip   ${R}${DIM}python3 scripts/monitor_paper_run.py 60${R}"
echo ""
