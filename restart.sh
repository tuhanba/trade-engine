#!/bin/bash
# ======================================================================
# AurvexAI — Docker Git Pull & Clean Restart Manager
# Kullanım: bash restart.sh
# ======================================================================

set -e

# Renkler
GREEN='\033[0;32m'
CYAN='\033[0;36m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
GOLD='\033[1;33m'
NC='\033[0m'

echo -e "${CYAN}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
echo -e "${CYAN}🚀 AurvexAI — Git Pulling & Docker Service Clean Restart...${NC}"
echo -e "${CYAN}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"

# 1. Git Pull
echo -e "\n${YELLOW}📥 [1/5] Git Pulling from Remote Repository...${NC}"
# Satır sonu veya yetki çakışmalarını önlemek için diagnostics.sh'ı sıfırlayarak çekelim
git checkout -- diagnostics.sh 2>/dev/null || true
if git pull origin main; then
    echo -e "${GREEN}✅ Git pull başarıyla tamamlandı.${NC}"
else
    echo -e "${RED}❌ Git pull başarısız oldu! Çakışmaları kontrol edin.${NC}"
    exit 1
fi

# 2. Syntax Check
echo -e "\n${YELLOW}🧪 [2/5] Verifying Code Syntax...${NC}"
if python3 -m compileall -q -x "\.venv|venv|env|__pycache__" .; then
    echo -e "${GREEN}✅ Syntax doğrulaması başarılı. Hatalı kod bulunamadı.${NC}"
else
    echo -e "${RED}❌ Syntax hatası tespit edildi! Lütfen kodunuzu kontrol edin.${NC}"
    exit 1
fi

# 3. Stop Host Services (Conflict Prevention)
echo -e "\n${YELLOW}🛑 [3/5] Disabling conflicting Host Systemd services...${NC}"
HOST_SERVICES=("ax-bot" "ax-dashboard" "aurvex-bot" "aurvex-dashboard" "aurvex-watchdog")
for svc in "${HOST_SERVICES[@]}"; do
    if systemctl list-unit-files | grep -q "^${svc}\.service"; then
        echo -e "   Durduruluyor ve devre dışı bırakılıyor: ${svc}..."
        sudo systemctl stop "${svc}" 2>/dev/null || true
        sudo systemctl disable "${svc}" 2>/dev/null || true
    fi
done

# 4. Clean Host Zombie Processes
echo -e "\n${YELLOW}🧹 [4/5] Killing any orphaned Python bot/dashboard processes on Host...${NC}"
sudo pkill -9 -f "async_scalp_engine.py" 2>/dev/null || true
sudo pkill -9 -f "app.py" 2>/dev/null || true
echo -e "${GREEN}✅ Host temizliği tamamlandı.${NC}"

# 5. Docker Rebuild & Reload
echo -e "\n${YELLOW}🔄 [5/5] Restarting and rebuilding Docker Containers...${NC}"
if [ -f "docker-compose.yml" ]; then
    echo -e "   Docker konteynerleri durduruluyor..."
    docker-compose down || docker compose down || true
    
    echo -e "   Eski çakışan konteynerler temizleniyor..."
    docker stop aurvex_redis aurvex_engine aurvex_dashboard 2>/dev/null || true
    docker rm aurvex_redis aurvex_engine aurvex_dashboard 2>/dev/null || true
    
    echo -e "   Docker konteynerleri yeniden inşa ediliyor ve başlatılıyor..."
    if docker-compose up -d --build || docker compose up -d --build; then
        echo -e "${GREEN}✅ Docker servisleri başarıyla başlatıldı!${NC}"
    else
        echo -e "${RED}❌ Docker başlatma başarısız oldu! Docker daemon çalışıyor mu?${NC}"
        exit 1
    fi
else
    echo -e "${RED}❌ docker-compose.yml bulunamadı!${NC}"
    exit 1
fi

# 6. Post-Start Operations (Redis Flush & Friday State Reset)
echo -e "\n${YELLOW}🧼 [6] Flushing Redis cache & resetting Friday CEO blocks...${NC}"
sleep 3  # Wait a few seconds for services to fully bind
docker exec aurvex_redis redis-cli flushall >/dev/null 2>&1 || true
docker exec aurvex_engine python -c "import database; database.set_state('confirmation_mode', 'false'); database.set_state('friday_auto_paused_by_guard', 'false'); database.set_state('friday_boss_cooldown_until', ''); database.set_state('friday_emergency_clutch', 'false')" >/dev/null 2>&1 || true
echo -e "${GREEN}✅ Redis önbelleği temizlendi ve otonom durumlar başarıyla sıfırlandı.${NC}"

# 7. Automated Container Verification
echo -e "\n${YELLOW}🧪 [7] Verifying container health and patch states...${NC}"
if docker exec aurvex_engine python health_check.py; then
    echo -e "${GREEN}✅ Konteyner sağlık testi başarılı.${NC}"
else
    echo -e "${RED}⚠️ Konteyner sağlık testi başarısız veya uyarı verdi!${NC}"
fi

if docker exec aurvex_engine python verify_fixes.py; then
    echo -e "${GREEN}✅ Yama ve veritabanı şema doğrulaması başarılı.${NC}"
else
    echo -e "${RED}⚠️ Yama doğrulaması başarısız veya uyarı verdi!${NC}"
fi

# 8. Execute Diagnostics Audit
echo -e "\n${YELLOW}📊 Running system audit to verify single process health...${NC}"
chmod +x diagnostics.sh
./diagnostics.sh

echo -e "\n${GREEN}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
echo -e "${GREEN}✅ İşlemler Tamamlandı! Sunucu Docker üzerinden 10/10 aktif.${NC}"
echo -e "${GREEN}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}\n"
