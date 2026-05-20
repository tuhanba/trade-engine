#!/bin/bash
# ═══════════════════════════════════════════════════════════════════
# AurvexAI Server Maintenance Script v1.0
# Kullanım: bash aurvex_maintain.sh
# ═══════════════════════════════════════════════════════════════════

set -e
BOLD='\033[1m'
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m'

BASE="/root/trade_engine/trade-engine"
VENV="/root/trade_engine/.venv/bin/python3"
LOG="$BASE/logs/ax_bot.log"
ERRORS=0
WARNINGS=0

ok()   { echo -e "${GREEN}✅ $1${NC}"; }
warn() { echo -e "${YELLOW}⚠️  $1${NC}"; WARNINGS=$((WARNINGS+1)); }
err()  { echo -e "${RED}❌ $1${NC}"; ERRORS=$((ERRORS+1)); }
info() { echo -e "${BLUE}ℹ️  $1${NC}"; }
sep()  { echo -e "\n${BOLD}━━━ $1 ━━━${NC}"; }

echo -e "${BOLD}"
echo "╔══════════════════════════════════════════╗"
echo "║   AurvexAI Server Maintenance v1.0      ║"
echo "║   $(date '+%Y-%m-%d %H:%M:%S UTC')          ║"
echo "╚══════════════════════════════════════════╝"
echo -e "${NC}"

# ═══════════════════════════════════════════════════════
sep "1. SUNUCU SAĞLIĞI"
# ═══════════════════════════════════════════════════════

# Disk
DISK=$(df -h / | awk 'NR==2{print $5}' | tr -d '%')
if [ "$DISK" -gt 90 ]; then
    err "Disk doluluk: %$DISK — kritik!"
elif [ "$DISK" -gt 75 ]; then
    warn "Disk doluluk: %$DISK"
else
    ok "Disk doluluk: %$DISK"
fi

# RAM
RAM_FREE=$(free -m | awk 'NR==2{printf "%.0f", $7/$2*100}')
if [ "$RAM_FREE" -lt 10 ]; then
    err "Boş RAM: %$RAM_FREE"
elif [ "$RAM_FREE" -lt 20 ]; then
    warn "Boş RAM: %$RAM_FREE"
else
    ok "Boş RAM: %$RAM_FREE"
fi

# CPU
CPU=$(top -bn1 | grep "Cpu(s)" | awk '{print $2}' | cut -d'%' -f1 | cut -d',' -f1)
ok "CPU kullanım: %$CPU"

# ═══════════════════════════════════════════════════════
sep "2. SERVİSLER"
# ═══════════════════════════════════════════════════════

# aurvex-bot
if systemctl is-active --quiet aurvex-bot; then
    UPTIME=$(systemctl show aurvex-bot --property=ActiveEnterTimestamp | cut -d'=' -f2)
    ok "aurvex-bot çalışıyor ($UPTIME)"
else
    err "aurvex-bot DURMUŞ — yeniden başlatılıyor..."
    systemctl start aurvex-bot
    sleep 5
    if systemctl is-active --quiet aurvex-bot; then
        ok "aurvex-bot başlatıldı"
    else
        err "aurvex-bot başlatılamadı!"
    fi
fi

# aurvex-dashboard
if systemctl is-active --quiet aurvex-dashboard; then
    ok "aurvex-dashboard çalışıyor"
else
    warn "aurvex-dashboard DURMUŞ — başlatılıyor..."
    systemctl start aurvex-dashboard
fi

# Duplicate process kontrolü
PROC_COUNT=$(pgrep -c -f scalp_bot.py 2>/dev/null || echo 0)
if [ "$PROC_COUNT" -gt 1 ]; then
    err "Birden fazla scalp_bot.py süreci var ($PROC_COUNT) — temizleniyor..."
    pkill -f scalp_bot.py 2>/dev/null || true
    sleep 2
    systemctl start aurvex-bot
    ok "Temizlendi, yeniden başlatıldı"
else
    ok "Tek scalp_bot süreci aktif"
fi

# ═══════════════════════════════════════════════════════
sep "3. GIT GÜNCELLEME"
# ═══════════════════════════════════════════════════════

cd "$BASE"

# Uncommitted değişiklik var mı?
if [ -n "$(git status --porcelain)" ]; then
    warn "Uncommitted değişiklikler var — stash ediliyor..."
    git stash
    info "Stash: $(git stash list | head -1)"
fi

# Pull
info "GitHub'dan çekiliyor..."
PULL_OUT=$(git pull origin main 2>&1)
if echo "$PULL_OUT" | grep -q "Already up to date"; then
    ok "Zaten güncel"
elif echo "$PULL_OUT" | grep -q "Fast-forward\|Updating"; then
    ok "Güncellendi:"
    echo "$PULL_OUT" | grep -E "^\s+[0-9]+ file" | head -3
    UPDATED=true
else
    err "Git pull başarısız: $PULL_OUT"
fi

# ═══════════════════════════════════════════════════════
sep "4. KALINTI DOSYA TEMİZLİĞİ"
# ═══════════════════════════════════════════════════════

JUNK_FILES=(
    "AUDIT_REPORT_PHASE1.md" "AUDIT_REPORT_PHASE3.md" "AUDIT_REPORT_PHASE4.md"
    "AUDIT_REPORT_PHASE5.md" "AUDIT_REPORT_PHASE6.md" "AUDIT_REPORT_PHASE7.md"
    "AUDIT_REPORT_PHASE8.md" "EMERGENCY_STOP.md" "FINAL_DELIVERY_REPORT.md"
    "PHASE2_DASHBOARD_FIX.md" "SIMULATION_FILTER_REPORT.md"
    "_git_push21.py" "database_pg.py" "diagnose_no_trades.py"
    "dump.py" "fix_newlines.py" "scalp_bot_v3.py" "sync_repo.py"
    "ghost_patch.py" "coin_optimization_results.csv" "past_signals_report.csv"
    "baglanti_ozeti.md" "sistem_iyilestirme_raporu.md"
    "test_ve_iyilestirme_raporu.md" "termius_kurulum_rehberi.md"
    "deploy.sh" "deploy_system.sh" "server_deploy.sh" "setup_nginx.sh" "update.sh"
)

DELETED=0
for f in "${JUNK_FILES[@]}"; do
    if [ -f "$BASE/$f" ]; then
        rm -f "$BASE/$f"
        info "Silindi: $f"
        DELETED=$((DELETED+1))
    fi
done

# Deprecated klasörü
if [ -d "$BASE/deprecated" ]; then
    rm -rf "$BASE/deprecated"
    info "Silindi: deprecated/"
    DELETED=$((DELETED+1))
fi

# Archive klasörü
if [ -d "$BASE/archive" ]; then
    rm -rf "$BASE/archive"
    info "Silindi: archive/"
    DELETED=$((DELETED+1))
fi

# Log temizliği (100MB üstündeyse)
if [ -f "$LOG" ]; then
    LOG_SIZE=$(du -m "$LOG" | cut -f1)
    if [ "$LOG_SIZE" -gt 100 ]; then
        warn "Log dosyası büyük (${LOG_SIZE}MB) — son 50000 satır tutuluyor..."
        tail -50000 "$LOG" > "$LOG.tmp" && mv "$LOG.tmp" "$LOG"
        ok "Log kırpıldı"
    else
        ok "Log boyutu: ${LOG_SIZE}MB"
    fi
fi

if [ "$DELETED" -gt 0 ]; then
    ok "$DELETED kalıntı dosya/klasör silindi"
else
    ok "Kalıntı dosya yok"
fi

# ═══════════════════════════════════════════════════════
sep "5. PYTHON IMPORT SAĞLIĞI"
# ═══════════════════════════════════════════════════════

cd "$BASE"
$VENV - << 'PYEOF'
import sys
sys.path.insert(0, '.')

modules = [
    'config', 'database', 'core.accounting',
    'core.ai_decision_engine', 'core.ghost_learning',
    'core.paper_tracker', 'core.trend_engine',
    'core.trigger_engine', 'core.risk_engine',
    'execution_engine', 'signal_engine',
    'ai_brain', 'ghost_learner', 'dashboard_service',
    'telegram_delivery', 'ml_signal_scorer',
]

errors = []
for m in modules:
    try:
        __import__(m)
        print(f"OK  {m}")
    except Exception as e:
        print(f"ERR {m}: {e}")
        errors.append(m)

print(f"\n{'='*40}")
if errors:
    print(f"IMPORT HATALARI: {errors}")
    sys.exit(1)
else:
    print(f"Tüm {len(modules)} modül OK")
PYEOF

if [ $? -eq 0 ]; then
    ok "Python import sağlığı OK"
else
    err "Import hataları var — log'u incele"
fi

# ═══════════════════════════════════════════════════════
sep "6. VERİTABANI SAĞLIĞI"
# ═══════════════════════════════════════════════════════

$VENV - << 'PYEOF'
import sys
sys.path.insert(0, '.')
import database

# init_db — idempotent
try:
    database.init_db()
    print("OK  init_db()")
except Exception as e:
    print(f"ERR init_db: {e}")

# Tablo kontrolü
required_tables = [
    'trades', 'signal_candidates', 'paper_results',
    'ghost_signals', 'ghost_results', 'ghost_suggestions',
    'coin_configs', 'ai_learning', 'coin_profiles',
    'params', 'system_state', 'paper_account',
    'balance_ledger', 'bot_status',
]

with database.get_conn() as conn:
    existing = {
        r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()
    }

missing = []
for t in required_tables:
    if t in existing:
        print(f"OK  table: {t}")
    else:
        print(f"ERR table MISSING: {t}")
        missing.append(t)

# İstatistikler
with database.get_conn() as conn:
    trades = conn.execute("SELECT COUNT(*) FROM trades").fetchone()[0]
    signals = conn.execute("SELECT COUNT(*) FROM signal_candidates").fetchone()[0]
    ghost_s = conn.execute("SELECT COUNT(*) FROM ghost_signals").fetchone()[0]
    ghost_r = conn.execute("SELECT COUNT(*) FROM ghost_results").fetchone()[0]
    paper = conn.execute("SELECT COUNT(*) FROM paper_results").fetchone()[0]

print(f"\n{'='*40}")
print(f"trades:           {trades}")
print(f"signal_candidates:{signals}")
print(f"ghost_signals:    {ghost_s} ({ghost_r} simüle)")
print(f"paper_results:    {paper}")

if missing:
    print(f"\nEKSİK TABLOLAR: {missing}")
    sys.exit(1)
else:
    print(f"\nTüm {len(required_tables)} tablo mevcut")
PYEOF

if [ $? -eq 0 ]; then
    ok "Veritabanı sağlığı OK"
else
    err "Veritabanı sorunları var"
fi

# ═══════════════════════════════════════════════════════
sep "7. BOT AKTİVİTE KONTROLÜ"
# ═══════════════════════════════════════════════════════

# Son 10 dakikada log aktivitesi var mı?
if [ -f "$LOG" ]; then
    LAST_LOG=$(tail -1 "$LOG" | cut -d'|' -f1 | tr -d ' ')
    LAST_TS=$(date -d "$LAST_LOG" +%s 2>/dev/null || echo 0)
    NOW_TS=$(date +%s)
    DIFF=$(( (NOW_TS - LAST_TS) / 60 ))

    if [ "$DIFF" -lt 5 ]; then
        ok "Bot aktif (son log: ${DIFF}dk önce)"
    elif [ "$DIFF" -lt 30 ]; then
        warn "Bot yavaş? (son log: ${DIFF}dk önce)"
    else
        err "Bot log üretmiyor (${DIFF}dk sessiz) — restart ediliyor..."
        systemctl restart aurvex-bot
        ok "Bot yeniden başlatıldı"
    fi

    # Son mesajı göster
    info "Son log: $(tail -3 "$LOG" | head -1)"
else
    warn "Log dosyası bulunamadı: $LOG"
fi

# Ghost signals birikmiş mi?
$VENV - << 'PYEOF' 2>/dev/null
import sys; sys.path.insert(0, '.')
from database import get_conn
with get_conn() as c:
    gs = c.execute("SELECT COUNT(*),SUM(simulated) FROM ghost_signals").fetchone()
    pending = (gs[0] or 0) - (gs[1] or 0)
    if gs[0] and gs[0] > 0:
        print(f"Ghost: {gs[0]} sinyal, {gs[1] or 0} simüle, {pending} bekliyor")
    else:
        print("Ghost: Henüz ghost signal yok (bot yeni başladıysa normal)")
PYEOF

# ═══════════════════════════════════════════════════════
sep "8. SERVİS RESTART (GEREKİYORSA)"
# ═══════════════════════════════════════════════════════

if [ "${UPDATED:-false}" = "true" ]; then
    info "Kod güncellendi — bot yeniden başlatılıyor..."
    systemctl restart aurvex-bot
    sleep 10
    if systemctl is-active --quiet aurvex-bot; then
        ok "Bot yeniden başlatıldı"
    else
        err "Bot başlatılamadı!"
        journalctl -u aurvex-bot -n 20 --no-pager
    fi
fi

# ═══════════════════════════════════════════════════════
sep "SONUÇ"
# ═══════════════════════════════════════════════════════

echo ""
if [ "$ERRORS" -eq 0 ] && [ "$WARNINGS" -eq 0 ]; then
    echo -e "${GREEN}${BOLD}🎉 Her şey OK — sistem sağlıklı${NC}"
elif [ "$ERRORS" -eq 0 ]; then
    echo -e "${YELLOW}${BOLD}⚠️  $WARNINGS uyarı, hata yok${NC}"
else
    echo -e "${RED}${BOLD}❌ $ERRORS hata, $WARNINGS uyarı${NC}"
fi

echo ""
echo "Servis durumları:"
systemctl is-active aurvex-bot     && echo "  aurvex-bot:       ✅ çalışıyor" || echo "  aurvex-bot:       ❌ durmuş"
systemctl is-active aurvex-dashboard && echo "  aurvex-dashboard: ✅ çalışıyor" || echo "  aurvex-dashboard: ❌ durmuş"
echo ""
echo "Canlı log: tail -f $LOG"
echo "Dashboard:  curl http://localhost:5000/api/health"
echo ""
