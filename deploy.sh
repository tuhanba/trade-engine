#!/bin/bash
# ============================================================
# AURVEX.Ai — Sunucu Deploy & Test Script v3.0
# Kullanım: bash deploy.sh
# ============================================================
set -e

REPO_DIR="/root/trade-engine"
VENV="$REPO_DIR/venv"
PYTHON="$VENV/bin/python3"
PIP="$VENV/bin/pip"

SERVICES=(
  "aurvex-bot.service"
  "aurvex-dashboard.service"
  "aurvex-watchdog.service"
  "aurvex-telegram.service"
)

GREEN='\033[0;32m'
RED='\033[0;31m'
YELLOW='\033[1;33m'
NC='\033[0m'

ok()   { echo -e "${GREEN}[OK]${NC} $1"; }
warn() { echo -e "${YELLOW}[WARN]${NC} $1"; }
err()  { echo -e "${RED}[ERR]${NC} $1"; }

echo "============================================================"
echo " AURVEX.Ai Deploy Script — $(date '+%Y-%m-%d %H:%M:%S')"
echo "============================================================"

# 1. Dizine geç
cd "$REPO_DIR" || { err "Dizin bulunamadı: $REPO_DIR"; exit 1; }
ok "Dizin: $REPO_DIR"

# 2. Servisleri durdur
echo ""
echo "--- Servisler durduruluyor ---"
for svc in "${SERVICES[@]}"; do
  systemctl stop "$svc" 2>/dev/null && ok "Durduruldu: $svc" || warn "Zaten durmuş: $svc"
done
# Eski process'leri de öldür
pkill -f scalp_bot_v3.py 2>/dev/null || true
pkill -f "app.py" 2>/dev/null || true

# 3. Yedek al
echo ""
echo "--- Yedek alınıyor ---"
BACKUP_DIR="$REPO_DIR/backups/$(date '+%Y%m%d_%H%M%S')"
mkdir -p "$BACKUP_DIR"
[ -f "$REPO_DIR/trading.db" ] && cp "$REPO_DIR/trading.db" "$BACKUP_DIR/trading.db" && ok "DB yedeklendi: $BACKUP_DIR/trading.db"
[ -f "$REPO_DIR/.env" ] && cp "$REPO_DIR/.env" "$BACKUP_DIR/.env" && ok ".env yedeklendi"

# 4. Git pull
echo ""
echo "--- Repo güncelleniyor ---"
git pull origin main && ok "Git pull tamamlandı" || warn "Git pull başarısız (manuel kontrol et)"

# 5. Log dizini
echo ""
echo "--- Log dizini hazırlanıyor ---"
mkdir -p "$REPO_DIR/logs"
for log in bot.log dashboard.log telegram.log watchdog.log error.log; do
  touch "$REPO_DIR/logs/$log"
done
ok "Log dosyaları hazır"

# 6. Requirements
echo ""
echo "--- Requirements kuruluyor ---"
if [ -f "$REPO_DIR/requirements.txt" ]; then
  "$PIP" install -r "$REPO_DIR/requirements.txt" -q && ok "Requirements kuruldu"
else
  # Temel paketleri kur
  "$PIP" install flask flask-socketio eventlet python-binance python-dotenv requests ta --quiet 2>/dev/null || true
  warn "requirements.txt bulunamadı, temel paketler kuruldu"
fi

# 7. DB Migration
echo ""
echo "--- DB migration çalıştırılıyor ---"
"$PYTHON" -c "
import sys; sys.path.insert(0, '$REPO_DIR')
from database import init_db
init_db()
print('Migration OK')
" && ok "DB migration tamamlandı" || err "DB migration hatası!"

# 8. Syntax kontrolü
echo ""
echo "--- Syntax kontrolü ---"
"$PYTHON" -m py_compile scalp_bot_v3.py && ok "scalp_bot_v3.py OK"
"$PYTHON" -m py_compile app.py && ok "app.py OK"
"$PYTHON" -m py_compile database.py && ok "database.py OK"
"$PYTHON" -m py_compile execution_engine.py && ok "execution_engine.py OK"
"$PYTHON" -m py_compile telegram_bot.py && ok "telegram_bot.py OK"

# 9. Servis dosyalarını kopyala
echo ""
echo "--- Servis dosyaları yükleniyor ---"
for svc in "${SERVICES[@]}"; do
  if [ -f "$REPO_DIR/$svc" ]; then
    cp "$REPO_DIR/$svc" "/etc/systemd/system/$svc"
    ok "Kopyalandı: $svc"
  else
    warn "Servis dosyası yok: $svc"
  fi
done
systemctl daemon-reload && ok "systemd reload tamamlandı"

# 10. Servisleri başlat
echo ""
echo "--- Servisler başlatılıyor ---"
for svc in "${SERVICES[@]}"; do
  systemctl enable "$svc" 2>/dev/null || true
  systemctl start "$svc" && ok "Başlatıldı: $svc" || err "Başlatılamadı: $svc"
  sleep 2
done

# 11. Sağlık kontrolü
echo ""
echo "--- Sağlık kontrolü (15sn bekleniyor) ---"
sleep 15

for svc in "${SERVICES[@]}"; do
  status=$(systemctl is-active "$svc" 2>/dev/null || echo "unknown")
  if [ "$status" = "active" ]; then
    ok "$svc: active"
  else
    err "$svc: $status"
    echo "  → journalctl -u $svc -n 20"
  fi
done

# 12. API testleri
echo ""
echo "--- API endpoint testleri ---"
BASE="http://localhost:5000"

test_endpoint() {
  local url="$1"
  local label="$2"
  result=$(curl -s -o /dev/null -w "%{http_code}" "$url" --max-time 8 2>/dev/null)
  if [ "$result" = "200" ]; then
    ok "$label → HTTP $result"
  else
    err "$label → HTTP $result"
  fi
}

sleep 5
test_endpoint "$BASE/api/health"        "/api/health"
test_endpoint "$BASE/api/stats"         "/api/stats"
test_endpoint "$BASE/api/live"          "/api/live"
test_endpoint "$BASE/api/scalp_signals" "/api/scalp_signals"
test_endpoint "$BASE/api/watchlist"     "/api/watchlist"
test_endpoint "$BASE/api/last"          "/api/last"
test_endpoint "$BASE/api/risk"          "/api/risk"

# 13. Log kontrol
echo ""
echo "--- Son log satırları ---"
for log in bot.log dashboard.log telegram.log; do
  echo ""
  echo ">>> $log (son 5 satır):"
  tail -5 "$REPO_DIR/logs/$log" 2>/dev/null || echo "(boş)"
done

echo ""
echo "============================================================"
echo " Deploy tamamlandı — $(date '+%Y-%m-%d %H:%M:%S')"
echo "============================================================"
echo ""
echo "Faydalı komutlar:"
echo "  journalctl -u aurvex-bot.service -f"
echo "  journalctl -u aurvex-dashboard.service -f"
echo "  tail -f $REPO_DIR/logs/bot.log"
echo "  tail -f $REPO_DIR/logs/dashboard.log"
echo "  curl http://localhost:5000/api/health | python3 -m json.tool"
