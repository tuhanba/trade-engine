"""
telegram_delivery.py — AX Telegram Raporlama v5.0 (LIVE-READY)
===============================================================
Dashboard ile aynı veriyi gösterir. Duplicate mesaj atmaz.
PnL = Dashboard PnL = DB PnL.
"""
import os
import time
import logging
import threading
import requests
from collections import deque
from datetime import datetime, timezone

logger = logging.getLogger(__name__)


def _token():
    return os.getenv("TELEGRAM_BOT_TOKEN", "")


def _chat():
    return os.getenv("TELEGRAM_CHAT_ID", "")


def _send_raw(text, parse_mode="HTML", retries=3):
    token = _token()
    chat = _chat()
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
        except Exception:
            pass
        time.sleep(1)
    return False


class _Queue:
    """Duplicate korumalı asenkron mesaj kuyruğu."""
    def __init__(self):
        self._q = deque()
        self._lock = threading.Lock()
        self._event = threading.Event()
        self._recent = deque(maxlen=20)  # Son 20 mesaj hash'i
        self._thread = threading.Thread(target=self._worker, daemon=True)
        self._thread.start()

    def push(self, text):
        # Duplicate kontrolü
        msg_hash = hash(text[:200])
        with self._lock:
            if msg_hash in self._recent:
                return  # Duplicate, atma
            self._recent.append(msg_hash)
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


def send_trade_open(data):
    """Trade açılış bildirimi — tüm detaylar."""
    dir_emoji = "📈 LONG" if data.get('direction') == 'LONG' else "📉 SHORT"
    conf_pct = int(data.get('confidence', 0.8) * 100)
    conf_bar = "🟢" * (conf_pct // 20) + "⚪" * (5 - conf_pct // 20)

    margin_loss = data.get('margin_loss_pct', 0)
    stop_dist = abs(data.get('entry', 0) - data.get('sl', 0)) / data.get('entry', 1) * 100

    msg = (
        f"💎 <b>AX TRADE AÇILDI</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"🪙 <b>{data.get('symbol', '')}</b> | {dir_emoji}\n"
        f"📊 Kalite: <b>{data.get('setup_quality', '?')}</b> | "
        f"Skor: <b>{data.get('final_score', 0):.1f}</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"📌 Giriş: <code>{data.get('entry', 0):.6f}</code>\n"
        f"🛑 Stop:  <code>{data.get('sl', 0):.6f}</code> ({stop_dist:.2f}%)\n"
        f"🎯 TP1:   <code>{data.get('tp1', 0):.6f}</code>\n"
        f"🎯 TP2:   <code>{data.get('tp2', 0):.6f}</code>\n"
        f"🎯 TP3:   <code>{data.get('tp3', 0):.6f}</code>\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"💰 Notional: <code>{data.get('notional_size', 0):.2f} USD</code>\n"
        f"🏦 Margin: <code>{data.get('margin_used', 0):.2f} USD</code>\n"
        f"⚠️ Risk: <code>{data.get('risk_usd', 0):.2f} USD</code> "
        f"(%{data.get('risk_pct', 1):.1f})\n"
        f"🔥 Max Loss: <code>{data.get('max_loss_after_fee', 0):.2f} USD</code>\n"
        f"📉 Margin Loss: %{margin_loss*100:.1f}\n"
        f"⚙️ Kaldıraç: <b>x{data.get('leverage', 10)}</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"🧠 AI Güven: {conf_bar} %{conf_pct}\n"
        f"📝 Not: <i>{data.get('reason', '')}</i>\n"
        f"🎯 Active: TP1\n"
        f"⏰ {datetime.now(timezone.utc).strftime('%H:%M UTC')}"
    )
    _queue.push(msg)


def send_tp_hit(symbol, level, pnl, remaining_qty):
    """TP1/TP2 hedef bildirimi."""
    msg = (
        f"🎯 <b>TP{level} HEDEFİNE ULAŞILDI!</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"🪙 {symbol}\n"
        f"💰 Partial Net PnL: <code>{pnl:+.4f} USD</code>\n"
        f"📦 Kalan Qty: <code>{remaining_qty:.6f}</code>\n"
        f"🛡️ SL → {'Breakeven' if level == 1 else 'Trailing'}\n"
        f"🎯 Sonraki: {'TP2' if level == 1 else 'RUNNER'}"
    )
    _queue.push(msg)


def send_trade_close(symbol, pnl, fee, reason, duration_str,
                     direction="", r_multiple=0, balance_after=0):
    """Trade kapanış bildirimi — tüm detaylar."""
    result_emoji = "✅" if pnl > 0 else "❌"
    msg = (
        f"{result_emoji} <b>İŞLEM KAPANDI</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"🪙 <b>{symbol}</b> {direction}\n"
        f"🏁 Neden: <b>{reason}</b>\n"
        f"💰 Net PnL: <code>{pnl:+.4f} USD</code>\n"
        f"💸 Fee: <code>{fee:.4f} USD</code>\n"
        f"📊 R: <code>{r_multiple:+.2f}R</code>\n"
        f"⏱️ Süre: <b>{duration_str}</b>\n"
        f"🏦 Bakiye: <code>{balance_after:.2f} USD</code>\n"
        f"━━━━━━━━━━━━━━━━━━━━"
    )
    _queue.push(msg)


def send_message(text):
    """Genel mesaj gönder."""
    _queue.push(text)


def deliver_signal(sig):
    """Geriye dönük uyumluluk."""
    data = sig.__dict__ if hasattr(sig, '__dict__') else sig
    # SignalData → trade_open formatına dönüştür
    mapped = {
        "symbol": data.get("symbol"),
        "direction": data.get("direction"),
        "entry": data.get("entry_zone", data.get("entry", 0)),
        "sl": data.get("stop_loss", data.get("sl", 0)),
        "tp1": data.get("tp1", 0),
        "tp2": data.get("tp2", 0),
        "tp3": data.get("tp3", 0),
        "setup_quality": data.get("setup_quality"),
        "final_score": data.get("final_score", 0),
        "confidence": data.get("confidence", 0.8),
        "risk_usd": data.get("risk_usd", 0),
        "risk_pct": data.get("risk_percent", 1),
        "leverage": data.get("leverage_suggestion", 10),
        "reason": data.get("reason", ""),
        "rr": data.get("rr", 0),
    }
    send_trade_open(mapped)
