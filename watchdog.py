"""
AURVEX.Ai Watchdog — Servis Sağlık Kontrolü
============================================
Her 60 saniyede bir bot ve dashboard'u kontrol eder.
Çökerse otomatik yeniden başlatır ve Telegram'a bildirir.
"""
import os
import time
import logging
import subprocess
import requests
from datetime import datetime, timezone

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | watchdog | %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger(__name__)

SERVICES = ["aurvex-bot", "aurvex-dashboard"]
HEALTH_URL = "http://127.0.0.1:5000/api/health"
CHECK_INTERVAL = 60  # saniye
MAX_RESTART_ATTEMPTS = 3
restart_counts = {s: 0 for s in SERVICES}
last_restart_time = {s: 0 for s in SERVICES}


def _send_telegram(text: str):
    try:
        from config import TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID
        token = os.getenv("TELEGRAM_BOT_TOKEN", TELEGRAM_BOT_TOKEN)
        chat  = os.getenv("TELEGRAM_CHAT_ID", TELEGRAM_CHAT_ID)
        if not token or not chat:
            return
        requests.post(
            f"https://api.telegram.org/bot{token}/sendMessage",
            json={"chat_id": chat, "text": text, "parse_mode": "HTML"},
            timeout=8,
        )
    except Exception as e:
        logger.debug(f"Telegram bildirim hatası: {e}")


def is_service_active(service: str) -> bool:
    try:
        result = subprocess.run(
            ["systemctl", "is-active", service],
            capture_output=True, text=True, timeout=5,
        )
        return result.stdout.strip() == "active"
    except Exception:
        return False


def restart_service(service: str) -> bool:
    now = time.time()
    # 5 dakika içinde 3'ten fazla restart engelle
    if now - last_restart_time[service] < 300:
        restart_counts[service] += 1
    else:
        restart_counts[service] = 1
        last_restart_time[service] = now

    if restart_counts[service] > MAX_RESTART_ATTEMPTS:
        logger.error(f"{service} çok fazla restart denemesi — manuel müdahale gerekli!")
        _send_telegram(
            f"🚨 <b>WATCHDOG ALARM</b>\n"
            f"{service} {MAX_RESTART_ATTEMPTS}x restart denendi — durdu!\n"
            f"⏰ {datetime.now(timezone.utc).strftime('%H:%M UTC')}"
        )
        return False

    try:
        subprocess.run(["systemctl", "restart", service], timeout=15, check=True)
        logger.info(f"{service} yeniden başlatıldı (deneme #{restart_counts[service]})")
        _send_telegram(
            f"⚠️ <b>WATCHDOG</b>: {service} yeniden başlatıldı\n"
            f"Deneme: #{restart_counts[service]}\n"
            f"⏰ {datetime.now(timezone.utc).strftime('%H:%M UTC')}"
        )
        return True
    except Exception as e:
        logger.error(f"{service} restart hatası: {e}")
        return False


def check_dashboard_health() -> bool:
    try:
        resp = requests.get(HEALTH_URL, timeout=5)
        return resp.status_code == 200 and resp.json().get("ok", False)
    except Exception:
        return False


def main():
    logger.info("AURVEX.Ai Watchdog başlatıldı.")
    _send_telegram(
        f"🛡️ <b>Watchdog Aktif</b>\n"
        f"Servisler izleniyor: {', '.join(SERVICES)}\n"
        f"⏰ {datetime.now(timezone.utc).strftime('%H:%M UTC')}"
    )

    while True:
        try:
            for service in SERVICES:
                if not is_service_active(service):
                    logger.warning(f"{service} çalışmıyor — yeniden başlatılıyor...")
                    restart_service(service)
                else:
                    # Servis aktif ama reset_counts'u sıfırla (5dk geçtiyse)
                    if time.time() - last_restart_time[service] > 300:
                        restart_counts[service] = 0

            # Dashboard HTTP health check
            if is_service_active("aurvex-dashboard"):
                if not check_dashboard_health():
                    logger.warning("Dashboard HTTP yanıt vermiyor — yeniden başlatılıyor...")
                    restart_service("aurvex-dashboard")

        except Exception as e:
            logger.error(f"Watchdog döngü hatası: {e}")

        time.sleep(CHECK_INTERVAL)


if __name__ == "__main__":
    main()
