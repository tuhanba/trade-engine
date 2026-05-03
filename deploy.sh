#!/bin/bash
# AX Scalp Engine - Modernize Safe Deploy Script v2.1
# ===================================================
set -e

echo "🚀 AX Scalp Engine Güvenli Güncelleme Başlatılıyor..."

# Klasör yolunu otomatik tespit et
PROJECT_DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )"
cd "$PROJECT_DIR"
echo "📂 Çalışma dizini: $PROJECT_DIR"

# 1. Kodları Güncelle
echo "[1/5] 📥 Kodlar GitHub'dan çekiliyor..."
git pull origin main

# 2. Bağımlılıkları Kontrol Et
echo "[2/5] 📦 Kütüphaneler kontrol ediliyor..."
if [ -d "venv" ]; then
    venv/bin/pip install aiohttp motor psycopg2-binary python-binance ta python-dotenv psutil --break-system-packages > /dev/null 2>&1 || true
else
    pip install aiohttp motor psycopg2-binary python-binance ta python-dotenv psutil --break-system-packages > /dev/null 2>&1 || true
fi

# 3. Syntax ve Modül Testi (Safety Guard)
echo "[3/5] 🔍 Güvenlik kontrolü yapılıyor..."
PYTHON_CMD="python3"
[ -d "venv" ] && PYTHON_CMD="venv/bin/python3"

$PYTHON_CMD -c "
import sys
try:
    import scalp_bot_v3
    from core.async_market_scanner import AsyncMarketScanner
    from core.advanced_trend_engine import AdvancedTrendEngine
    from core.advanced_risk_engine import AdvancedRiskEngine
    from database import init_db
    init_db()
    print('✅ Kod yapısı ve veritabanı mükemmel (10/10).')
except Exception as e:
    print(f'❌ KRİTİK HATA TESPİT EDİLDİ: {e}')
    sys.exit(1)
"

# 4. Servisleri Yeniden Başlat
echo "[4/5] 🔄 Servisler yeniden başlatılıyor..."
# Botu durdur ve yeniden başlat
pkill -f scalp_bot_v3.py || true
nohup $PYTHON_CMD scalp_bot_v3.py > bot_v3.log 2>&1 &

# 5. Durum Kontrolü
echo "[5/5] ✨ Durum kontrolü yapılıyor..."
sleep 3
if pgrep -f scalp_bot_v3.py > /dev/null; then
    echo "✅ SİSTEM AKTİF VE ÇALIŞIYOR!"
else
    echo "⚠️ UYARI: Bot başlatılamadı! Lütfen bot_v3.log dosyasını kontrol edin."
fi

echo "=== Güncelleme Tamamlandı ==="
echo "📊 Logları izlemek için: tail -f bot_v3.log"
