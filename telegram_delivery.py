"""
Telegram Delivery — AX Scalp Engine v2.0
=========================================
Sinyal formatı: profesyonel, emoji'li, okunabilir.
Duplicate engeli, retry mekanizması ve hata izolasyonu mevcuttur.
"""
import os
import time
import logging
import threading
import requests
from collections import deque
from datetime import datetime, timezone
from config import TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID, EXECUTION_MODE, TELEGRAM_THRESHOLD
from database import save_telegram_message

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
            if resp.status_code == 429:
                wait = resp.json().get("parameters", {}).get("retry_after", 5)
                time.sleep(wait)
                continue
        except Exception as e:
            logger.debug(f"Telegram gönderim hatası: {e}")
        if attempt < retries:
            time.sleep(2 ** attempt)
    return False

class _Queue:
    def __init__(self):
        self._q      = deque()
        self._lock   = threading.Lock()
        self._event  = threading.Event()
        self._thread = threading.Thread(target=self._worker, daemon=True)
        self._thread.start()
    def push(self, text, parse_mode="HTML", dedupe_key=None):
        with self._lock:
            self._q.append((text, parse_mode, dedupe_key))
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
                if len(item) >= 3:
                    text, pm, dk = item[0], item[1], item[2]
                else:
                    text, pm, dk = item[0], item[1] if len(item) > 1 else "HTML", None
                ok = _send_raw(text, pm)
                if ok and dk:
                    try:
                        from database import mark_telegram_message_sent
                        mark_telegram_message_sent(dk)
                    except Exception:
                        pass
                time.sleep(0.5)

_queue = _Queue()

def _fmt(val, decimals=4):
    try:
        return f"{float(val):.{decimals}f}"
    except Exception:
        return str(val)

def format_signal(sig):
    quality   = sig.setup_quality or "B"
    direction = sig.direction or "?"
    if quality == "S":
        header    = "⭐ <b>S-CLASS SETUP — FULL SIZE</b>"
        qbar      = "██████████ S"
    elif quality == "A+":
        header    = "🔥 <b>A+ SCALP SETUPu</b>"
        qbar      = "████████ A+"
    elif quality == "A":
        header    = "⚡ <b>A SCALP SETUPu</b>"
        qbar      = "██████░░ A"
    else:
        header    = "⚠️ <b>B SCALP — HALF SIZE</b>"
        qbar      = "████░░░░ B"
    dir_emoji = "📈 LONG" if direction == "LONG" else "📉 SHORT"
    conf_pct  = int((sig.confidence or 0) * 100)
    conf_bar  = "█" * (conf_pct // 10) + "░" * (10 - conf_pct // 10)
    now_utc   = datetime.now(timezone.utc).strftime("%H:%M UTC")
    msg = (
        f"{header}\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"🧭 Mode: <b>{EXECUTION_MODE.upper()}</b>\n"
        f"🪙 <b>{sig.symbol}</b>  {dir_emoji}\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"📌 Entry:  <code>{_fmt(sig.entry_zone)}</code>\n"
        f"🛑 Stop:   <code>{_fmt(sig.stop_loss)}</code>\n"
        f"🎯 TP1:    <code>{_fmt(sig.tp1)}</code>\n"
        f"🎯 TP2:    <code>{_fmt(sig.tp2)}</code>\n"
        f"🚀 TP3:    <code>{_fmt(sig.tp3)}</code>\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"📊 Score:   <b>{_fmt(sig.final_score, 1)}</b> | Quality: <b>{sig.setup_quality}</b>\n"
        f"⚖️ RR:      <b>{_fmt(sig.rr, 2)}R</b>  |  Risk: {_fmt(sig.risk_percent, 1)}%\n"
        f"💸 Risk Amt:<b>{_fmt(sig.max_loss, 2)}</b> | Size: {_fmt(sig.position_size, 4)}\n"
        f"🧮 Notional:<b>{_fmt(sig.notional_size, 2)}</b> | Lev: {sig.leverage_suggestion or '?'}x\n"
        f"🔧 Kaldıraç: {sig.leverage_suggestion or '?'}x\n"
        f"📊 Kalite:  {qbar}\n"
        f"🧠 Güven:   {conf_bar} {conf_pct}%\n"
        f"💡 Why this trade?:   <i>{sig.reason or '—'}</i>\n"
        f"🛡 Invalidasyon: <code>{_fmt(sig.stop_loss)}</code>\n"
        f"⏰ {now_utc}\n"
    )
    if quality == "B":
        msg += "\n<i>⚠️ B kalite — yarım pozisyon önerilir</i>"
    return msg

def format_trade_open(trade):
    dir_emoji = "📈" if trade.get("direction") == "LONG" else "📉"
    return (
        f"✅ <b>TRADE AÇILDI</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"{dir_emoji} <b>{trade.get('symbol')}</b> {trade.get('direction')}\n"
        f"📌 Entry: <code>{_fmt(trade.get('entry', 0))}</code>\n"
        f"🛑 SL:    <code>{_fmt(trade.get('sl', 0))}</code>\n"
        f"🎯 TP1:   <code>{_fmt(trade.get('tp1', 0))}</code>\n"
        f"⚖️ RR:    <b>{_fmt(trade.get('rr', 0), 2)}R</b>\n"
        f"⏰ {datetime.now(timezone.utc).strftime('%H:%M UTC')}"
    )

def format_trade_close(trade, pnl, reason):
    result = "WIN 🟢" if pnl > 0 else "LOSS 🔴"
    reason_map = {
        "tp1": "TP1 Hit", "tp2": "TP2 Hit",
        "trail": "Trailing Stop", "sl": "Stop Loss",
        "timeout": "Zaman Aşımı",
    }
    reason_str = reason_map.get(reason, reason.upper() if reason else "?")
    return (
        f"{'🟢' if pnl > 0 else '🔴'} <b>TRADE KAPANDI — {result}</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"🪙 <b>{trade.get('symbol')}</b> {trade.get('direction')}\n"
        f"📌 Entry:  <code>{_fmt(trade.get('entry', 0))}</code>\n"
        f"🏁 Çıkış:  <code>{_fmt(trade.get('close_price', 0))}</code>\n"
        f"💰 PnL:    <b>{'+' if pnl >= 0 else ''}{pnl:.3f}$</b>\n"
        f"📋 Neden:  {reason_str}\n"
        f"⏰ {datetime.now(timezone.utc).strftime('%H:%M UTC')}"
    )

_sent_ids: deque = deque(maxlen=1000)
_sent_ids_set: set = set()

def deliver_signal(sig):
    try:
        if sig.id in _sent_ids_set:
            logger.debug(f"Duplicate sinyal engellendi: {sig.symbol}")
            return False
        if (sig.final_score or 0) < TELEGRAM_THRESHOLD:
            return False
        if sig.setup_quality not in ["S", "A+", "A", "B"]:
            return False
        if not sig.is_valid():
            return False
        dedupe_key = f"{sig.symbol}:{sig.direction}:{round(sig.entry_zone, 6)}:{datetime.now(timezone.utc).strftime('%Y-%m-%d')}"
        msg = format_signal(sig)
        if not save_telegram_message(sig.id, sig.symbol, dedupe_key, msg, status="queued"):
            return False
        _queue.push(msg, "HTML", dedupe_key)
        if len(_sent_ids) >= 1000:
            _sent_ids_set.discard(_sent_ids[0])
        _sent_ids.append(sig.id)
        _sent_ids_set.add(sig.id)
        sig.telegram_status = "sent"
        logger.info(
            f"Telegram gönderildi: {sig.symbol} {sig.direction} "
            f"{sig.setup_quality} RR={_fmt(sig.rr, 2)}"
        )
        return True
    except Exception as e:
        logger.error(f"Telegram delivery hatası: {e}")
        try:
            sig.telegram_status = "error"
        except Exception:
            pass
        return False

def send_trade_open(data: dict):
    """
    Trade açılış bildirimi. PAPER/DRY-RUN etiketi zorunludur.
    data dict'i execution_engine.open_trade() tarafından sağlanır.
    """
    try:
        direction_emoji = "🟢 LONG" if str(data.get("direction", "")).upper() == "LONG" else "🔴 SHORT"
        mode_label = "📄 PAPER/DRY-RUN" if EXECUTION_MODE != "live" else "🔴 LIVE"
        text = (
            f"{mode_label}\n"
            f"━━━━━━━━━━━━━━━━━━\n"
            f"🆕 TRADE AÇILDI\n"
            f"{direction_emoji} {data.get('symbol', '-')} x{data.get('leverage', '?')}\n"
            f"━━━━━━━━━━━━━━━━━━\n"
            f"Entry:   {_fmt(data.get('entry', 0))}\n"
            f"SL:      {_fmt(data.get('sl', 0))}\n"
            f"TP1:     {_fmt(data.get('tp1', 0))}\n"
            f"TP2:     {_fmt(data.get('tp2', 0))}\n"
            f"TP3:     {_fmt(data.get('tp3', 0))}\n"
            f"━━━━━━━━━━━━━━━━━━\n"
            f"Notional:     ${_fmt(data.get('notional_size', 0), 2)}\n"
            f"Margin:       ${_fmt(data.get('margin_used', 0), 2)}\n"
            f"Risk USD:     ${_fmt(data.get('risk_usd', 0), 2)}\n"
            f"Max Kayıp:    ${_fmt(data.get('max_loss_after_fee', 0), 2)}\n"
            f"Giriş Fee:    ${_fmt(data.get('open_fee', 0), 4)}\n"
            f"━━━━━━━━━━━━━━━━━━\n"
            f"Kalite:  {data.get('setup_quality', '-')} | Skor: {_fmt(data.get('final_score', 0), 1)}\n"
            f"Sebep:   {data.get('reason', '-')}\n"
        )
        _queue.push(text)
    except Exception as e:
        logger.error(f"Trade open bildirim hatası: {e}")


def send_tp_hit(symbol: str, tp_level: int, net_pnl: float,
                remaining_qty: float, balance_after: float = 0):
    """TP1 veya TP2 vurdu bildirimi."""
    try:
        emoji = "🎯" if tp_level == 1 else "🏆"
        sign = "+" if net_pnl >= 0 else ""
        text = (
            f"{emoji} TP{tp_level} — {symbol}\n"
            f"━━━━━━━━━━━━━━━━━━\n"
            f"TP{tp_level} PnL:      {sign}${net_pnl:.4f}\n"
            f"Kalan Qty:    {remaining_qty:.6f}\n"
            f"Bakiye:       ${balance_after:.2f}\n"
        )
        if tp_level == 1:
            text += "SL → Breakeven taşındı\n"
        elif tp_level == 2:
            text += "Runner / Trailing başladı\n"
        _queue.push(text)
    except Exception as e:
        logger.error(f"TP{tp_level} bildirim hatası: {e}")


def send_trade_close(symbol: str, net_pnl: float, total_fee: float,
                     reason: str, duration_str: str,
                     direction: str = "", r_multiple: float = 0,
                     balance_after: float = 0):
    """Trade kapanış bildirimi."""
    try:
        sign = "+" if net_pnl >= 0 else ""
        result = "✅ KAR" if net_pnl > 0 else "❌ ZARAR"
        text = (
            f"{result} — {symbol}\n"
            f"━━━━━━━━━━━━━━━━━━\n"
            f"Sebep:        {reason.upper()}\n"
            f"Net PnL:      {sign}${net_pnl:.4f}\n"
            f"Toplam Fee:   ${total_fee:.4f}\n"
            f"R-Multiple:   {r_multiple:.2f}R\n"
            f"Süre:         {duration_str}\n"
            f"━━━━━━━━━━━━━━━━━━\n"
            f"Bakiye:       ${balance_after:.2f}\n"
        )
        _queue.push(text)
    except Exception as e:
        logger.error(f"Trade close bildirim hatası: {e}")


def send_message(text):
    try:
        _queue.push(text)
    except Exception as e:
        logger.error(f"Telegram mesaj hatası: {e}")
