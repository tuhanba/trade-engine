"""
Telegram Delivery v3 — DB-based dedup + tam format
====================================================
- Duplicate engeli: memory değil DB (telegram_messages tablosu)
- Mesajda: mode, symbol, direction, score, quality, entry, stop,
  TP1/TP2/TP3, RR, net_RR, risk%, risk_amount, position_size,
  notional, leverage, max_loss, fee, confidence, reason, invalidation
- Sadece approved_for_telegram=True sinyaller gönderilir
- Retry mekanizması ve rate limit koruması
"""
import os
import time
import logging
import threading
import requests
from collections import deque
from datetime import datetime, timezone

from config import TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID, EXECUTION_MODE

logger = logging.getLogger(__name__)


def _token() -> str:
    return os.getenv("TELEGRAM_BOT_TOKEN", TELEGRAM_BOT_TOKEN)

def _chat() -> str:
    return os.getenv("TELEGRAM_CHAT_ID", TELEGRAM_CHAT_ID)


# ── HTTP gönderimi ─────────────────────────────────────────────────────────────

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
            }, timeout=10)
            if resp.status_code == 200:
                return True
            if resp.status_code == 429:
                wait = resp.json().get("parameters", {}).get("retry_after", 10)
                logger.warning(f"Telegram rate limit — {wait}s bekleniyor")
                time.sleep(wait)
                continue
        except Exception as e:
            logger.debug(f"Telegram gönderim hatası (attempt {attempt}): {e}")
        if attempt < retries:
            time.sleep(2 ** attempt)
    return False


# ── Gönderim kuyruğu ──────────────────────────────────────────────────────────

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
                time.sleep(0.6)

_queue = _Queue()


# ── Yardımcılar ───────────────────────────────────────────────────────────────

def _fmt(val, decimals: int = 4) -> str:
    try:
        return f"{float(val):.{decimals}f}"
    except Exception:
        return str(val)

def _mode_badge() -> str:
    mode = os.getenv("EXECUTION_MODE", EXECUTION_MODE).upper()
    return "🔴 LIVE" if mode == "LIVE" else "📋 PAPER"


# ── Mesaj formatları ──────────────────────────────────────────────────────────

def format_signal(sig) -> str:
    """Tam sinyal mesajı — tüm risk ve pozisyon bilgileri."""
    quality   = sig.setup_quality or "B"
    direction = sig.direction or "?"
    mode_str  = _mode_badge()

    headers = {
        "S":  ("⭐", "<b>S-CLASS SETUP — FULL SIZE</b>",  "██████████ S"),
        "A+": ("🔥", "<b>A+ SCALP SETUP</b>",             "████████░░ A+"),
        "A":  ("⚡", "<b>A SCALP SETUP</b>",              "██████░░░░ A"),
        "B":  ("⚠️", "<b>B SCALP — HALF SIZE</b>",        "████░░░░░░ B"),
    }
    emoji, title, qbar = headers.get(quality, ("📌", "<b>SCALP SETUP</b>", "░░░░░░░░░░ ?"))
    dir_emoji = "📈 LONG" if direction == "LONG" else "📉 SHORT"
    conf_pct  = int((sig.confidence or 0) * 100)
    conf_bar  = "█" * (conf_pct // 10) + "░" * (10 - conf_pct // 10)
    now_utc   = datetime.now(timezone.utc).strftime("%d.%m %H:%M UTC")

    risk_pct  = _fmt(getattr(sig, "risk_percent", 0), 1)
    risk_amt  = _fmt(getattr(sig, "risk_amount",  0), 2)
    pos_size  = _fmt(getattr(sig, "position_size",0), 4)
    notional  = _fmt(getattr(sig, "notional_size",0), 2)
    leverage  = getattr(sig, "leverage_suggestion", "?")
    max_loss  = _fmt(getattr(sig, "max_loss",     0), 2)
    rr        = _fmt(sig.rr, 2)
    net_rr    = _fmt(getattr(sig, "net_rr", sig.rr), 2)
    fee       = _fmt(getattr(sig, "estimated_fee", 0), 3)
    score     = _fmt(sig.final_score, 1)
    inval     = getattr(sig, "invalidation_level", 0)
    inval_str = f"<code>{_fmt(inval)}</code>" if inval > 0 else "—"

    msg = (
        f"{emoji} {mode_str} | {title}\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"🪙 <b>{sig.symbol}</b>  {dir_emoji}\n"
        f"📊 Score: <b>{score}</b>  |  Kalite: {qbar}\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"📌 Entry:    <code>{_fmt(sig.entry_zone)}</code>\n"
        f"🛑 Stop:     <code>{_fmt(sig.stop_loss)}</code>\n"
        f"🎯 TP1:      <code>{_fmt(sig.tp1)}</code>\n"
        f"🎯 TP2:      <code>{_fmt(sig.tp2)}</code>\n"
        f"🚀 TP3:      <code>{_fmt(sig.tp3)}</code>\n"
        f"❌ Inval.:   {inval_str}\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"⚖️  RR:       <b>{rr}R</b>  (net: {net_rr}R)\n"
        f"💰 Risk:      {risk_pct}%  =  <b>${risk_amt}</b>\n"
        f"📦 Pozisyon: {pos_size} coin\n"
        f"💵 Notional: ${notional}\n"
        f"🔧 Kaldıraç: <b>{leverage}x</b>\n"
        f"⚠️  MaxKayıp: ${max_loss}\n"
        f"💸 Est.Fee:  ${fee}\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"🧠 Güven:    {conf_bar} {conf_pct}%\n"
        f"💡 Sebep:    <i>{sig.reason or '—'}</i>\n"
        f"⏰ {now_utc}\n"
    )
    if quality == "B":
        msg += "\n<i>⚠️ B kalite — yarım pozisyon önerilir</i>"
    if os.getenv("EXECUTION_MODE", EXECUTION_MODE).upper() == "LIVE":
        msg += "\n\n🔴 <b>LIVE MODE — GERÇEK PARA</b>"
    return msg

def format_trade_open(trade: dict) -> str:
    dir_emoji = "📈" if trade.get("direction") == "LONG" else "📉"
    return (
        f"✅ {_mode_badge()} | <b>TRADE AÇILDI</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"{dir_emoji} <b>{trade.get('symbol')}</b> {trade.get('direction')}\n"
        f"📌 Entry:    <code>{_fmt(trade.get('entry', 0))}</code>\n"
        f"🛑 SL:       <code>{_fmt(trade.get('sl', 0))}</code>\n"
        f"🎯 TP1:      <code>{_fmt(trade.get('tp1', 0))}</code>\n"
        f"🎯 TP2:      <code>{_fmt(trade.get('tp2', 0))}</code>\n"
        f"⚖️  RR:      <b>{_fmt(trade.get('rr', 0), 2)}R</b>\n"
        f"💰 Risk:     {_fmt(trade.get('risk_percent', 0), 1)}%  =  ${_fmt(trade.get('risk_amount', 0), 2)}\n"
        f"🔧 Kaldıraç: {trade.get('leverage', '?')}x\n"
        f"⏰ {datetime.now(timezone.utc).strftime('%H:%M UTC')}"
    )

def format_trade_close(trade: dict, pnl: float, reason: str) -> str:
    result    = "WIN 🟢" if pnl > 0 else "LOSS 🔴"
    reason_map = {
        "tp1": "TP1 Hit", "tp2": "TP2 Hit", "tp3": "TP3 Hit",
        "trail": "Trailing Stop", "sl": "Stop Loss",
        "be": "Breakeven SL", "timeout": "Zaman Aşımı",
    }
    reason_str = reason_map.get(reason, reason.upper() if reason else "?")
    r_mult     = round(pnl / max(abs(trade.get("risk_amount", 1)), 1e-10), 2)
    return (
        f"{'🟢' if pnl > 0 else '🔴'} <b>TRADE KAPANDI — {result}</b>\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"🪙 <b>{trade.get('symbol')}</b> {trade.get('direction')}\n"
        f"📌 Entry:  <code>{_fmt(trade.get('entry', 0))}</code>\n"
        f"🏁 Çıkış:  <code>{_fmt(trade.get('close_price', 0))}</code>\n"
        f"💰 PnL:    <b>{'+' if pnl >= 0 else ''}{pnl:.3f}$</b>  ({r_mult:+.2f}R)\n"
        f"📋 Neden:  {reason_str}\n"
        f"⏰ {datetime.now(timezone.utc).strftime('%H:%M UTC')}"
    )


# ── Ana gönderim fonksiyonları ─────────────────────────────────────────────────

def deliver_signal(sig) -> bool:
    """Sinyali Telegram'a gönder. Duplicate kontrolü DB'den."""
    try:
        if not getattr(sig, "approved_for_telegram", False):
            return False
        if sig.setup_quality not in ("S", "A+", "A", "B"):
            return False
        if not sig.is_valid():
            return False

        try:
            from database import is_telegram_sent
            if is_telegram_sent(sig.id):
                logger.debug(f"Telegram duplicate (DB): {sig.symbol}")
                return False
        except Exception:
            pass

        msg = format_signal(sig)
        _queue.push(msg)
        sig.telegram_status = "sent"
        logger.info(
            f"Telegram gönderildi: {sig.symbol} {sig.direction} "
            f"{sig.setup_quality} RR={_fmt(sig.rr, 2)} score={_fmt(sig.final_score, 1)}"
        )
        return True
    except Exception as e:
        logger.error(f"Telegram delivery hatası: {e}")
        try:
            sig.telegram_status = "error"
        except Exception:
            pass
        return False

def send_trade_open(trade: dict):
    try:
        _queue.push(format_trade_open(trade))
    except Exception as e:
        logger.error(f"Trade open bildirim hatası: {e}")

def send_trade_close(trade: dict, pnl: float, reason: str):
    try:
        _queue.push(format_trade_close(trade, pnl, reason))
    except Exception as e:
        logger.error(f"Trade close bildirim hatası: {e}")

def send_message(text: str):
    try:
        _queue.push(text)
    except Exception as e:
        logger.error(f"Telegram mesaj hatası: {e}")
