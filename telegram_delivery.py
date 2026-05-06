"""
telegram_delivery.py — AX Telegram Raporlama v4.7
=================================================
Aşama 7: History + Telegram Raporlama Standardizasyonu.
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

def send_trade_open(data):
    dir_emoji = "📈 LONG" if data['direction'] == 'LONG' else "📉 SHORT"
    conf_pct = int(data.get('confidence', 0.8) * 100)
    conf_bar = "🟢" * (conf_pct // 20) + "⚪" * (5 - conf_pct // 20)
    
    msg = (
        f"💎 <b>AX ELITE MASTER SİNYAL</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"🪙 <b>{data['symbol']}</b> | {dir_emoji}\n"
        f"📊 Kalite: <b>{data.get('setup_quality', 'A')}</b> | Skor: <b>{data.get('final_score', 0):.1f}</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"📌 Giriş: <code>{data['entry']:.6f}</code>\n"
        f"🛑 Stop:  <code>{data['sl']:.6f}</code>\n"
        f"🎯 TP1:   <code>{data['tp1']:.6f}</code>\n"
        f"🎯 TP2:   <code>{data['tp2']:.6f}</code>\n"
        f"⚖️ RR:    <b>{data.get('rr', 1.5):.2f}R</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"💰 Risk: <code>{data.get('risk_usd', 0):.2f} USD</code> (%{data.get('risk_pct', 1):.1f})\n"
        f"⚙️ Kaldıraç: <b>x{data.get('leverage', 10)}</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"🧠 <b>AI ANALİZİ:</b>\n"
        f"├ Güven: {conf_bar} %{conf_pct}\n"
        f"└ Neden: <i>{data.get('reason', 'Strateji onaylandı.')}</i>\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"⏰ {datetime.now(timezone.utc).strftime('%H:%M UTC')}"
    )
    _queue.push(msg)

def send_tp_hit(symbol, level, pnl, remaining_qty):
    msg = (
        f"🎯 <b>TP{level} HEDEFİNE ULAŞILDI!</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"🪙 {symbol}\n"
        f"💰 Kar: <code>+{pnl:.2f} USD</code>\n"
        f"📦 Kalan: <code>{remaining_qty:.4f} Qty</code>\n"
        f"🛡️ SL Breakeven'a çekildi."
    )
    _queue.push(msg)

def send_trade_close(symbol, pnl, fee, reason, duration_str):
    result_emoji = "✅" if pnl > 0 else "❌"
    msg = (
        f"{result_emoji} <b>İŞLEM KAPANDI</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"🪙 <b>{symbol}</b>\n"
        f"🏁 Neden: <b>{reason}</b>\n"
        f"💰 Net PnL: <code>{pnl:.2f} USD</code> (Fee: {fee:.2f})\n"
        f"⏱️ Süre: <b>{duration_str}</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━"
    )
    _queue.push(msg)

def send_message(text):
    _queue.push(text)

def deliver_signal(sig):
    # Geriye dönük uyumluluk için
    send_trade_open(sig.__dict__ if hasattr(sig, '__dict__') else sig)
