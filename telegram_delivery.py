"""
Telegram Delivery — AX Scalp Engine v3.1 (ELITE)
=========================================
Sinyal formatı: profesyonel, emoji'li, okunabilir.
"""
import os
import time
import logging
import threading
import requests
from collections import deque
from datetime import datetime, timezone
from config import TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID, TELEGRAM_THRESHOLD

logger = logging.getLogger(__name__)

def _token():
    return os.getenv("TELEGRAM_BOT_TOKEN", TELEGRAM_BOT_TOKEN)

def _chat():
    return os.getenv("TELEGRAM_CHAT_ID", TELEGRAM_CHAT_ID)

def _send_raw(text, parse_mode="HTML", retries=3):
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
        except Exception as e:
            logger.debug(f"Telegram gönderim hatası: {e}")
        time.sleep(1)
    return False

class _Queue:
    def __init__(self):
        self._q      = deque()
        self._lock   = threading.Lock()
        self._event  = threading.Event()
        self._thread = threading.Thread(target=self._worker, daemon=True)
        self._thread.start()
    def push(self, text, parse_mode="HTML"):
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
                    text, pm = self._q.popleft()
                _send_raw(text, pm)
                time.sleep(0.5)

_queue = _Queue()

def _fmt(val, decimals=4):
    try:
        return f"{float(val):.{decimals}f}"
    except Exception:
        return str(val)

def format_signal(sig):
    dir_emoji = "📈 LONG" if sig.direction == "LONG" else "📉 SHORT"
    now_utc   = datetime.now(timezone.utc).strftime("%H:%M UTC")
    return (
        f"🚀 <b>AX ELITE SİNYAL</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"🪙 <b>{sig.symbol}</b>  {dir_emoji}\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"📌 Entry:  <code>{_fmt(sig.entry_zone)}</code>\n"
        f"🛑 Stop:   <code>{_fmt(sig.stop_loss)}</code>\n"
        f"🎯 TP1:    <code>{_fmt(sig.tp1)}</code>\n"
        f"📊 Kalite: <b>{sig.setup_quality}</b>\n"
        f"⚖️ RR:     <b>{_fmt(sig.rr, 2)}R</b>\n"
        f"⏰ {now_utc}\n"
    )

def deliver_signal(sig):
    try:
        msg = format_signal(sig)
        _queue.push(msg)
        return True
    except Exception as e:
        logger.error(f"Telegram delivery hatası: {e}")
        return False

def send_trade_open(trade):
    try:
        msg = f"✅ <b>TRADE AÇILDI: {trade.get('symbol')}</b>"
        _queue.push(msg)
    except Exception as e:
        logger.error(f"Trade open bildirim hatası: {e}")

def send_message(text):
    _queue.push(text)
