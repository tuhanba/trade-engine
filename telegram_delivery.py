"""
Telegram Delivery — AX Scalp Engine v4.0 (ULTIMATE ELITE)
=========================================================
Sinyal formatı: Ultra-detaylı, AI destekli, profesyonel.
"""
import os
import time
import logging
import threading
import requests
from collections import deque
from datetime import datetime, timezone
from config import TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID

logger = logging.getLogger(__name__)

def _token():
    return os.getenv("TELEGRAM_BOT_TOKEN", TELEGRAM_BOT_TOKEN)

def _chat():
    return os.getenv("TELEGRAM_CHAT_ID", TELEGRAM_CHAT_ID)

def _send_raw(text, parse_mode="HTML", retries=3):
    token = _token()
    chat  = _chat()
    if not token or not chat: return False
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    for _ in range(retries):
        try:
            resp = requests.post(url, json={"chat_id": chat, "text": text[:4096], "parse_mode": parse_mode}, timeout=10)
            if resp.status_code == 200: return True
        except: pass
        time.sleep(1)
    return False

class _Queue:
    def __init__(self):
        self._q = deque()
        self._lock = threading.Lock()
        self._event = threading.Event()
        self._thread = threading.Thread(target=self._worker, daemon=True)
        self._thread.start()
    def push(self, text):
        with self._lock: self._q.append(text)
        self._event.set()
    def _worker(self):
        while True:
            self._event.wait(); self._event.clear()
            while True:
                with self._lock:
                    if not self._q: break
                    text = self._q.popleft()
                _send_raw(text)
                time.sleep(0.5)

_queue = _Queue()

def format_signal(sig):
    dir_emoji = "📈 LONG" if sig.direction == "LONG" else "📉 SHORT"
    conf_pct = int((sig.confidence or 0.8) * 100)
    conf_bar = "🟢" * (conf_pct // 20) + "⚪" * (5 - conf_pct // 20)
    
    return (
        f"💎 <b>AX ELITE MASTER SİNYAL</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"🪙 <b>{sig.symbol}</b> | {dir_emoji}\n"
        f"📊 Kalite: <b>{sig.setup_quality}</b> | Skor: <b>{sig.coin_score}</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"📌 Giriş:  <code>{sig.entry_zone:.6f}</code>\n"
        f"🛑 Stop:   <code>{sig.stop_loss:.6f}</code>\n"
        f"🎯 Hedef:  <code>{sig.tp1:.6f}</code>\n"
        f"⚖️ RR:     <b>{sig.rr:.2f}R</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"🧠 <b>AI ANALİZİ:</b>\n"
        f"├ Güven: {conf_bar} %{conf_pct}\n"
        f"└ Neden: <i>{sig.reason}</i>\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"⏰ {datetime.now(timezone.utc).strftime('%H:%M UTC')}"
    )

def deliver_signal(sig):
    _queue.push(format_signal(sig))

def send_message(text):
    _queue.push(text)
