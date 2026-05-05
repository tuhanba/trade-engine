"""
watchdog.py — AURVEX.Ai Servis Watchdog v1.0
=============================================
Bot ve Dashboard servislerini izler, çökmede yeniden başlatır.
Telegram'a kritik uyarı gönderir.
"""
import os
import time
import logging
import subprocess
import requests
from datetime import datetime, timezone

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [WATCHDOG] %(levelname)s %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
)
logger = logging.getLogger(__name__)

try:
    from config import TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID
except ImportError:
    TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
    TELEGRAM_CHAT_ID   = os.getenv("TELEGRAM_CHAT_ID", "")

SERVICES = ["aurvex-bot.service", "aurvex-dashboard.service"]
CHECK_INTERVAL = 60   # saniye
DASHBOARD_URL  = "http://localhost:5000/api/health"
MAX_RESTART_ATTEMPTS = 3
restart_counts: dict = {s: 0 for s in SERVICES}


def _tg(msg: str):
    """Telegram'a mesaj gönder."""
    if not TELEGRAM_BOT_TOKEN or not TELEGRAM_CHAT_ID:
        return
    try:
        requests.post(
            f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage",
            json={"chat_id": TELEGRAM_CHAT_ID, "text": f"🔧 WATCHDOG: {msg}"},
            timeout=8,
        )
    except Exception:
        pass


def _is_active(service: str) -> bool:
    """systemctl is-active ile servis durumunu kontrol et."""
    try:
        result = subprocess.run(
            ["systemctl", "is-active", service],
            capture_output=True, text=True, timeout=10
        )
        return result.stdout.strip() == "active"
    except Exception:
        return False


def _restart(service: str):
    """Servisi yeniden başlat."""
    try:
        subprocess.run(["systemctl", "restart", service], timeout=30)
        logger.info(f"Restart gönderildi: {service}")
    except Exception as e:
        logger.error(f"Restart hatası {service}: {e}")


def _dashboard_healthy() -> bool:
    """Dashboard HTTP health check."""
    try:
        r = requests.get(DASHBOARD_URL, timeout=10)
        return r.status_code == 200 and r.json().get("ok", False)
    except Exception:
        return False


def run():
    logger.info("Watchdog başladı.")
    _tg("Watchdog başladı — servisler izleniyor.")
    while True:
        for service in SERVICES:
            active = _is_active(service)
            if not active:
                restart_counts[service] += 1
                logger.warning(f"{service} aktif değil! Restart #{restart_counts[service]}")
                if restart_counts[service] <= MAX_RESTART_ATTEMPTS:
                    _restart(service)
                    _tg(f"⚠️ {service} çöktü, yeniden başlatıldı (#{restart_counts[service]})")
                else:
                    logger.error(f"{service} {MAX_RESTART_ATTEMPTS} kez restart edildi, manuel müdahale gerekli!")
                    _tg(f"🚨 {service} {MAX_RESTART_ATTEMPTS} kez restart edildi! Manuel müdahale gerekli.")
            else:
                if restart_counts[service] > 0:
                    logger.info(f"{service} tekrar aktif.")
                    restart_counts[service] = 0

        # Dashboard HTTP kontrolü
        if not _dashboard_healthy():
            logger.warning("Dashboard HTTP health check başarısız!")
        else:
            logger.debug("Tüm servisler sağlıklı.")

        time.sleep(CHECK_INTERVAL)


if __name__ == "__main__":
    run()
