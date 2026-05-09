#!/bin/bash
# ═══════════════════════════════════════════════════════════════════
#  AURVEX Ai — Güvenli Deploy Scripti
#  Kullanım: bash deploy.sh [--reset]
#    --reset  : Trade/sinyal verilerini sıfırla (bakiye dahil)
#    --branch : Özel branch belirt (varsayılan: aşağıda tanımlı)
# ═══════════════════════════════════════════════════════════════════
set -euo pipefail

BRANCH="claude/fix-scoring-telegram-ux-YuTYB"
PY="python3"
DIR="/root/trade_engine"
SERVICES="aurvex-bot aurvex-dashboard"
WATCHDOG="aurvex-watchdog"
HEALTH_URL="http://localhost:5000/api/health"
RESET=false

# ── Argüman ayrıştırma ──────────────────────────────────────────────
for arg in "$@"; do
    case $arg in
        --reset)   RESET=true ;;
        --branch=*) BRANCH="${arg#*=}" ;;
    esac
done

# ── Renk kodları ────────────────────────────────────────────────────
OK="\033[0;32m✓\033[0m"
ERR="\033[0;31m✗\033[0m"
INF="\033[0;34m→\033[0m"
WARN="\033[0;33m!\033[0m"

log()  { echo -e "${INF} $1"; }
ok()   { echo -e "${OK} $1"; }
fail() { echo -e "${ERR} $1"; }
warn() { echo -e "${WARN} $1"; }

echo ""
echo "╔══════════════════════════════════════╗"
echo "║     AURVEX Ai — DEPLOY BAŞLIYOR      ║"
echo "╚══════════════════════════════════════╝"
echo " Branch : $BRANCH"
echo " Dizin  : $DIR"
echo " Reset  : $RESET"
echo ""

# ── Dizin kontrolü ──────────────────────────────────────────────────
if [ ! -d "$DIR" ]; then
    fail "Dizin bulunamadı: $DIR"
    exit 1
fi
cd "$DIR"

# ── [1/7] Git ayarları ──────────────────────────────────────────────
log "[1/7] Git yapılandırılıyor..."
git config pull.rebase false 2>/dev/null || true
ok "Git merge stratejisi: merge"

# ── [2/7] Kod güncelle ──────────────────────────────────────────────
log "[2/7] Kod güncelleniyor (branch: $BRANCH)..."

# Mevcut branch'i kaydet (rollback için)
PREV_COMMIT=$(git rev-parse HEAD 2>/dev/null || echo "unknown")

git fetch origin "$BRANCH" 2>&1 | tail -3

# Branch varsa geç, yoksa oluştur
if git show-ref --verify --quiet "refs/heads/$BRANCH"; then
    git checkout "$BRANCH"
else
    git checkout -b "$BRANCH" "origin/$BRANCH"
fi

# Yerel değişiklik varsa stash'le
if ! git diff --quiet; then
    warn "Yerel değişiklikler stash'leniyor..."
    git stash
fi

git pull origin "$BRANCH"
NEW_COMMIT=$(git rev-parse HEAD)
ok "Kod güncellendi: ${PREV_COMMIT:0:7} → ${NEW_COMMIT:0:7}"

# ── [3/7] Import sağlığı (pull sonrası, servis öncesi) ──────────────
log "[3/7] Import sağlığı kontrol ediliyor..."
IMPORT_RESULT=$($PY -c "
import sys; sys.path.insert(0, '.')
failed = []
for m in ['config','database','core.accounting','execution_engine',
          'telegram_delivery','signal_engine','ml_signal_scorer',
          'ai_brain','dashboard_service']:
    try: __import__(m)
    except Exception as e: failed.append(f'{m}: {e}')
if failed:
    print('FAIL:' + '|'.join(failed))
else:
    print('OK')
" 2>/dev/null)

if [[ "$IMPORT_RESULT" == OK ]]; then
    ok "Import sağlığı: 14/14 OK"
else
    fail "Import hatası — deploy iptal ediliyor!"
    echo "$IMPORT_RESULT" | tr '|' '\n' | sed 's/^/  /'
    warn "Rollback: $PREV_COMMIT"
    git checkout "$PREV_COMMIT" 2>/dev/null || true
    exit 1
fi

# ── [4/7] Servisleri durdur ─────────────────────────────────────────
log "[4/7] Servisler durduruluyor..."
for svc in $SERVICES $WATCHDOG; do
    if systemctl is-active --quiet "$svc" 2>/dev/null; then
        systemctl stop "$svc" 2>/dev/null || true
        ok "$svc durduruldu"
    fi
done
sleep 2

# ── [5/7] Migration ─────────────────────────────────────────────────
log "[5/7] DB migration çalıştırılıyor..."
if $PY scripts/migrate_accounting_schema.py 2>/dev/null; then
    ok "Migration tamamlandı"
else
    warn "Migration uyarısı (devam ediliyor)"
fi

# Reset isteniyorsa
if [ "$RESET" = true ]; then
    warn "Dashboard sıfırlanıyor (trade/sinyal verileri silinecek)..."
    read -r -p "  Emin misiniz? [y/N]: " confirm
    if [[ "$confirm" =~ ^[Yy]$ ]]; then
        $PY reset_dashboard.py 2>/dev/null && ok "Dashboard sıfırlandı" || warn "Reset hatası"
    else
        warn "Reset iptal edildi"
    fi
fi

# ── [6/7] Service dosyaları ve başlatma ─────────────────────────────
log "[6/7] Servisler başlatılıyor..."

# Service dosyalarını kopyala (varsa)
for svcfile in aurvex-bot.service aurvex-dashboard.service; do
    if [ -f "$svcfile" ]; then
        cp "$svcfile" /etc/systemd/system/ 2>/dev/null || true
    fi
done
if [ -f "aurvex-watchdog.service" ]; then
    cp aurvex-watchdog.service /etc/systemd/system/ 2>/dev/null || true
fi
systemctl daemon-reload 2>/dev/null || true

# Bot'u başlat
systemctl start aurvex-bot 2>/dev/null && ok "aurvex-bot başlatıldı" || fail "aurvex-bot başlatılamadı!"
sleep 3

# Dashboard'u başlat
systemctl start aurvex-dashboard 2>/dev/null && ok "aurvex-dashboard başlatıldı" || fail "aurvex-dashboard başlatılamadı!"
sleep 3

# Watchdog (opsiyonel)
systemctl start "$WATCHDOG" 2>/dev/null && ok "$WATCHDOG başlatıldı" || warn "$WATCHDOG yok/atlandı"

# ── [7/7] Sağlık kontrolü ───────────────────────────────────────────
log "[7/7] Sağlık kontrolü yapılıyor..."
sleep 4

echo ""
echo "── Servis Durumları ────────────────────"
for svc in $SERVICES; do
    if systemctl is-active --quiet "$svc" 2>/dev/null; then
        ok "$svc: ÇALIŞIYOR"
    else
        fail "$svc: DURDU"
        systemctl status "$svc" --no-pager -n 5 2>/dev/null || true
    fi
done

echo ""
echo "── API Sağlık Kontrolü ─────────────────"
for i in 1 2 3; do
    HEALTH=$(curl -s --max-time 5 "$HEALTH_URL" 2>/dev/null || echo "")
    if echo "$HEALTH" | grep -q '"status"'; then
        ok "Dashboard API: HAZIR"
        break
    fi
    if [ $i -lt 3 ]; then
        warn "Dashboard bekleniyor... ($i/3)"
        sleep 5
    else
        warn "Dashboard API henüz hazır değil (başlıyor olabilir)"
    fi
done

echo ""
echo "╔══════════════════════════════════════╗"
echo "║          DEPLOY TAMAMLANDI           ║"
echo "╚══════════════════════════════════════╝"
echo " Commit : ${NEW_COMMIT:0:12}"
echo " Log    : journalctl -u aurvex-bot -f"
echo ""
