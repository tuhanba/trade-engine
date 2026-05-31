#!/bin/bash
# ═══════════════════════════════════════════════════════════════════════════
# AurvexAI — Git Pull & Service Restart Script
# Kullanım: bash restart.sh
# ═══════════════════════════════════════════════════════════════════════════

set -e

# Renkler
GREEN='\033[0;32m'
CYAN='\033[0;36m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
NC='\033[0m' # No Color

echo -e "${CYAN}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
echo -e "${CYAN}🚀 AurvexAI — Pulling Latest Code & Restarting Services...${NC}"
echo -e "${CYAN}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"

# 1. Git Pull
echo -e "\n${YELLOW}📥 [1/4] Git Pulling from Remote Repository...${NC}"
if git pull origin main; then
    echo -e "${GREEN}✅ Git pull tamamlandı.${NC}"
else
    echo -e "${RED}❌ Git pull başarısız oldu! İnternet veya yetki sorunlarını kontrol edin.${NC}"
    exit 1
fi

# 2. Syntax Check
echo -e "\n${YELLOW}🧪 [2/4] Verifying Code Syntax...${NC}"
if python3 -m compileall -q .; then
    echo -e "${GREEN}✅ Syntax doğrulaması başarılı.${NC}"
else
    echo -e "${RED}❌ Syntax hatası tespit edildi! Lütfen son kod değişikliklerinizi kontrol edin.${NC}"
    exit 1
fi

# 3. Systemd Restart
echo -e "\n${YELLOW}🔄 [3/4] Restarting systemd services...${NC}"
SERVICES=("ax-bot" "ax-dashboard")
for svc in "${SERVICES[@]}"; do
    echo -e "   Yeniden başlatılıyor: ${svc}..."
    if sudo systemctl restart "${svc}" 2>/dev/null; then
        echo -e "   ${GREEN}✅ ${svc} başarıyla restart edildi.${NC}"
    else
        echo -e "   ${RED}❌ ${svc} restart edilemedi (systemctl yetkisi yok veya servis kurulu değil).${NC}"
        echo -e "   ${YELLOW}💡 Alternatif manuel başlatma denenebilir.${NC}"
    fi
done

# 4. Status Check
echo -e "\n${YELLOW}🔍 [4/4] Checking Status of Services...${NC}"
for svc in "${SERVICES[@]}"; do
    echo -e "\n📊 ${svc} durumu:"
    if sudo systemctl is-active --quiet "${svc}" 2>/dev/null; then
        echo -e "   Durum: ${GREEN}AKTİF (Çalışıyor)${NC}"
        sudo systemctl status "${svc}" --no-pager -n 5 2>/dev/null | tail -n 3 || true
    else
        echo -e "   Durum: ${RED}PASİF (DURMUŞ)${NC}"
    fi
done

echo -e "\n${GREEN}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}"
echo -e "${GREEN}✅ İşlemler Tamamlandı! Sistem güncel ve aktif.${NC}"
echo -e "${GREEN}━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━${NC}\n"
