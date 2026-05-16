#!/bin/bash
# ╔══════════════════════════════════════════════════════════════════════════════╗
# ║          AURVEX Trade Engine — Safe Update Script v2.0                      ║
# ║          Kullanım: bash update.sh [--skip-deps] [--force] [--dry-run]       ║
# ╚══════════════════════════════════════════════════════════════════════════════╝
#
#  --skip-deps   pip install atla (hızlı update)
#  --force       açık trade olsa bile sormadan kapat
#  --dry-run     hiçbir şeyi değiştirme, sadece ne yapılacağını göster
#  --no-audit    audit_pnl_consistency adımını atla
#  --status      sadece durum raporu göster, güncelleme yapma

set -euo pipefail

# ── Renkler & semboller ────────────────────────────────────────────────────────
RED='\033[0;31m';    GREEN='\033[0;32m';  YELLOW='\033[1;33m'
BLUE='\033[0;34m';   CYAN='\033[0;36m';  MAGENTA='\033[0;35m'
BOLD='\033[1m';      DIM='\033[2m';       NC='\033[0m'

SYM_OK="✅";   SYM_FAIL="❌";  SYM_WARN="⚠️ ";  SYM_INFO="ℹ️ "
SYM_RUN="🔄";  SYM_STOP="🛑";  SYM_UP="🚀";    SYM_DB="🗄️ "
SYM_NET="🌐";  SYM_LOCK="🔒";  SYM_TRADE="💹"; SYM_SNAP="📸"

ok()    { echo -e "  ${GREEN}${SYM_OK}  $1${NC}"; }
fail()  { echo -e "  ${RED}${SYM_FAIL}  $1${NC}"; }
warn()  { echo -e "  ${YELLOW}${SYM_WARN} $1${NC}"; }
info()  { echo -e "  ${CYAN}${SYM_INFO} $1${NC}"; }
run()   { echo -e "  ${MAGENTA}${SYM_RUN} $1${NC}"; }
step()  { echo -e "\n${BOLD}${BLUE}  ══════════════════════════════════════════${NC}"; \
          echo -e "${BOLD}${BLUE}  $1${NC}"; \
          echo -e "${BOLD}${BLUE}  ══════════════════════════════════════════${NC}"; }
dim()   { echo -e "  ${DIM}$1${NC}"; }
banner(){ echo -e "${BOLD}${CYAN}$1${NC}"; }

# ── Argümanlar ─────────────────────────────────────────────────────────────────
SKIP_DEPS=false; FORCE=false; DRY_RUN=false; NO_AUDIT=false; STATUS_ONLY=false
for arg in "$@"; do
    case $arg in
        --skip-deps) SKIP_DEPS=true ;;
        --force)     FORCE=true ;;
        --dry-run)   DRY_RUN=true ;;
        --no-audit)  NO_AUDIT=true ;;
        --status)    STATUS_ONLY=true ;;
    esac
done

dry() {
    if [ "$DRY_RUN" = true ]; then
        echo -e "  ${DIM}[DRY-RUN] $*${NC}"
        return 0
    fi
    "$@"
}

# ── Sabitler ───────────────────────────────────────────────────────────────────
TRADE_DIR="/root/trade_engine/trade-engine"
ROOT_DIR="/root/trade_engine"

# DB: trade-engine içinde varsa onu kullan, yoksa üst dizinden
if [ -f "$TRADE_DIR/trading.db" ]; then
    DB_PATH="$TRADE_DIR/trading.db"
else
    DB_PATH="$ROOT_DIR/trading.db"
fi

# Venv: birkaç olası yeri dene
if   [ -f "$TRADE_DIR/.venv/bin/python3" ]; then  VENV="$TRADE_DIR/.venv"
elif [ -f "$ROOT_DIR/.venv/bin/python3"  ]; then  VENV="$ROOT_DIR/.venv"
elif [ -f "$ROOT_DIR/venv/bin/python3"   ]; then  VENV="$ROOT_DIR/venv"
elif [ -f "$TRADE_DIR/venv/bin/python3"  ]; then  VENV="$TRADE_DIR/venv"
else
    echo -e "  ${RED}❌ Python venv bulunamadı! Lütfen önce venv kurun.${NC}"
    exit 1
fi

PYTHON="$VENV/bin/python3"
PIP="$VENV/bin/pip"
BRANCH="main"
DASHBOARD_PORT=5000
LOG_DIR="$ROOT_DIR/logs"
BACKUP_DIR="$ROOT_DIR/backups"
TIMESTAMP=$(date +%Y%m%d_%H%M%S)
START_TIME=$(date +%s)

# Tüm olası servis isimleri (eski + yeni)
OLD_SERVICES=("ax-bot" "ax-dashboard")
NEW_SERVICES=("aurvex-dashboard" "aurvex-bot")   # dashboard önce başlar

# ── Başlık ─────────────────────────────────────────────────────────────────────
clear
echo ""
echo -e "${BOLD}${CYAN}╔══════════════════════════════════════════════════════════════╗${NC}"
echo -e "${BOLD}${CYAN}║         AURVEX AI Trade Engine — Safe Updater v2.0          ║${NC}"
echo -e "${BOLD}${CYAN}║         $(date '+%Y-%m-%d %H:%M:%S %Z')                           ║${NC}"
echo -e "${BOLD}${CYAN}╚══════════════════════════════════════════════════════════════╝${NC}"
echo ""
[ "$DRY_RUN"     = true ] && warn "DRY-RUN modu: hiçbir değişiklik yapılmayacak"
[ "$FORCE"       = true ] && warn "FORCE modu: açık trade'ler sorulmadan kapatılacak"
[ "$SKIP_DEPS"   = true ] && info "SKIP-DEPS: pip install atlanacak"
[ "$STATUS_ONLY" = true ] && info "STATUS modu: sadece durum raporu"

# ── Root kontrolü ──────────────────────────────────────────────────────────────
if [ "$EUID" -ne 0 ]; then
    fail "Root yetkisi gerekli!  →  sudo bash update.sh"
    exit 1
fi

# ══════════════════════════════════════════════════════════════════════════════
# STATUS ONLY MODU
# ══════════════════════════════════════════════════════════════════════════════
if [ "$STATUS_ONLY" = true ]; then
    step "DURUM RAPORU"
    echo ""

    for SVC in "${OLD_SERVICES[@]}" "${NEW_SERVICES[@]}"; do
        if systemctl list-unit-files --quiet "$SVC.service" &>/dev/null 2>&1 && \
           systemctl list-unit-files "$SVC.service" 2>/dev/null | grep -q "$SVC"; then
            if systemctl is-active --quiet "$SVC" 2>/dev/null; then
                ok "$SVC: ÇALIŞIYOR"
            else
                STATE=$(systemctl is-active "$SVC" 2>/dev/null || echo "unknown")
                warn "$SVC: $STATE"
            fi
        fi
    done

    echo ""
    HEALTH=$(curl -sf --max-time 5 "http://127.0.0.1:$DASHBOARD_PORT/api/health" 2>/dev/null || echo "{}")
    if echo "$HEALTH" | python3 -c "import sys,json; d=json.load(sys.stdin); print('ok')" &>/dev/null 2>&1; then
        ok "Dashboard API: YANIT VERİYOR"
        echo "$HEALTH" | python3 -m json.tool 2>/dev/null | grep -E '"(ok|bot_running|execution_mode|balance)"' | sed 's/^/    /' || true
    else
        fail "Dashboard API: YANIT YOK (port $DASHBOARD_PORT)"
    fi

    echo ""
    if [ -f "$DB_PATH" ]; then
        "$PYTHON" - << 'PYEOF' 2>/dev/null || warn "DB okunamadı"
import sys, os
sys.path.insert(0, os.environ.get("TRADE_DIR", "/root/trade_engine/trade-engine"))
os.chdir(sys.path[0])
import sqlite3
DB = os.environ.get("DB_PATH", "/root/trade_engine/trading.db")
conn = sqlite3.connect(DB)
open_t = conn.execute("SELECT COUNT(*) FROM trades WHERE status='open'").fetchone()[0]
total  = conn.execute("SELECT COUNT(*) FROM trades").fetchone()[0]
try:
    paper_t = conn.execute("SELECT COUNT(*) FROM paper_results").fetchone()[0]
    pending = conn.execute("SELECT COUNT(*) FROM paper_results WHERE status='pending'").fetchone()[0]
    print(f"  {SYM_TRADE}  Açık trade   : {open_t}   |   Toplam: {total}")
    print(f"  {SYM_DB}  Paper results: {paper_t}   |   Pending: {pending}")
except: pass
conn.close()
PYEOF
    fi

    CD=$(cd "$TRADE_DIR" 2>/dev/null && git log -1 --pretty=format:"%h — %s" 2>/dev/null || echo "bilinmiyor")
    dim "Son commit: $CD"
    echo ""
    exit 0
fi

# ══════════════════════════════════════════════════════════════════════════════
# ADIM 1 — PRE-CHECK
# ══════════════════════════════════════════════════════════════════════════════
step "1/9 ${SYM_INFO}  ÖN KONTROL"

[ -d "$TRADE_DIR" ]  || { fail "Proje dizini bulunamadı: $TRADE_DIR"; exit 1; }
[ -d "$TRADE_DIR/.git" ] || { fail "Git repo bulunamadı: $TRADE_DIR/.git"; exit 1; }
[ -f "$PYTHON" ]     || { fail "Python venv bulunamadı: $PYTHON"; exit 1; }

ok "Proje dizini mevcut: $TRADE_DIR"
ok "Git repo mevcut"
ok "Python venv mevcut: $PYTHON"

# Remote erişim testi
if git -C "$TRADE_DIR" ls-remote origin --quiet &>/dev/null 2>&1; then
    ok "GitHub erişimi: OK"
else
    fail "GitHub erişimi başarısız — internet bağlantısını kontrol et"
    exit 1
fi

# ══════════════════════════════════════════════════════════════════════════════
# ADIM 2 — AÇIK TRADE SNAPSHOT & GÜVENLİ KAPANIŞ
# ══════════════════════════════════════════════════════════════════════════════
step "2/9 ${SYM_TRADE}  AÇIK TRADE KONTROLÜ"

OPEN_TRADE_COUNT=0
if [ -f "$DB_PATH" ]; then
    OPEN_TRADE_COUNT=$("$PYTHON" - << 'PYEOF' 2>/dev/null || echo "0"
import sqlite3, os
DB = os.environ.get("DB_PATH", "/root/trade_engine/trading.db")
conn = sqlite3.connect(DB)
try:
    n = conn.execute("SELECT COUNT(*) FROM trades WHERE status='open'").fetchone()[0]
    print(n)
except:
    print(0)
conn.close()
PYEOF
)
fi

OPEN_TRADE_COUNT=${OPEN_TRADE_COUNT:-0}

if [ "$OPEN_TRADE_COUNT" -eq 0 ]; then
    ok "Açık trade yok — güvenle devam edilebilir"
else
    echo ""
    warn "${OPEN_TRADE_COUNT} açık trade tespit edildi:"
    echo ""

    # Açık tradeleri listele
    "$PYTHON" - << 'PYEOF' 2>/dev/null || true
import sqlite3, os

candidates = ["/root/trade_engine/trade-engine/trading.db", "/root/trade_engine/trading.db"]
DB = next((p for p in candidates if os.path.exists(p)), candidates[-1])
conn = sqlite3.connect(DB)
conn.row_factory = sqlite3.Row
try:
    cols = [r[1] for r in conn.execute("PRAGMA table_info(trades)").fetchall()]
    price_col = next((c for c in ["entry_price","open_price","price","avg_price"] if c in cols), None)
    qty_col   = next((c for c in ["quantity","qty","amount","size"] if c in cols), None)
    dir_col   = next((c for c in ["direction","side","type","trade_type"] if c in cols), None)
    lv_col    = next((c for c in ["leverage","lv"] if c in cols), None)
    ts_col    = next((c for c in ["created_at","opened_at","timestamp"] if c in cols), None)
    rows = conn.execute("SELECT * FROM trades WHERE status='open' ORDER BY id DESC").fetchall()
    print(f"  {'ID':>4}  {'Sembol':<12} {'Yön':<6} {'Giriş':>10} {'Adet':>8} {'Lv':>4}  {'Açılış'}")
    print(f"  {'─'*4}  {'─'*12} {'─'*6} {'─'*10} {'─'*8} {'─'*4}  {'─'*19}")
    for r in rows:
        ep  = f"{r[price_col]:>10.4f}" if price_col else "        ?"
        qty = f"{r[qty_col]:>8.4f}"   if qty_col   else "       ?"
        sid = r[dir_col][:6]           if dir_col   else "?"
        lv  = f"{r[lv_col]:>4}x"      if lv_col    else "   ?"
        ts  = r[ts_col][:19]           if ts_col    else "?"
        print(f"  {r['id']:>4}  {r['symbol']:<12} {sid:<6} {ep} {qty} {lv}  {ts}")
except Exception as e:
    print(f"  Tablo okunamadı: {e}")
conn.close()
PYEOF
    echo ""

    CLOSE_TRADES=false
    if [ "$FORCE" = true ]; then
        warn "FORCE modu aktif — tüm açık trade'ler kapatılıyor..."
        CLOSE_TRADES=true
    elif [ "$DRY_RUN" = true ]; then
        info "[DRY-RUN] Gerçek çalışmada kullanıcıya sorulacak"
        CLOSE_TRADES=false
    else
        echo -en "  ${YELLOW}${BOLD}Güncelleme öncesi tüm açık trade'ler kapatılsın mı? (paper modda güvenlidir) [y/N]: ${NC}"
        read -r -t 30 REPLY || REPLY="n"
        echo ""
        [[ "$REPLY" =~ ^[Yy]$ ]] && CLOSE_TRADES=true || CLOSE_TRADES=false
    fi

    if [ "$CLOSE_TRADES" = true ] && [ "$DRY_RUN" = false ]; then
        run "Paper trade'ler 'system_update' gerekçesiyle kapatılıyor..."
        "$PYTHON" - << 'PYEOF' 2>&1 | sed 's/^/    /'
import sqlite3, os, sys
from datetime import datetime

# DB yolunu bul
candidates = [
    "/root/trade_engine/trade-engine/trading.db",
    "/root/trade_engine/trading.db",
]
DB_PATH = next((p for p in candidates if os.path.exists(p)), None)
if not DB_PATH:
    print("HATA: trading.db bulunamadı"); sys.exit(1)

conn = sqlite3.connect(DB_PATH)
conn.row_factory = sqlite3.Row

# Gerçek kolon adlarını oku
cols = [r[1] for r in conn.execute("PRAGMA table_info(trades)").fetchall()]
print(f"Kolon listesi: {cols}")

# Fiyat kolonunu tespit et
price_col = next((c for c in ["entry_price","open_price","price","avg_price"] if c in cols), None)
qty_col   = next((c for c in ["quantity","qty","amount","size"] if c in cols), None)
dir_col   = next((c for c in ["direction","side","type","trade_type"] if c in cols), None)

rows = conn.execute("SELECT * FROM trades WHERE status='open'").fetchall()
now  = datetime.utcnow().strftime("%Y-%m-%d %H:%M:%S")

closed = 0
for row in rows:
    tid    = row["id"]
    symbol = row["symbol"] if "symbol" in cols else "?"
    ep     = row[price_col] if price_col else 0.0
    qty    = row[qty_col]   if qty_col   else 0.0
    side   = row[dir_col]   if dir_col   else "?"
    fee    = round(ep * qty * 0.0004 * 2, 6) if ep and qty else 0.0

    # Direkt SQL ile güncelle — hiçbir custom import gerekmez
    updates = {"status": "closed", "close_reason": "system_update",
               "net_pnl": -fee, "closed_at": now}
    if "close_price" in cols: updates["close_price"] = ep
    if "total_fee"   in cols: updates["total_fee"]   = fee

    set_clause = ", ".join(f"{k}=?" for k in updates)
    conn.execute(f"UPDATE trades SET {set_clause} WHERE id=?",
                 list(updates.values()) + [tid])
    print(f"Kapatıldı → #{tid} {symbol} {side}  fee={fee:.4f}")
    closed += 1

conn.commit()
conn.close()
print(f"\nToplam {closed} trade kapatıldı.")
PYEOF
        ok "Trade'ler güvenle kapatıldı"
    elif [ "$CLOSE_TRADES" = false ]; then
        warn "Trade'ler kapatılmadı — güncelleme sonrası manuel kontrol gerekebilir"
    fi
fi

# ══════════════════════════════════════════════════════════════════════════════
# ADIM 3 — DB YEDEK
# ══════════════════════════════════════════════════════════════════════════════
step "3/9 ${SYM_SNAP}  VERİTABANI YEDEĞİ"

dry mkdir -p "$BACKUP_DIR"

if [ -f "$DB_PATH" ] && [ "$DRY_RUN" = false ]; then
    BACKUP_FILE="$BACKUP_DIR/trading_${TIMESTAMP}.db"
    cp "$DB_PATH" "$BACKUP_FILE"
    BACKUP_SIZE=$(du -sh "$BACKUP_FILE" 2>/dev/null | cut -f1 || echo "?")
    ok "DB yedeği alındı: $BACKUP_FILE ($BACKUP_SIZE)"

    # 10'dan fazla yedek varsa eskiyi sil
    BACKUP_COUNT=$(ls "$BACKUP_DIR"/trading_*.db 2>/dev/null | wc -l)
    if [ "$BACKUP_COUNT" -gt 10 ]; then
        ls -t "$BACKUP_DIR"/trading_*.db | tail -n +11 | xargs rm -f
        dim "Eski yedekler temizlendi (10 adet korundu)"
    fi
else
    [ "$DRY_RUN" = true ] && dim "[DRY-RUN] cp $DB_PATH → $BACKUP_DIR/trading_${TIMESTAMP}.db" || warn "DB dosyası bulunamadı, yedek atlandı"
fi

# ══════════════════════════════════════════════════════════════════════════════
# ADIM 4 — SERVİSLERİ DURDUR
# ══════════════════════════════════════════════════════════════════════════════
step "4/9 ${SYM_STOP}  TÜM SERVİSLER DURDURULUYOR"

for SVC in "${NEW_SERVICES[@]}" "${OLD_SERVICES[@]}"; do
    if systemctl list-unit-files "$SVC.service" &>/dev/null 2>&1 && \
       systemctl list-unit-files "$SVC.service" 2>/dev/null | grep -q "$SVC"; then
        if systemctl is-active --quiet "$SVC" 2>/dev/null; then
            dry systemctl stop "$SVC"
            ok "$SVC durduruldu"
        else
            dim "$SVC zaten çalışmıyor — atlandı"
        fi
    else
        dim "$SVC kurulu değil — atlandı"
    fi
done

# Artık process kalmadığından emin ol
if [ "$DRY_RUN" = false ]; then
    LEFTOVER=$(pgrep -f "scalp_bot.py\|scalp_bot_v3.py\|app.py" 2>/dev/null || true)
    if [ -n "$LEFTOVER" ]; then
        warn "Arka planda kalan Python süreçleri kapatılıyor..."
        pkill -f "scalp_bot.py"   2>/dev/null || true
        pkill -f "scalp_bot_v3.py" 2>/dev/null || true
        sleep 2
        ok "Artık süreçler temizlendi"
    fi
fi

# ══════════════════════════════════════════════════════════════════════════════
# ADIM 5 — GİT PULL
# ══════════════════════════════════════════════════════════════════════════════
step "5/9 ${SYM_RUN}  GİTHUB'DAN GÜNCELLEME ÇEKİLİYOR"

cd "$TRADE_DIR"

CURRENT_COMMIT=$(git rev-parse --short HEAD 2>/dev/null || echo "bilinmiyor")
dim "Mevcut commit: $CURRENT_COMMIT"

# Local değişiklikleri stash'e al
STASH_RESULT=$(git stash 2>&1 || true)
if echo "$STASH_RESULT" | grep -q "No local changes"; then
    dim "Yerel değişiklik yok, stash atlandı"
else
    dim "Yerel değişiklikler stash'e alındı"
fi

run "git fetch origin $BRANCH..."
dry git fetch origin "$BRANCH"

REMOTE_COMMIT=$(git rev-parse --short "origin/$BRANCH" 2>/dev/null || echo "bilinmiyor")
dim "Remote commit : $REMOTE_COMMIT"

if [ "$CURRENT_COMMIT" = "$REMOTE_COMMIT" ]; then
    info "Zaten güncel — yeni commit yok"
else
    info "Yeni commit(ler) var: $CURRENT_COMMIT → $REMOTE_COMMIT"
    run "git reset --hard origin/$BRANCH..."
    dry git reset --hard "origin/$BRANCH"
    NEW_COMMIT=$(git rev-parse --short HEAD 2>/dev/null || echo "bilinmiyor")
    ok "Kod güncellendi: $NEW_COMMIT"
    echo ""
    git log --oneline -5 2>/dev/null | while read -r line; do dim "  $line"; done
fi

# Merge conflict kontrolü
CONFLICTS=$(grep -rn "<<<<<<< HEAD" . \
    --include="*.py" \
    --exclude-dir=".git" \
    --exclude-dir="archive" \
    --exclude-dir="deprecated" \
    2>/dev/null | wc -l || echo "0")
if [ "$CONFLICTS" -gt 0 ]; then
    fail "Merge conflict bulundu! ($CONFLICTS satır)"
    grep -rn "<<<<<<< HEAD" . --include="*.py" --exclude-dir=".git" 2>/dev/null | head -10 | sed 's/^/    /'
    exit 1
else
    ok "Merge conflict yok"
fi

# ══════════════════════════════════════════════════════════════════════════════
# ADIM 6 — BAĞIMLILIKLAR
# ══════════════════════════════════════════════════════════════════════════════
step "6/9 ${SYM_LOCK}  PYTHON BAĞIMLILIKLARI"

if [ "$SKIP_DEPS" = true ]; then
    info "pip install atlandı (--skip-deps)"
else
    run "pip install -r requirements.txt..."
    dry "$PIP" install -r requirements.txt -q 2>&1 | tail -5
    ok "Bağımlılıklar güncellendi"
fi

# ══════════════════════════════════════════════════════════════════════════════
# ADIM 7 — DB MİGRASYON & AUDIT
# ══════════════════════════════════════════════════════════════════════════════
step "7/9 ${SYM_DB}  DB MİGRASYON & AUDIT"

run "init_db() çalıştırılıyor..."
if [ "$DRY_RUN" = false ]; then
"$PYTHON" - << 'PYEOF' 2>&1 | sed 's/^/    /'
import sys, os
sys.path.insert(0, os.environ.get("TRADE_DIR", "/root/trade_engine/trade-engine"))
os.chdir(sys.path[0])

try:
    import database
    database.init_db()
    print("init_db() → OK")
    database._TRADE_COLUMNS = None   # cache sıfırla
    print("Cache sıfırlandı")

    conn = database.get_connection()
    trade_count  = conn.execute("SELECT COUNT(*) FROM trades").fetchone()[0]
    paper_total  = conn.execute("SELECT COUNT(*) FROM paper_results").fetchone()[0]
    paper_pend   = conn.execute("SELECT COUNT(*) FROM paper_results WHERE status='pending'").fetchone()[0]
    paper_done   = conn.execute("SELECT COUNT(*) FROM paper_results WHERE status='completed'").fetchone()[0]
    conn.close()
    print(f"Trades      : {trade_count}")
    print(f"Paper total : {paper_total}  (pending={paper_pend}, completed={paper_done})")
except Exception as e:
    print(f"HATA: {e}")
    sys.exit(1)
PYEOF
    ok "DB init & cache temizlendi"
fi

run "migrate_accounting_schema.py çalıştırılıyor..."
if [ "$DRY_RUN" = false ]; then
    "$PYTHON" scripts/migrate_accounting_schema.py 2>&1 | grep -v "^$" | head -20 | sed 's/^/    /' || warn "Migration uyarı verdi"
    ok "Schema migration tamamlandı"
fi

if [ "$NO_AUDIT" = false ] && [ "$DRY_RUN" = false ]; then
    run "audit_pnl_consistency.py çalıştırılıyor..."
    AUDIT_OUT=$("$PYTHON" scripts/audit_pnl_consistency.py 2>&1 || true)
    ERROR_COUNT=$(echo "$AUDIT_OUT" | grep -c "ERROR" || echo "0")
    WARN_COUNT=$(echo "$AUDIT_OUT"  | grep -c "WARNING" || echo "0")
    echo "$AUDIT_OUT" | grep -E "ERROR|WARNING|PASS|OK" | head -15 | sed 's/^/    /' || true
    if [ "$ERROR_COUNT" -gt 0 ]; then
        fail "Audit: $ERROR_COUNT ERROR — DB bütünlük sorunu var!"
    else
        ok "Audit: 0 ERROR, $WARN_COUNT WARNING"
    fi
else
    [ "$NO_AUDIT" = true ] && dim "Audit atlandı (--no-audit)" || dim "[DRY-RUN] Audit atlandı"
fi

# ══════════════════════════════════════════════════════════════════════════════
# ADIM 8 — SERVİSLERİ BAŞLAT
# ══════════════════════════════════════════════════════════════════════════════
step "8/9 ${SYM_UP}  SERVİSLER BAŞLATILIYOR"

# Systemd unit dosyalarını repo'dan kopyala
for SVC in "aurvex-bot" "aurvex-dashboard"; do
    SVC_FILE="$TRADE_DIR/${SVC}.service"
    if [ -f "$SVC_FILE" ]; then
        dry cp "$SVC_FILE" "/etc/systemd/system/${SVC}.service"
        dim "Service dosyası güncellendi: /etc/systemd/system/${SVC}.service"
    fi
done

dry systemctl daemon-reload

for SVC in "${NEW_SERVICES[@]}"; do
    dry systemctl enable "$SVC" 2>/dev/null || true
    run "$SVC başlatılıyor..."
    dry systemctl restart "$SVC"
    sleep 3
    if [ "$DRY_RUN" = false ]; then
        if systemctl is-active --quiet "$SVC"; then
            ok "$SVC: ÇALIŞIYOR"
        else
            fail "$SVC: BAŞLAMADI!"
            echo ""
            warn "Son 25 log satırı ($SVC):"
            journalctl -u "$SVC" -n 25 --no-pager 2>/dev/null | sed 's/^/    /' || true
            echo ""
            fail "Deploy başarısız. DB yedeği: $BACKUP_DIR/trading_${TIMESTAMP}.db"
            exit 1
        fi
    else
        dim "[DRY-RUN] systemctl restart $SVC"
    fi
done

# ══════════════════════════════════════════════════════════════════════════════
# ADIM 9 — HEALTH CHECK
# ══════════════════════════════════════════════════════════════════════════════
step "9/9 ${SYM_NET}  HEALTH CHECK"

if [ "$DRY_RUN" = true ]; then
    info "[DRY-RUN] Health check atlandı"
else

# Dashboard API bekle (max 20sn)
echo ""
info "Dashboard API bekleniyor (maks 20sn)..."
for i in $(seq 1 10); do
    HTTP_CODE=$(curl -s -o /dev/null -w "%{http_code}" --max-time 3 \
        "http://127.0.0.1:$DASHBOARD_PORT/api/health" 2>/dev/null || echo "000")
    [ "$HTTP_CODE" = "200" ] && break
    sleep 2
done

if [ "$HTTP_CODE" = "200" ]; then
    ok "Dashboard API: 200 OK"
    HEALTH_JSON=$(curl -sf --max-time 5 "http://127.0.0.1:$DASHBOARD_PORT/api/health" 2>/dev/null || echo "{}")
    echo ""
    echo -e "  ${BOLD}Dashboard durum:${NC}"
    echo "$HEALTH_JSON" | python3 -m json.tool 2>/dev/null \
        | grep -E '"(ok|bot_running|execution_mode|paper_mode|balance|open_trades|db_connected)"' \
        | sed 's/^/    /' || echo "    (JSON parse edilemedi)"
    echo ""
else
    warn "Dashboard API: $HTTP_CODE — app.py başlıyor olabilir"
    info "Manuel kontrol: curl http://127.0.0.1:$DASHBOARD_PORT/api/health"
fi

# Python import sanity
echo ""
info "Modül import testi (19 modül)..."
IMPORT_OUT=$("$PYTHON" - 2>&1 << 'PYEOF'
import sys, os
sys.path.insert(0, "/root/trade_engine/trade-engine")
os.chdir(sys.path[0])
mods = [
    "config", "database", "core.accounting", "core.trigger_engine",
    "core.risk_engine", "core.trend_engine", "core.ai_decision_engine",
    "core.market_scanner", "execution_engine", "telegram_delivery",
    "signal_engine", "ml_signal_scorer", "ai_brain", "dashboard_service",
    "core.paper_tracker", "core.ghost_learning", "core.trailing_engine",
    "core.watchdog", "core.signal_intelligence"
]
failed = 0
for m in mods:
    try:
        __import__(m)
        print(f"OK  {m}")
    except Exception as e:
        print(f"ERR {m}: {e}")
        failed += 1
if failed:
    print(f"\n{failed} modul BASARISIZ")
    sys.exit(1)
else:
    print(f"\n19/19 modul import basarili")
PYEOF
)
IMPORT_STATUS=$?
while IFS= read -r line; do
    if [[ "$line" == OK* ]];   then echo -e "  ${GREEN}✅ $line${NC}"
    elif [[ "$line" == ERR* ]]; then echo -e "  ${RED}❌ $line${NC}"
    else echo -e "  ${DIM}$line${NC}"; fi
done <<< "$IMPORT_OUT"

# Binance API testi
echo ""
BINANCE=$(curl -s -o /dev/null -w "%{http_code}" --max-time 6 \
    "https://fapi.binance.com/fapi/v1/ping" 2>/dev/null || echo "000")
[ "$BINANCE" = "200" ] && ok "Binance Futures API: ERİŞİLEBİLİR" || warn "Binance API: $BINANCE"

# Paper results özet
echo ""
"$PYTHON" - << 'PYEOF' 2>/dev/null | sed 's/^/  /'
import sys, os, sqlite3
DB = os.environ.get("DB_PATH", "/root/trade_engine/trading.db")
conn = sqlite3.connect(DB)
try:
    total   = conn.execute("SELECT COUNT(*) FROM paper_results").fetchone()[0]
    pending = conn.execute("SELECT COUNT(*) FROM paper_results WHERE status='pending'").fetchone()[0]
    done    = conn.execute("SELECT COUNT(*) FROM paper_results WHERE status='completed'").fetchone()[0]
    wins    = conn.execute("SELECT COUNT(*) FROM paper_results WHERE would_have_won=1").fetchone()[0]
    open_t  = conn.execute("SELECT COUNT(*) FROM trades WHERE status='open'").fetchone()[0]
    print(f"💹 Açık trade    : {open_t}")
    print(f"📊 Paper results : {total} toplam | {pending} pending | {done} completed")
    if done > 0:
        print(f"🎯 Winrate       : {wins/done*100:.1f}%  ({wins}/{done})")
except Exception as e:
    print(f"DB özet alınamadı: {e}")
conn.close()
PYEOF

fi  # DRY_RUN check biter

# ══════════════════════════════════════════════════════════════════════════════
# SONUÇ
# ══════════════════════════════════════════════════════════════════════════════
END_TIME=$(date +%s)
ELAPSED=$((END_TIME - START_TIME))
FINAL_COMMIT=$(git -C "$TRADE_DIR" log -1 --pretty=format:"%h — %s" 2>/dev/null || echo "bilinmiyor")
SERVER_IP=$(curl -sf --max-time 4 ifconfig.me 2>/dev/null || hostname -I | awk '{print $1}' || echo "localhost")

echo ""
if [ "$DRY_RUN" = true ]; then
    echo -e "${BOLD}${CYAN}╔══════════════════════════════════════════════════════════════╗${NC}"
    echo -e "${BOLD}${CYAN}║   ℹ️  DRY-RUN TAMAMLANDI — gerçek değişiklik yapılmadı      ║${NC}"
    echo -e "${BOLD}${CYAN}╚══════════════════════════════════════════════════════════════╝${NC}"
elif [ "${IMPORT_STATUS:-0}" -eq 0 ] && [ "${HTTP_CODE:-000}" = "200" ]; then
    echo -e "${BOLD}${GREEN}╔══════════════════════════════════════════════════════════════╗${NC}"
    echo -e "${BOLD}${GREEN}║         ✅  GÜNCELLEME BAŞARIYLA TAMAMLANDI!                ║${NC}"
    echo -e "${BOLD}${GREEN}║         Süre: ${ELAPSED}sn  |  Commit: ${FINAL_COMMIT:0:40}  ║${NC}"
    echo -e "${BOLD}${GREEN}╚══════════════════════════════════════════════════════════════╝${NC}"
else
    echo -e "${BOLD}${YELLOW}╔══════════════════════════════════════════════════════════════╗${NC}"
    echo -e "${BOLD}${YELLOW}║   ⚠️  GÜNCELLEME TAMAMLANDI (uyarılar var — logları kontrol) ║${NC}"
    echo -e "${BOLD}${YELLOW}║   Süre: ${ELAPSED}sn                                              ║${NC}"
    echo -e "${BOLD}${YELLOW}╚══════════════════════════════════════════════════════════════╝${NC}"
fi

echo ""
echo -e "  ${BOLD}🌐 Dashboard :${NC} http://$SERVER_IP:$DASHBOARD_PORT"
echo -e "  ${BOLD}🔗 Health   :${NC} http://$SERVER_IP:$DASHBOARD_PORT/api/health"
echo -e "  ${BOLD}📊 Stats    :${NC} http://$SERVER_IP:$DASHBOARD_PORT/api/stats"
echo ""
echo -e "${BOLD}  Günlük komutlar:${NC}"
echo "    journalctl -u aurvex-bot -f -n 50        → Bot canlı log"
echo "    journalctl -u aurvex-dashboard -f -n 50  → Dashboard log"
echo "    systemctl status aurvex-bot               → Bot durumu"
echo "    bash update.sh --status                   → Hızlı durum raporu"
echo "    bash update.sh --skip-deps                → Hızlı güncelleme (deps atla)"
echo "    bash update.sh --dry-run                  → Simülasyon (değişiklik yok)"
echo ""
[ -f "$BACKUP_DIR/trading_${TIMESTAMP}.db" ] && \
    dim "  DB yedeği: $BACKUP_DIR/trading_${TIMESTAMP}.db"
echo ""
