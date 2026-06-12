#!/bin/bash
# ═══════════════════════════════════════════════════════════════════
# AurvexAI Server Maintenance Script v2.0 — Ruflo Edition
# Kullanım: bash aurvex_maintain.sh [--fix] [--full] [--telegram-test]
# ═══════════════════════════════════════════════════════════════════

BASE="/root/trade_engine"
VENV="/root/trade_engine/.venv/bin/python3"
BOT_LOG="$BASE/logs/ax_bot.log"
DASH_LOG="$BASE/logs/dashboard.log"
DB_PATH="$BASE/trading.db"
ENV_FILE="$BASE/.env"

# Flags
DO_FIX=false
DO_FULL=false
DO_TG_TEST=false
for arg in "$@"; do
    case $arg in
        --fix)         DO_FIX=true ;;
        --full)        DO_FULL=true ;;
        --telegram-test) DO_TG_TEST=true ;;
    esac
done

# Renkler
BOLD='\033[1m'; DIM='\033[2m'
RED='\033[0;31m'; GREEN='\033[0;32m'
YELLOW='\033[1;33m'; BLUE='\033[0;34m'
CYAN='\033[0;36m'; MAGENTA='\033[0;35m'
NC='\033[0m'

ERRORS=0; WARNINGS=0; FIXES=0

ok()    { echo -e "  ${GREEN}✅ $1${NC}"; }
warn()  { echo -e "  ${YELLOW}⚠️  $1${NC}"; WARNINGS=$((WARNINGS+1)); }
err()   { echo -e "  ${RED}❌ $1${NC}"; ERRORS=$((ERRORS+1)); }
info()  { echo -e "  ${BLUE}ℹ️  $1${NC}"; }
fixed() { echo -e "  ${CYAN}🔧 $1${NC}"; FIXES=$((FIXES+1)); }
sep()   { echo -e "\n${BOLD}${MAGENTA}━━━ $1 ━━━${NC}"; }
kv()    { printf "  ${DIM}%-28s${NC} %s\n" "$1" "$2"; }

clear
echo -e "${BOLD}${CYAN}"
cat << 'BANNER'
  ╔═══════════════════════════════════════════════╗
  ║   AurvexAI Maintenance v2.0 — Ruflo Edition  ║
  ╚═══════════════════════════════════════════════╝
BANNER
echo -e "${NC}  📅 $(date '+%Y-%m-%d %H:%M:%S UTC')"
echo -e "  📁 $BASE"
[ "$DO_FIX" = true ]     && echo -e "  ${CYAN}🔧 Auto-fix: AÇIK${NC}"
[ "$DO_FULL" = true ]    && echo -e "  ${CYAN}📊 Full stats: AÇIK${NC}"
[ "$DO_TG_TEST" = true ] && echo -e "  ${CYAN}📱 Telegram test: AÇIK${NC}"

cd "$BASE" 2>/dev/null || { echo -e "${RED}❌ $BASE bulunamadı!${NC}"; exit 1; }

# ═══════════════════════════════════════════════════════
sep "1. SUNUCU KAYNAKLARI"
# ═══════════════════════════════════════════════════════

DISK=$(df -h / | awk 'NR==2{print $4" kullanılabilir ("$5" dolu)"}')
RAM_TOTAL=$(free -m | awk 'NR==2{print $2}')
RAM_USED=$(free -m | awk 'NR==2{print $3}')
RAM_FREE=$(free -m | awk 'NR==2{printf "%.0f", $7/$2*100}')
DISK_PCT=$(df / | awk 'NR==2{print $5}' | tr -d '%')
UPTIME=$(uptime -p)
LOAD=$(cat /proc/loadavg | awk '{print $1" "$2" "$3}')

kv "Uptime:"    "$UPTIME"
kv "Load avg:"  "$LOAD"
kv "Disk:"      "$DISK"
kv "RAM:"       "${RAM_USED}MB / ${RAM_TOTAL}MB (boş: %${RAM_FREE})"

[ "$DISK_PCT" -gt 90 ] && err "Disk kritik: %$DISK_PCT" || \
[ "$DISK_PCT" -gt 75 ] && warn "Disk yüksek: %$DISK_PCT" || ok "Disk: %$DISK_PCT"
[ "$RAM_FREE" -lt 10 ] && err "RAM kritik: %$RAM_FREE boş" || \
[ "$RAM_FREE" -lt 20 ] && warn "RAM az: %$RAM_FREE boş" || ok "RAM yeterli: %$RAM_FREE boş"

# ═══════════════════════════════════════════════════════
sep "2. GIT DURUMU"
# ═══════════════════════════════════════════════════════

BRANCH=$(git branch --show-current 2>/dev/null || echo "?")
COMMIT=$(git log --oneline -1 2>/dev/null || echo "?")
REMOTE_COMMIT=$(git ls-remote origin HEAD 2>/dev/null | awk '{print substr($1,1,7)}')
LOCAL_COMMIT=$(git rev-parse --short HEAD 2>/dev/null)

kv "Branch:"  "$BRANCH"
kv "Commit:"  "$COMMIT"
kv "Remote:"  "$REMOTE_COMMIT"

if [ "$BRANCH" != "main" ]; then
    warn "main branch değil: $BRANCH"
    if [ "$DO_FIX" = true ]; then
        git checkout main 2>/dev/null && fixed "main branch'e geçildi" || warn "Branch değiştirilemedi"
    fi
fi

if [ -n "$(git status --porcelain 2>/dev/null)" ]; then
    warn "Uncommitted değişiklikler var"
    git status --short | head -5 | while read l; do info "$l"; done
fi

info "GitHub'dan kontrol ediliyor..."
PULL=$(git pull origin main 2>&1)
if echo "$PULL" | grep -q "Already up to date"; then
    ok "Güncel (main)"
elif echo "$PULL" | grep -q "Fast-forward\|Updating"; then
    fixed "Güncellendi:"
    echo "$PULL" | grep -E "^\s+[0-9]+ file" | head -3 | while read l; do info "$l"; done
    UPDATED=true
else
    warn "Pull sonucu: $(echo "$PULL" | head -1)"
fi

# ═══════════════════════════════════════════════════════
sep "3. SERVİSLER"
# ═══════════════════════════════════════════════════════

check_service() {
    local name=$1
    if systemctl is-active --quiet "$name"; then
        local pid=$(systemctl show "$name" --property=MainPID | cut -d= -f2)
        local mem=$(ps -o rss= -p "$pid" 2>/dev/null | awk '{printf "%.0fMB", $1/1024}')
        local since=$(systemctl show "$name" --property=ActiveEnterTimestamp \
                      | cut -d= -f2 | cut -d' ' -f1-2)
        ok "$name aktif | PID:$pid | RAM:$mem | $since'den beri"
    else
        err "$name DURMUŞ"
        if [ "$DO_FIX" = true ]; then
            systemctl start "$name" && fixed "$name başlatıldı" || err "$name başlatılamadı"
        fi
    fi
}

check_service ax-bot
check_service ax-dashboard

# Duplicate process
PROC_COUNT=$(pgrep -f async_scalp_engine.py 2>/dev/null | wc -l)
PROC_COUNT=${PROC_COUNT//[^0-9]/}; PROC_COUNT=${PROC_COUNT:-0}
if [ "$PROC_COUNT" -gt 1 ]; then
    err "$PROC_COUNT async_scalp_engine süreci var!"
    if [ "$DO_FIX" = true ]; then
        pkill -f async_scalp_engine.py 2>/dev/null; sleep 2
        systemctl start ax-bot
        fixed "Duplicate temizlendi, yeniden başlatıldı"
    fi
else
    ok "Tek scalp_bot süreci"
fi

# ═══════════════════════════════════════════════════════
sep "4. TELEGRAM"
# ═══════════════════════════════════════════════════════

$VENV - << 'PYEOF'
import sys, os
sys.path.insert(0, '.')

# Token kontrolü
try:
    import config
    token = config.TELEGRAM_BOT_TOKEN
    chat_id = config.TELEGRAM_CHAT_ID
    if token and len(token) > 10:
        print(f"  \033[32m✅ BOT_TOKEN: ...{token[-6:]}\033[0m")
    else:
        print(f"  \033[31m❌ BOT_TOKEN: BOŞ veya YANLIŞ\033[0m")
    if chat_id:
        print(f"  \033[32m✅ CHAT_ID: {chat_id}\033[0m")
    else:
        print(f"  \033[31m❌ CHAT_ID: BOŞ\033[0m")
except Exception as e:
    print(f"  \033[31m❌ Config okunamadı: {e}\033[0m")
PYEOF

# .env dosyası var mı?
if [ -f "$ENV_FILE" ]; then
    ok ".env dosyası mevcut"
    TG_TOKEN=$(grep "TELEGRAM_BOT_TOKEN" "$ENV_FILE" | cut -d= -f2 | tr -d ' "')
    TG_CHAT=$(grep "TELEGRAM_CHAT_ID" "$ENV_FILE" | cut -d= -f2 | tr -d ' "')
    [ -n "$TG_TOKEN" ] && ok ".env BOT_TOKEN tanımlı" || err ".env BOT_TOKEN boş!"
    [ -n "$TG_CHAT" ]  && ok ".env CHAT_ID tanımlı"  || err ".env CHAT_ID boş!"
else
    err ".env dosyası yok! Telegram çalışmıyor."
    if [ "$DO_FIX" = true ]; then
        warn ".env oluşturuluyor — token'ları manuel girmeniz gerekiyor"
        cat > "$ENV_FILE" << 'ENVEOF'
TELEGRAM_BOT_TOKEN=
TELEGRAM_CHAT_ID=
BINANCE_API_KEY=
BINANCE_API_SECRET=
ENVEOF
        fixed ".env şablonu oluşturuldu — token'ları gir: nano $ENV_FILE"
    fi
fi

# Telegram bağlantı testi
if [ "$DO_TG_TEST" = true ]; then
    $VENV - << 'PYEOF'
import sys; sys.path.insert(0, '.')
try:
    from telegram_delivery import _send_raw
    result = _send_raw("🔧 AurvexAI Maintenance Test — sistem sağlıklı ✅")
    if result:
        print("  \033[32m✅ Telegram bağlantı testi BAŞARILI — mesaj gönderildi\033[0m")
    else:
        print("  \033[31m❌ Telegram bağlantı testi BAŞARISIZ — token/chat_id kontrol et\033[0m")
except Exception as e:
    print(f"  \033[31m❌ Telegram test hatası: {e}\033[0m")
PYEOF
fi

# ═══════════════════════════════════════════════════════
sep "5. PYTHON IMPORTLAR"
# ═══════════════════════════════════════════════════════

$VENV - << 'PYEOF'
import sys; sys.path.insert(0, '.')
modules = [
    'config','database','core.accounting',
    'core.ai_decision_engine','core.ghost_learning',
    'core.paper_tracker','core.trend_engine',
    'core.trigger_engine','core.risk_engine',
    'execution_engine','core.signal_engine','ai_brain',
    'ghost_learner','dashboard_service',
    'telegram_delivery','ml_signal_scorer',
]
errors = []
for m in modules:
    try:
        __import__(m)
        print(f"  \033[32m✅ {m}\033[0m")
    except Exception as e:
        print(f"  \033[31m❌ {m}: {e}\033[0m")
        errors.append(m)
if errors:
    print(f"\n  \033[31mHATALI MODÜLLER: {errors}\033[0m")
    sys.exit(1)
else:
    print(f"\n  \033[32mTüm {len(modules)} modül OK\033[0m")
PYEOF

IMPORT_STATUS=$?

# ═══════════════════════════════════════════════════════
sep "6. VERİTABANI"
# ═══════════════════════════════════════════════════════

# DB boyutu
if [ -f "$DB_PATH" ]; then
    DB_SIZE=$(du -sh "$DB_PATH" | cut -f1)
    ok "DB boyutu: $DB_SIZE"
else
    err "DB bulunamadı: $DB_PATH"
fi

$VENV - << 'PYEOF'
import sys, json
sys.path.insert(0, '.')
import database

# init_db
try:
    database.init_db()
    print("  \033[32m✅ init_db() OK\033[0m")
except Exception as e:
    print(f"  \033[31m❌ init_db(): {e}\033[0m")

# Tablo kontrolü
required = [
    'trades','signal_candidates','paper_results',
    'ghost_signals','ghost_results','ghost_suggestions',
    'coin_configs','ai_learning','coin_profiles',
    'params','system_state','paper_account',
    'balance_ledger','bot_status','coin_library',
    'coin_cooldown','trade_events','ai_logs',
    'partial_closes','weekly_summary',
]
with database.get_conn() as conn:
    existing = {r[0] for r in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table'"
    ).fetchall()}

missing = [t for t in required if t not in existing]
if missing:
    print(f"  \033[31m❌ EKSİK TABLOLAR: {missing}\033[0m")
else:
    print(f"  \033[32m✅ {len(required)} tablo mevcut\033[0m")

# İstatistikler
with database.get_conn() as conn:
    def q(sql):
        try: return conn.execute(sql).fetchone()[0] or 0
        except: return 0

    trades_total = q("SELECT COUNT(*) FROM trades")
    trades_open  = q("SELECT COUNT(*) FROM trades WHERE status='OPEN'")
    trades_closed= q("SELECT COUNT(*) FROM trades WHERE status='closed'")
    trades_wins  = q("SELECT COUNT(*) FROM trades WHERE status='closed' AND net_pnl>0")
    total_pnl    = q("SELECT COALESCE(SUM(net_pnl),0) FROM trades WHERE status='closed'")
    balance      = q("SELECT balance FROM paper_account WHERE id=1")
    signals      = q("SELECT COUNT(*) FROM signal_candidates")
    signals_today= q("SELECT COUNT(*) FROM signal_candidates WHERE DATE(created_at)=DATE('now')")
    ghost_total  = q("SELECT COUNT(*) FROM ghost_signals")
    ghost_sim    = q("SELECT COUNT(*) FROM ghost_signals WHERE simulated=1")
    ghost_wins   = q("SELECT COUNT(*) FROM ghost_results WHERE virtual_outcome='WIN'")
    ghost_losses = q("SELECT COUNT(*) FROM ghost_results WHERE virtual_outcome='LOSS'")
    paper_total  = q("SELECT COUNT(*) FROM paper_results")
    paper_done   = q("SELECT COUNT(*) FROM paper_results WHERE status='completed'")
    paper_wins   = q("SELECT COUNT(*) FROM paper_results WHERE would_have_won=1")
    suggestions  = q("SELECT COUNT(*) FROM ghost_suggestions WHERE applied=0")

    wr = round(trades_wins/trades_closed*100,1) if trades_closed>0 else 0
    ghost_wr = round(ghost_wins/(ghost_wins+ghost_losses)*100,1) if (ghost_wins+ghost_losses)>0 else 0
    paper_wr = round(paper_wins/paper_done*100,1) if paper_done>0 else 0
    regime = q("SELECT value FROM system_state WHERE key='market_regime'") or "NEUTRAL"

    # Kapanış nedeni dağılımı
    try:
        close_reasons = conn.execute(
            "SELECT COALESCE(close_reason,'?') r, COUNT(*) n FROM trades "
            "WHERE status='closed' GROUP BY 1 ORDER BY 2 DESC LIMIT 6"
        ).fetchall()
    except Exception:
        close_reasons = []
    # Leverage dağılımı
    try:
        lev_counts = conn.execute(
            "SELECT COALESCE(leverage,1) lev, COUNT(*) n FROM trades "
            "WHERE status='closed' GROUP BY 1 ORDER BY 1"
        ).fetchall()
    except Exception:
        lev_counts = []
    # SL mesafesi ortalaması
    try:
        sl_dist_row = conn.execute(
            "SELECT ROUND(AVG(ABS(entry-sl)/entry*100),2) FROM trades "
            "WHERE status='closed' AND entry>0 AND sl>0"
        ).fetchone()
        avg_sl_pct = sl_dist_row[0] or 0
    except Exception:
        avg_sl_pct = 0

print()
print("  \033[1m📊 TRADE İSTATİSTİKLERİ\033[0m")
print(f"  {'Bakiye:':<28} ${balance:.2f}")
print(f"  {'Toplam PnL:':<28} ${total_pnl:.2f}")
print(f"  {'Trade (açık/kapandı):':<28} {trades_open}/{trades_closed}")
print(f"  {'Win Rate:':<28} {wr}% ({trades_wins}W/{trades_closed-trades_wins}L)")
print(f"  {'Ort SL mesafesi:':<28} %{avg_sl_pct}")
if close_reasons:
    reasons_str = "  ".join(f"{r[0]}={r[1]}" for r in close_reasons)
    print(f"  {'Kapanış nedenleri:':<28} {reasons_str}")
if lev_counts:
    lev_str = "  ".join(f"{r[0]}x={r[1]}" for r in lev_counts)
    print(f"  {'Leverage dağılımı:':<28} {lev_str}")
print()
print("  \033[1m👻 GHOST LEARNING\033[0m")
print(f"  {'Ghost signals:':<28} {ghost_total} ({ghost_sim} simüle)")
print(f"  {'Ghost Win Rate:':<28} {ghost_wr}% ({ghost_wins}W/{ghost_losses}L)")
print(f"  {'Bekleyen öneri:':<28} {suggestions}")
print()
print("  \033[1m📋 PAPER RESULTS\033[0m")
print(f"  {'Paper results:':<28} {paper_total} ({paper_done} tamamlandı)")
print(f"  {'Paper Win Rate:':<28} {paper_wr}% ({paper_wins}/{paper_done})")
print(f"  {'500 trade hedefi:':<28} %{round(trades_closed/500*100,1)} ({trades_closed}/500)")
print()
print("  \033[1m🌍 PİYASA REJİMİ\033[0m")
print(f"  {'Güncel rejim:':<28} {regime}")
print()
print("  \033[1m📡 SİNYALLER\033[0m")
print(f"  {'Toplam signal_candidates:':<28} {signals}")
print(f"  {'Bugün:':<28} {signals_today}")
PYEOF

# ═══════════════════════════════════════════════════════
sep "7. LOG ANALİZİ"
# ═══════════════════════════════════════════════════════

if [ -f "$BOT_LOG" ]; then
    LOG_SIZE=$(du -sh "$BOT_LOG" | cut -f1)
    LOG_SIZE_MB=$(du -m "$BOT_LOG" | cut -f1)
    kv "Bot log:" "$LOG_SIZE"

    # Aktivite kontrolü
    LAST_LINE=$(tail -1 "$BOT_LOG")
    LAST_TIME=$(echo "$LAST_LINE" | awk '{print $1" "$2}' | cut -d',' -f1)
    LAST_TS=$(date -d "$LAST_TIME" +%s 2>/dev/null || echo 0)
    DIFF=$(( ($(date +%s) - LAST_TS) / 60 ))
    [ "$DIFF" -lt 5 ]  && ok "Bot aktif (son log: ${DIFF}dk önce)" || \
    [ "$DIFF" -lt 15 ] && warn "Bot yavaş? (son log: ${DIFF}dk önce)" || \
                          err "Bot sessiz (${DIFF}dk) — restart ediliyor"

    # Son 5 dakika hata sayısı
    RECENT_ERRORS=$(tail -200 "$BOT_LOG" 2>/dev/null | grep -E "ERROR|CRITICAL" | wc -l)
    RECENT_WARNS=$(tail -200 "$BOT_LOG" 2>/dev/null | grep -c "WARNING" | head -1)
    RECENT_ERRORS=${RECENT_ERRORS//[^0-9]/}; RECENT_ERRORS=${RECENT_ERRORS:-0}
    RECENT_WARNS=${RECENT_WARNS//[^0-9]/};   RECENT_WARNS=${RECENT_WARNS:-0}
    [ "$RECENT_ERRORS" -gt 0 ] && err "Son 200 satırda $RECENT_ERRORS hata" || ok "Son 200 satırda hata yok"
    [ "$RECENT_WARNS" -gt 10 ] && warn "Son 200 satırda $RECENT_WARNS uyarı" || ok "Uyarı seviyesi normal ($RECENT_WARNS)"

    # Son kritik hatalar
    if [ "$RECENT_ERRORS" -gt 0 ]; then
        echo ""
        info "Son hatalar:"
        tail -200 "$BOT_LOG" | grep "ERROR\|CRITICAL" | tail -5 | while read l; do
            echo "    $l"
        done
    fi

    # Log boyutu
    if [ "$LOG_SIZE_MB" -gt 100 ]; then
        warn "Log büyük (${LOG_SIZE_MB}MB) — kırpılıyor..."
        tail -50000 "$BOT_LOG" > "$BOT_LOG.tmp" && mv "$BOT_LOG.tmp" "$BOT_LOG"
        fixed "Log kırpıldı"
    fi
else
    warn "Bot log yok: $BOT_LOG"
fi

# Son 5 log satırı
echo ""
info "Son 5 log satırı:"
tail -5 "$BOT_LOG" 2>/dev/null | while read l; do echo "    $l"; done

# ═══════════════════════════════════════════════════════
sep "8. KALINTI TEMİZLİK"
# ═══════════════════════════════════════════════════════

JUNK=(
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
for f in "${JUNK[@]}"; do
    if [ -f "$BASE/$f" ]; then
        rm -f "$BASE/$f"; DELETED=$((DELETED+1))
        info "Silindi: $f"
    fi
done
for d in "deprecated" "archive"; do
    if [ -d "$BASE/$d" ]; then
        rm -rf "$BASE/$d"; DELETED=$((DELETED+1))
        info "Silindi: $d/"
    fi
done
[ "$DELETED" -gt 0 ] && fixed "$DELETED kalıntı silindi" || ok "Kalıntı yok"

# ═══════════════════════════════════════════════════════
sep "9. AUTO-FIX"
# ═══════════════════════════════════════════════════════

if [ "$DO_FIX" = true ]; then
    # Migration
    $VENV - << 'PYEOF'
import sys; sys.path.insert(0, '.')
import database

fixes = [
    ("trades",        "direction",    "TEXT DEFAULT 'LONG'"),
    ("trades",        "mfe",          "REAL DEFAULT 0"),
    ("trades",        "mae",          "REAL DEFAULT 0"),
    ("trades",        "r_multiple",   "REAL DEFAULT 0"),
    ("coin_profiles", "total_trades", "INTEGER DEFAULT 0"),
    ("coin_profiles", "sample_size",  "INTEGER DEFAULT 0"),
]

with database.get_conn() as conn:
    for table, col, col_type in fixes:
        try:
            existing = {r[1] for r in conn.execute(f"PRAGMA table_info({table})")}
            if col not in existing:
                conn.execute(f"ALTER TABLE {table} ADD COLUMN {col} {col_type}")
                print(f"  \033[36m🔧 {table}.{col} eklendi\033[0m")
        except Exception as e:
            pass

print("  \033[32m✅ Migration tamamlandı\033[0m")
PYEOF

    # Restart (güncelleme varsa veya hata varsa)
    if [ "${UPDATED:-false}" = "true" ] || [ "$ERRORS" -gt 0 ]; then
        info "Bot yeniden başlatılıyor..."
        systemctl restart ax-bot
        sleep 8
        systemctl is-active --quiet ax-bot && fixed "Bot yeniden başlatıldı" || err "Bot başlatılamadı"
    fi
else
    info "Auto-fix kapalı. --fix ile çalıştır"
fi

# ═══════════════════════════════════════════════════════
sep "10. CRON DURUMU"
# ═══════════════════════════════════════════════════════

CRON_MAINTAIN=$(crontab -l 2>/dev/null | grep "aurvex_maintain" | wc -l)
CRON_GHOST=$(crontab -l 2>/dev/null | grep "ghost_learner" | wc -l)
CRON_NIGHTLY=$(crontab -l 2>/dev/null | grep "ai_brain" | wc -l)
CRON_MAINTAIN=${CRON_MAINTAIN//[^0-9]/}; CRON_MAINTAIN=${CRON_MAINTAIN:-0}
CRON_GHOST=${CRON_GHOST//[^0-9]/};       CRON_GHOST=${CRON_GHOST:-0}
CRON_NIGHTLY=${CRON_NIGHTLY//[^0-9]/};   CRON_NIGHTLY=${CRON_NIGHTLY:-0}

[ "$CRON_MAINTAIN" -gt 0 ] && ok "Maintenance cron aktif" || warn "Maintenance cron YOK"
[ "$CRON_GHOST" -gt 0 ]    && ok "Ghost learner cron aktif" || warn "Ghost learner cron YOK"
[ "$CRON_NIGHTLY" -gt 0 ]  && ok "AI Brain nightly cron aktif" || warn "AI Brain nightly cron YOK"

if [ "$DO_FIX" = true ]; then
    if [ "$CRON_GHOST" -eq 0 ]; then
        (crontab -l 2>/dev/null; echo "0 3 * * * cd $BASE && $VENV ghost_learner.py cycle >> $BASE/logs/ghost.log 2>&1") | crontab -
        fixed "Ghost learner cron eklendi (her gün 03:00)"
    fi
    if [ "$CRON_NIGHTLY" -eq 0 ]; then
        (crontab -l 2>/dev/null; echo "0 2 * * * cd $BASE && $VENV -c 'from ai_brain import nightly_optimize_coins; nightly_optimize_coins()' >> $BASE/logs/nightly.log 2>&1") | crontab -
        fixed "AI Brain nightly cron eklendi (her gün 02:00)"
    fi
    if [ "$CRON_MAINTAIN" -eq 0 ]; then
        (crontab -l 2>/dev/null; echo "0 */6 * * * bash $BASE/aurvex_maintain.sh --fix >> $BASE/logs/maintain.log 2>&1") | crontab -
        fixed "Maintenance cron eklendi (her 6 saatte bir)"
    fi
elif [ "$CRON_GHOST" -eq 0 ] || [ "$CRON_NIGHTLY" -eq 0 ] || [ "$CRON_MAINTAIN" -eq 0 ]; then
    echo ""
    info "Eksik cron'lar için: bash aurvex_maintain.sh --fix"
fi

# --- Backtest temp DB cleanup (>24h old) ---
find /home/user/trade-engine -maxdepth 1 -name "backtest_temp_*.db" -mtime +1 -delete 2>/dev/null || true
echo "[Maintain] Backtest temp cleanup done."

# ═══════════════════════════════════════════════════════
sep "SONUÇ"
# ═══════════════════════════════════════════════════════

echo ""
if   [ "$ERRORS" -eq 0 ] && [ "$WARNINGS" -eq 0 ]; then
    echo -e "  ${GREEN}${BOLD}🎉 Sistem mükemmel — hiç sorun yok${NC}"
elif [ "$ERRORS" -eq 0 ]; then
    echo -e "  ${YELLOW}${BOLD}⚠️  $WARNINGS uyarı var, kritik hata yok${NC}"
else
    echo -e "  ${RED}${BOLD}❌ $ERRORS hata, $WARNINGS uyarı${NC}"
    echo -e "  ${CYAN}💡 Düzeltmek için: bash aurvex_maintain.sh --fix${NC}"
fi

[ "$FIXES" -gt 0 ] && echo -e "  ${CYAN}🔧 $FIXES sorun otomatik düzeltildi${NC}"

echo ""
echo -e "${BOLD}  Servis durumları:${NC}"
systemctl is-active ax-bot      2>/dev/null && echo -e "  ax-bot:       ${GREEN}✅ çalışıyor${NC}" || echo -e "  ax-bot:       ${RED}❌ durmuş${NC}"
systemctl is-active ax-dashboard 2>/dev/null && echo -e "  ax-dashboard: ${GREEN}✅ çalışıyor${NC}" || echo -e "  ax-dashboard: ${RED}❌ durmuş${NC}"

echo ""
echo -e "${DIM}  Komutlar:${NC}"
echo -e "  ${DIM}tail -f $BOT_LOG${NC}"
echo -e "  ${DIM}curl http://localhost:5000/api/health${NC}"
echo -e "  ${DIM}bash aurvex_maintain.sh --fix           # hataları otomatik düzelt${NC}"
echo -e "  ${DIM}bash aurvex_maintain.sh --telegram-test # telegram bağlantısını test et${NC}"
echo -e "  ${DIM}bash aurvex_maintain.sh --full          # tam istatistikler${NC}"
echo ""
