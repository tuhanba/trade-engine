"""
Telegram Delivery — AX Scalp Engine v4.2 (ULTIMATE ELITE)
=========================================================
Sinyal formati: Ultra-detayli, AI destekli, profesyonel.
TP1/TP2/TP3 gosterimi, duplicate engeli, lifecycle mesajlari.
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

# Duplicate mesaj engeli (son 200 mesajin hash'i)
_sent_hashes = set()
_sent_lock = threading.Lock()

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
    for _ in range(retries):
        try:
            resp = requests.post(
                url,
                json={"chat_id": chat, "text": text[:4096], "parse_mode": parse_mode},
                timeout=10
            )
            if resp.status_code == 200:
                return True
        except:
            pass
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
        # Duplicate engeli
        msg_hash = hash(text[:200])
        with _sent_lock:
            if msg_hash in _sent_hashes:
                logger.debug("[Telegram] Duplicate mesaj engellendi")
                return
            _sent_hashes.add(msg_hash)
            if len(_sent_hashes) > 200:
                _sent_hashes.clear()
        with self._lock:
            self._q.append(text)
        self._event.set()

    def _worker(self):
        while True:
            self._event.wait()
            self._event.clear()
            while True:
                with self._lock:
                    if not self._q:
                        break
                    text = self._q.popleft()
                _send_raw(text)
                time.sleep(0.5)

_queue = _Queue()

def format_signal(sig):
    dir_emoji = "📈 LONG" if sig.direction == "LONG" else "📉 SHORT"
    conf_pct = int((sig.confidence or 0.8) * 100)
    conf_bar = "🟢" * (conf_pct // 20) + "⚪" * (5 - conf_pct // 20)

    tp2_line = f"🎯 TP2:    <code>{sig.tp2:.6f}</code>\n" if sig.tp2 else ""
    tp3_line = f"🎯 TP3:    <code>{sig.tp3:.6f}</code>\n" if sig.tp3 else ""

    lev = getattr(sig, 'leverage', None) or getattr(sig, 'leverage_suggestion', 10) or 10
    return (
        f"💎 <b>AX ELITE MASTER SİNYAL</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"🪙 <b>{sig.symbol}</b> | {dir_emoji} | ⚡ <b>{lev}x</b>\n"
        f"📊 Kalite: <b>{sig.setup_quality}</b> | Skor: <b>{sig.coin_score}</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"📌 Giriş:  <code>{sig.entry_zone:.6f}</code>\n"
        f"🛑 Stop:   <code>{sig.stop_loss:.6f}</code>\n"
        f"🎯 TP1:    <code>{sig.tp1:.6f}</code>\n"
        f"{tp2_line}"
        f"{tp3_line}"
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

def send_trade_open(trade):
    symbol    = trade.get('symbol', 'Unknown')
    direction = trade.get('direction', 'Unknown')
    entry     = trade.get('entry', 0)
    tp1       = trade.get('tp1', 0)
    tp2       = trade.get('tp2', 0)
    tp3       = trade.get('tp3', 0)
    sl        = trade.get('sl', 0)
    leverage  = trade.get('leverage', 10)
    notional  = trade.get('notional_size', 0)
    margin    = trade.get('position_size', 0)
    tp2_line = f"\U0001f3af TP2: <code>{tp2:.6f}</code>\n" if tp2 else ""
    tp3_line = f"\U0001f3af TP3: <code>{tp3:.6f}</code>\n" if tp3 else ""
    notional_line = (f"\U0001f4bc Pozisyon: <code>{notional:.2f}$</code> ({leverage}x) | "
                     f"Teminat: <code>{margin:.2f}$</code>\n") if notional else ""
    msg = (
        f"\u2705 <b>PAPER TRADE OPENED</b>\n"
        f"\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\u2501\n"
        f"\U0001fa99 <b>{symbol}</b> | {direction} | \u26a1 {leverage}x\n"
        f"\U0001f4cc Giris: <code>{entry:.6f}</code>\n"
        f"\U0001f6d1 Stop:  <code>{sl:.6f}</code>\n"
        f"\U0001f3af TP1:   <code>{tp1:.6f}</code>\n"
        f"{tp2_line}"
        f"{tp3_line}"
        f"{notional_line}"
        f"\u23f0 {datetime.now(timezone.utc).strftime('%H:%M UTC')}"
    )
    _queue.push(msg)
def send_trade_close(trade, pnl, reason):
    symbol = trade.get('symbol', 'Unknown')
    direction = trade.get('direction', '')
    result = "WIN 🟢" if pnl > 0 else "LOSS 🔴"
    msg = (
        f"{'🟢' if pnl > 0 else '🔴'} <b>TRADE KAPANDI — {result}</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"🪙 <b>{symbol}</b> {direction}\n"
        f"💰 PnL: <b>{pnl:.2f}$</b>\n"
        f"📋 Neden: {reason}\n"
        f"⏰ {datetime.now(timezone.utc).strftime('%H:%M UTC')}"
    )
    _queue.push(msg)

def send_tp_hit(symbol, direction, tp_level, pnl, remaining_pct=None):
    stage_map = {1: "→ Breakeven SL aktif, TP2 bekleniyor", 2: "→ Runner aktif, trailing stop başladı", 3: "→ Trade tamamen kapandı"}
    stage_note = stage_map.get(tp_level, "")
    pnl_str = f"+{pnl:.3f}$" if pnl >= 0 else f"{pnl:.3f}$"
    msg = (
        f"🎯 <b>TP{tp_level} HIT</b>\n"
        f"🪙 {symbol} {direction}\n"
        f"💰 Kısmi PnL: {pnl_str}\n"
        f"📌 {stage_note}\n"
        f"⏰ {datetime.now(timezone.utc).strftime('%H:%M UTC')}"
    )
    _queue.push(msg)

def send_sl_hit(symbol, direction, pnl):
    msg = (
        f"🛑 <b>STOP-LOSS HIT</b>\n"
        f"🪙 {symbol} {direction}\n"
        f"💰 {pnl:.3f}$\n"
        f"⏰ {datetime.now(timezone.utc).strftime('%H:%M UTC')}"
    )
    _queue.push(msg)

def send_message(text):
    _queue.push(text)
