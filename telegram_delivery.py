"""
telegram_delivery.py – Telegram bildirim modülü.

Token/chat_id yoksa sadece log warning verir, crash olmaz.
Telegram API hatası botu durdurmaz.
"""

from __future__ import annotations

import logging
import time
import threading
from collections import deque
from datetime import datetime, timezone
from typing import Any

import requests

import config

try:
    from config import EXECUTION_MODE
    from database import save_telegram_message
except Exception:
    EXECUTION_MODE = "paper"
    def save_telegram_message(*a, **kw): return True

logger = logging.getLogger("ax.telegram")

_TELEGRAM_API = "https://api.telegram.org/bot{token}/sendMessage"
_TIMEOUT = 10


class TelegramDelivery:
    """Güvenli Telegram bildirim gönderici."""

    def __init__(
        self,
        token: str = "",
        chat_id: str = "",
    ):
        self.token = token or config.TELEGRAM_BOT_TOKEN
        self.chat_id = chat_id or config.TELEGRAM_CHAT_ID

    # ── Durum ────────────────────────────────────────────────────

    def is_configured(self) -> bool:
        """Token ve chat_id tanımlı mı?"""
        return bool(self.token) and bool(self.chat_id)

    # ── Temel gönderim ───────────────────────────────────────────

    def send_message(self, text: str) -> bool:
        """
        Mesaj gönderir. Başarılıysa True döner.
        Yapılandırılmamışsa veya hata varsa False döner, crash olmaz.
        """
        if not self.is_configured():
            logger.warning("Telegram yapılandırılmamış – mesaj atlandı")
            return False

        url = _TELEGRAM_API.format(token=self.token)
        payload = {
            "chat_id": self.chat_id,
            "text": text,
            "parse_mode": "HTML",
        }

        try:
            resp = requests.post(url, json=payload, timeout=_TIMEOUT)
            if resp.status_code == 200:
                return True
            logger.warning(
                "Telegram yanıt hatası: %s – %s",
                resp.status_code,
                resp.text[:200],
            )
            return False
        except requests.RequestException as exc:
            logger.error("Telegram gönderim hatası: %s", exc)
            return False

    # ── Trade bildirimleri ───────────────────────────────────────

    def send_trade_open(self, trade: dict[str, Any]) -> bool:
        """Trade açılış mesajı gönderir."""
        text = (
            "📈 <b>Trade Açıldı</b>\n"
            f"Symbol : {trade.get('symbol', '?')}\n"
            f"Side   : {trade.get('side', '?')}\n"
            f"Entry  : {trade.get('entry_price', 0)}\n"
            f"SL     : {trade.get('stop_loss', 0)}\n"
            f"TP1    : {trade.get('tp1', 0)}\n"
            f"TP2    : {trade.get('tp2', 0)}\n"
            f"TP3    : {trade.get('tp3', 0)}\n"
            f"Lev    : {trade.get('leverage', 1)}x\n"
            f"Risk%  : {trade.get('risk_pct', 0)}%\n"
            f"RiskUSD: ${trade.get('risk_usd', 0)}\n"
            f"Margin : ${trade.get('margin_used', 0)}\n"
            f"Notional: ${trade.get('notional', 0)}"
        )
        return self.send_message(text)

    def send_trade_close(self, trade: dict[str, Any]) -> bool:
        """Trade kapanış mesajı gönderir."""
        pnl = trade.get("realized_pnl", 0)
        emoji = "✅" if pnl >= 0 else "❌"
        text = (
            f"{emoji} <b>Trade Kapandı</b>\n"
            f"Symbol : {trade.get('symbol', '?')}\n"
            f"Side   : {trade.get('side', '?')}\n"
            f"Exit   : {trade.get('exit_price', 0)}\n"
            f"PnL    : ${pnl}\n"
            f"Reason : {trade.get('close_reason', '')}"
        )
        return self.send_message(text)

    def send_error(self, title: str, error: Any) -> bool:
        """Hata bildirimi gönderir."""
        text = f"⚠️ <b>{title}</b>\n{str(error)[:500]}"
        return self.send_message(text)


def _send_raw(text: str, parse_mode: str = "HTML") -> bool:
    token = config.TELEGRAM_BOT_TOKEN
    chat_id = config.TELEGRAM_CHAT_ID
    if not token or not chat_id:
        return False
    try:
        resp = requests.post(
            f"https://api.telegram.org/bot{token}/sendMessage",
            json={"chat_id": chat_id, "text": text, "parse_mode": parse_mode},
            timeout=10,
        )
        return resp.status_code == 200
    except Exception as e:
        logger.debug(f"_send_raw error: {e}")
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
        logger.error(f"deliver_signal hatası: {e}")
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
