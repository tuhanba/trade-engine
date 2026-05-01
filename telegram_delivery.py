"""
Telegram Delivery — Data Layer'dan beslenip sinyal gönderir.
Sadece A+, A ve seçilmiş B sinyalleri gönderilir.
Duplicate mesaj engeli, retry mekanizması ve hata izolasyonu mevcuttur.
"""
import os
import time
import logging
import threading
import requests
from collections import deque
from config import TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID

logger = logging.getLogger(__name__)

# ─────────────────────────────────────────────────────────────────────────────
# DÜŞÜK SEVİYE GÖNDERICI
# ─────────────────────────────────────────────────────────────────────────────
def _token() -> str:
    return os.getenv("TELEGRAM_BOT_TOKEN", TELEGRAM_BOT_TOKEN)

def _chat() -> str:
    return os.getenv("TELEGRAM_CHAT_ID", TELEGRAM_CHAT_ID)

def _send_raw(text: str, parse_mode: str = "HTML", retries: int = 3) -> bool:
    token = _token()
    chat  = _chat()
    if not token or not chat:
        return False
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    for attempt in range(1, retries + 1):
        try:
            resp = requests.post(url, json={
                "chat_id": chat, "text": text[:4096], "parse_mode": parse_mode,
            }, timeout=8)
            if resp.status_code == 200:
                return True
            if resp.status_code == 429:
                wait = resp.json().get("parameters", {}).get("retry_after", 5)
                time.sleep(wait)
                continue
        except Exception as e:
            logger.debug(f"Telegram gönderim hatası: {e}")
        if attempt < retries:
            time.sleep(2 ** attempt)
    return False

# ─────────────────────────────────────────────────────────────────────────────
# KUYRUK
# ─────────────────────────────────────────────────────────────────────────────
class _Queue:
    def __init__(self):
        self._q      = deque()
        self._lock   = threading.Lock()
        self._event  = threading.Event()
        self._thread = threading.Thread(target=self._worker, daemon=True)
        self._thread.start()

    def push(self, text: str, parse_mode: str = "HTML"):
        with self._lock:
            self._q.append((text, parse_mode))
        self._event.set()

    def _worker(self):
        while True:
            self._event.wait()
            self._event.clear()
            while True:
                with self._lock:
                    if not self._q:
                        break
                    item = self._q.popleft()
                _send_raw(*item)
                time.sleep(0.5)

_queue = _Queue()

# ─────────────────────────────────────────────────────────────────────────────
# SINYAL FORMATLAYICI
# ─────────────────────────────────────────────────────────────────────────────
def format_signal(sig) -> str:
    """SignalData nesnesini Telegram mesajına dönüştür."""
    quality = sig.setup_quality

    if quality in ["A+", "A"]:
        header = f"🔥 <b>SCALP SETUP — {quality}</b>"
    else:
        header = f"⚠️ <b>AGGRESSIVE SCALP — B QUALITY</b>"

    msg = f"""{header}

Coin: <b>{sig.symbol}</b>
Direction: <b>{sig.direction}</b>
Entry: {sig.entry_zone}
Stop: {sig.stop_loss}
TP1: {sig.tp1}
TP2: {sig.tp2}
TP3: {sig.tp3}
RR: {sig.rr}
Risk: {sig.risk_percent}%
Leverage: {sig.leverage_suggestion}x
Confidence: {int(sig.confidence * 100)}%
Reason: {sig.reason}
Status: {sig.status}"""

    if quality == "B":
        msg += "\n<i>⚠️ Low / Half Size — Optional</i>"

    return msg

# ─────────────────────────────────────────────────────────────────────────────
# ANA TESLİMAT FONKSİYONU
# ─────────────────────────────────────────────────────────────────────────────
_sent_ids = set()

def deliver_signal(sig) -> bool:
    """
    Data Layer'dan gelen sinyali Telegram'a gönderir.
    - Sadece A+, A ve B kalitesindeki sinyaller gönderilir.
    - Duplicate engeli: aynı ID iki kere gönderilmez.
    - Sistem çökmez; hata loglanır.
    """
    try:
        if sig.id in _sent_ids:
            logger.debug(f"Duplicate sinyal engellendi: {sig.symbol}")
            return False

        if sig.setup_quality not in ["A+", "A", "B"]:
            return False

        if not sig.is_valid():
            return False

        msg = format_signal(sig)
        _queue.push(msg)
        _sent_ids.add(sig.id)
        sig.telegram_status = "sent"
        logger.info(f"Telegram gönderildi: {sig.symbol} {sig.direction} {sig.setup_quality}")
        return True

    except Exception as e:
        logger.error(f"Telegram delivery hatası: {e}")
        sig.telegram_status = "error"
        return False

def send_message(text: str):
    """Genel mesaj gönder (sistem bildirimleri için)."""
    try:
        _queue.push(text)
    except Exception as e:
        logger.error(f"Telegram mesaj hatası: {e}")
