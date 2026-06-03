#!/bin/bash
# ======================================================================
# Aurvex AI Watchdog Failover & Auto-Restart Script
# ======================================================================
# Runs as a cron job to monitor systemd service units.
# Auto-restarts failed services and dispatches Telegram notifications.

# Sourcing Environment Variables
ENV_FILE="/root/trade-engine-main/.env"
if [ ! -f "$ENV_FILE" ]; then
    ENV_FILE="$(dirname "$0")/../.env"
fi

if [ -f "$ENV_FILE" ]; then
    export $(grep -v '^#' "$ENV_FILE" | xargs)
fi

# Configuration
BOT_TOKEN="${TELEGRAM_BOT_TOKEN}"
CHAT_ID="${TELEGRAM_CHAT_ID}"
SERVICES=("ax-bot" "ax-dashboard")
HOSTNAME=$(hostname)
DATE=$(date "+%Y-%m-%d %H:%M:%S")

send_telegram_alert() {
    local text="$1"
    if [ -n "$BOT_TOKEN" ] && [ -n "$CHAT_ID" ]; then
        curl -s -X POST "https://api.telegram.org/bot${BOT_TOKEN}/sendMessage" \
            -d "chat_id=${CHAT_ID}" \
            -d "text=${text}" \
            -d "parse_mode=HTML" > /dev/null
    fi
}

for SVC in "${SERVICES[@]}"; do
    if systemctl list-unit-files | grep -q "^${SVC}\.service"; then
        STATUS=$(systemctl is-active "$SVC")
        if [ "$STATUS" != "active" ]; then
            echo "[${DATE}] CRITICAL: Service ${SVC} is ${STATUS}. Attempting auto-restart..."
            
            # Send Telegram alert *before* restart attempt
            send_telegram_alert "🚨 <b>SUNUCU KRİTİK UYARI</b> [${HOSTNAME}]%0A%0A<code>${SVC}</code> servisi çökmüş (Status: <b>${STATUS}</b>)!%0AOtomatik kurtarma başlatılıyor... 🔄"
            
            # Restart
            systemctl restart "$SVC"
            sleep 3
            
            # Check status again
            NEW_STATUS=$(systemctl is-active "$SVC")
            if [ "$NEW_STATUS" = "active" ]; then
                echo "[${DATE}] SUCCESS: Service ${SVC} is running again."
                send_telegram_alert "🟢 <b>KURTARMA BAŞARILI</b> [${HOSTNAME}]%0A%0A<code>${SVC}</code> servisi başarıyla yeniden başlatıldı ve şu an aktif! ✅"
            else
                echo "[${DATE}] FAILED: Service ${SVC} failed to restart."
                send_telegram_alert "🚨 <b>KURTARMA BAŞARISIZ!</b> [${HOSTNAME}]%0A%0A<code>${SVC}</code> servisi yeniden başlatılamadı! Lütfen sunucuyu manuel olarak inceleyin. ❌"
            fi
        fi
    fi
done
