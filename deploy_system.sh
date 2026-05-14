#!/bin/bash

# AURVEX.Ai Production Deploy System
# ==================================

PROJECT_DIR="/root/trade_engine"
BACKUP_DIR="/root/trade_engine_backups"
TIMESTAMP=$(date +%Y%m%d_%H%M%S)

# Renkler
GREEN='\033[0;32m'
RED='\033[0;31m'
YELLOW='\033[1;33m'
NC='\033[0m'

log() { echo -e "${GREEN}[$(date +'%Y-%m-%d %H:%M:%S')] $1${NC}"; }
warn() { echo -e "${YELLOW}[$(date +'%Y-%m-%d %H:%M:%S')] WARN: $1${NC}"; }
error() { echo -e "${RED}[$(date +'%Y-%m-%d %H:%M:%S')] ERROR: $1${NC}"; }

# 1. Backup Script
create_backup() {
    log "Yedekleme başlatılıyor..."
    mkdir -p $BACKUP_DIR
    tar -czf "$BACKUP_DIR/backup_$TIMESTAMP.tar.gz" -C $PROJECT_DIR . --exclude="venv" --exclude="logs" --exclude="*.db-journal"
    log "Yedek oluşturuldu: backup_$TIMESTAMP.tar.gz"
}

# 2. Health Check Script
check_health() {
    log "Sistem sağlık kontrolü yapılıyor..."
    
    # Servis kontrolleri
    for service in aurvex-bot aurvex-dashboard aurvex-watchdog; do
        if systemctl is-active --quiet $service; then
            log "$service: ÇALIŞIYOR"
        else
            error "$service: DURMUŞ!"
            return 1
        fi
    done

    # Port kontrolü (Dashboard)
    if curl -s --head  --request GET http://localhost:5000 | grep "200 OK" > /dev/null; then
        log "Dashboard API: ERİŞİLEBİLİR"
    else
        warn "Dashboard API: ERİŞİLEMİYOR (Port 5000)"
    fi

    return 0
}

# 3. Cleanup Script
cleanup_stale() {
    log "Eski süreçler temizleniyor..."
    # Yetim kalmış python süreçlerini temizle (dikkatli kullan)
    # pkill -f "scalp_bot.py" || true
    # pkill -f "app.py" || true
    
    # Eski logları temizle (30 günden eski)
    find $PROJECT_DIR/logs -name "*.log" -mtime +30 -delete
    log "Temizlik tamamlandı."
}

# 4. Atomic Deploy
deploy() {
    log "Atomik Deploy başlatılıyor..."
    
    create_backup
    
    log "Yeni kodlar çekiliyor..."
    git fetch origin
    git reset --hard origin/main
    
    log "Bağımlılıklar güncelleniyor..."
    source venv/bin/activate
    pip install -r requirements.txt
    
    log "Servisler yeniden başlatılıyor..."
    systemctl daemon-reload
    systemctl restart aurvex-dashboard
    sleep 2
    systemctl restart aurvex-bot
    sleep 2
    systemctl restart aurvex-watchdog
    
    if check_health; then
        log "DEPLOY BAŞARILI!"
    else
        error "DEPLOY HATALI! Rollback başlatılıyor..."
        rollback
    fi
}

# 5. Rollback Script
rollback() {
    log "Rollback işlemi başlatılıyor..."
    LATEST_BACKUP=$(ls -t $BACKUP_DIR/backup_*.tar.gz | head -n 1)
    if [ -z "$LATEST_BACKUP" ]; then
        error "Yedek bulunamadı! Manuel müdahale gerekli."
        exit 1
    fi
    
    log "Geri yükleniyor: $LATEST_BACKUP"
    tar -xzf "$LATEST_BACKUP" -C $PROJECT_DIR
    
    systemctl restart aurvex-dashboard aurvex-bot aurvex-watchdog
    log "ROLLBACK TAMAMLANDI."
}

# Ana Menü
case "$1" in
    deploy) deploy ;;
    rollback) rollback ;;
    backup) create_backup ;;
    health) check_health ;;
    cleanup) cleanup_stale ;;
    *) echo "Kullanım: $0 {deploy|rollback|backup|health|cleanup}" ;;
esac
