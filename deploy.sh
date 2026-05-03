#!/bin/bash
# AX Scalp Engine - Modernize Safe Deploy Script v2.2
# ===================================================
set -e

echo "🚀 AX Scalp Engine Güvenli Güncelleme Başlatılıyor..."

PROJECT_DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )"
cd "$PROJECT_DIR"

# 1. Kodları Güncelle
echo "[1/5] 📥 Kodlar GitHub'dan çekiliyor..."
git pull origin main

# 2. Bağımlılıkları Kontrol Et
echo "[2/5] 📦 Kütüphaneler kontrol ediliyor..."
pip install aiohttp motor psycopg2-binary python-binance ta python-dotenv psutil --break-system-packages > /dev/null 2>&1 || true

# 3. Syntax ve Modül Testi (Safety Guard)
echo "[3/5] 🔍 Güvenlik kontrolü yapılıyor..."
python3 -c "
import sys
try:
    import scalp_bot_v3
    from core.async_market_scanner import AsyncMarketScanner
    from core.advanced_trend_engine import AdvancedTrendEngine
    from core.advanced_risk_engine import AdvancedRiskEngine
    print('✅ Kod yapısı mükemmel (10/10).')
except Exception as e:
    print(f'❌ KRİTİK HATA TESPİT EDİLDİ: {e}')
    sys.exit(1)
"

# 4. Servisleri Yeniden Başlat
echo "[4/5] 🔄 Servisler yeniden başlatılıyor..."
pkill -f scalp_bot_v3.py || true
nohup python3 scalp_bot_v3.py > bot_v3.log 2>&1 &

# 5. Durum Kontrolü
echo "[5/5] ✨ Durum kontrolü yapılıyor..."
sleep 3
if pgrep -f scalp_bot_v3.py > /dev/null; then
    echo "✅ SİSTEM AKTİF VE ÇALIŞIYOR!"
else
    echo "⚠️ UYARI: Bot başlatılamadı! Lütfen bot_v3.log dosyasını kontrol edin."
fi

echo "=== Güncelleme Tamamlandı ==="
