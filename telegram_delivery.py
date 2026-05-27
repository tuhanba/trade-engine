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
            logger.warning(
                "[Telegram] Yapılandırılmamış — BOT_TOKEN veya CHAT_ID boş. "
                "Mesaj gönderilemedi: %s",
                text[:50],
            )
            return False

        url = _TELEGRAM_API.format(token=self.token)
        payload = {
            "chat_id": self.chat_id,
            "text": text[:4096],
            "parse_mode": "HTML",
        }

        try:
            resp = requests.post(url, json=payload, timeout=_TIMEOUT)
            if resp.status_code == 200:
                return True
            logger.warning(
                "[Telegram] Gönderim başarısız: HTTP %d — %s",
                resp.status_code,
                resp.text[:100],
            )
            return False
        except requests.exceptions.Timeout:
            logger.warning("[Telegram] Timeout — mesaj gönderilemedi")
            return False
        except Exception as exc:
            logger.warning("[Telegram] Hata: %s", exc)
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


def _send_raw_detailed(text: str, parse_mode: str = "HTML") -> tuple[bool, int]:
    token = config.TELEGRAM_BOT_TOKEN
    chat_id = config.TELEGRAM_CHAT_ID
    if not token or not chat_id:
        return False, 0
    try:
        resp = requests.post(
            f"https://api.telegram.org/bot{token}/sendMessage",
            json={"chat_id": chat_id, "text": text, "parse_mode": parse_mode},
            timeout=10,
        )
        if resp.status_code == 200:
            return True, 200
        logger.warning(
            f"[Telegram] Raw send failed: HTTP {resp.status_code} — {resp.text[:100]}"
        )
        return False, resp.status_code
    except requests.exceptions.Timeout:
        logger.warning("[Telegram] Raw send timeout")
        return False, 408
    except Exception as e:
        logger.debug(f"_send_raw error: {e}")
        return False, 500


def _send_raw(text: str, parse_mode: str = "HTML") -> bool:
    ok, _ = _send_raw_detailed(text, parse_mode)
    return ok


class _Queue:
    def __init__(self):
        self._q      = deque()
        self._lock   = threading.Lock()
        self._event  = threading.Event()
        self._thread = threading.Thread(target=self._worker, daemon=True)
        self._thread.start()

    def push(self, text, parse_mode="HTML", dedupe_key=None, sig_id=None, symbol=None, attempts=0):
        if not dedupe_key:
            import uuid
            dedupe_key = f"msg:{uuid.uuid4()}"
            try:
                save_telegram_message(sig_id or "", symbol or "", dedupe_key, text, status="queued")
            except Exception as e:
                logger.warning(f"save_telegram_message error in push: {e}")

        with self._lock:
            self._q.append((text, parse_mode, dedupe_key, attempts))
        self._event.set()

    def _worker(self):
        while True:
            self._event.wait()
            self._event.clear()
            while True:
                try:
                    with self._lock:
                        if not self._q:
                            break
                        item = self._q.popleft()
                    
                    text = item[0]
                    pm = item[1] if len(item) > 1 else "HTML"
                    dk = item[2] if len(item) > 2 else None
                    attempts = item[3] if len(item) > 3 else 0
                    
                    ok, status_code = _send_raw_detailed(text, pm)
                    if ok:
                        if dk:
                            try:
                                from database import mark_telegram_message_sent
                                mark_telegram_message_sent(dk)
                            except Exception:
                                pass
                    else:
                        is_client_error = status_code in (400, 401, 404)
                        if is_client_error:
                            logger.error(
                                f"[Telegram Queue Worker] Kalıcı gönderim hatası (HTTP {status_code}). Retry iptal edildi. DedupeKey: {dk}"
                            )
                            attempts = 99
                        else:
                            attempts += 1
                        
                        if attempts < 5:
                            backoff = min(30, 2 ** attempts)
                            logger.warning(
                                f"[Telegram Queue Worker] Gönderim başarısız (HTTP {status_code}, Deneme {attempts}/5). "
                                f"{backoff} saniye sonra tekrar denenecek. DedupeKey: {dk}"
                            )
                            time.sleep(backoff)
                            with self._lock:
                                self._q.appendleft((text, pm, dk, attempts))
                            self._event.set()
                        else:
                            logger.error(
                                f"[Telegram Queue Worker] Gönderim KALICI olarak başarısız. DedupeKey: {dk}"
                            )
                            if dk:
                                try:
                                    from database import get_conn
                                    with get_conn() as conn:
                                        conn.execute(
                                            "UPDATE telegram_messages SET status = 'failed' WHERE dedupe_key = ?",
                                            (dk,)
                                        )
                                except Exception as db_err:
                                    logger.warning(f"[DB] Mesaj status=failed güncelleme hatası: {db_err}")
                except Exception as e:
                    logger.error(f"[Telegram Queue Worker] Hata: {e}")
                time.sleep(0.5)


_queue = _Queue()


def recover_queued_messages():
    """Recover unsent messages from database on startup."""
    try:
        from database import get_conn
        with get_conn() as conn:
            rows = conn.execute(
                "SELECT sig_id, symbol, dedupe_key, text FROM telegram_messages WHERE status = 'queued' ORDER BY id ASC"
            ).fetchall()
        
        if rows:
            logger.info(f"[Telegram] Recovering {len(rows)} unsent messages from database.")
            for row in rows:
                sig_id, symbol, dedupe_key, text = row
                _queue.push(text, parse_mode="HTML", dedupe_key=dedupe_key, sig_id=sig_id, symbol=symbol, attempts=0)
    except Exception as e:
        logger.warning(f"[Telegram] Failed to recover queued messages: {e}")


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
    hour = datetime.now(timezone.utc).hour
    if 8 <= hour < 18:
        session = "🇬🇧🇺🇸 London/NY"
    elif 0 <= hour < 6:
        session = "🌏 Asian"
    else:
        session = "🌙 Late"
    # BUG FIX: entry_zone veya rr None/eksik olduğunda crash önle
    _entry = getattr(sig, 'entry_zone', None) or getattr(sig, 'entry_price', None) or 0
    _rr    = sig.rr if getattr(sig, 'rr', None) is not None else 0
    msg = (
        f"{header}\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"🧭 Mode: <b>{EXECUTION_MODE.upper()}</b>\n"
        f"🪙 <b>{sig.symbol}</b>  {dir_emoji}\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"📌 Entry:  <code>{_fmt(_entry)}</code>\n"
        f"🛑 Stop:   <code>{_fmt(getattr(sig,'stop_loss',0) or 0)}</code>\n"
        f"🎯 TP1:    <code>{_fmt(getattr(sig,'tp1',0) or 0)}</code>\n"
        f"🎯 TP2:    <code>{_fmt(getattr(sig,'tp2',0) or 0)}</code>\n"
        f"🚀 TP3:    <code>{_fmt(getattr(sig,'tp3',0) or 0)}</code>\n"
        f"━━━━━━━━━━━━━━━━━━━━\n"
        f"📊 Score:   <b>{_fmt(getattr(sig,'final_score',0) or 0, 1)}</b> | Quality: <b>{sig.setup_quality}</b>\n"
        f"⚖️ RR:      <b>{_fmt(_rr, 2)}R</b>  |  Risk: {_fmt(getattr(sig,'risk_percent',0) or 0, 1)}%\n"
        f"💸 Risk Amt:<b>{_fmt(getattr(sig,'max_loss',0) or 0, 2)}</b> | Size: {_fmt(getattr(sig,'position_size',0) or 0, 4)}\n"
        f"🧮 Notional:<b>{_fmt(getattr(sig,'notional_size',0) or 0, 2)}</b> | Lev: {getattr(sig,'leverage_suggestion',None) or '?'}x\n"
        f"🔧 Kaldıraç: {getattr(sig,'leverage_suggestion',None) or '?'}x\n"
        f"📊 Kalite:  {qbar}\n"
        f"🧠 Güven:   {conf_bar} {conf_pct}%\n"
        f"💡 Why this trade?:   <i>{getattr(sig,'reason',None) or '—'}</i>\n"
        f"🛡 Invalidasyon: <code>{_fmt(getattr(sig,'stop_loss',0) or 0)}</code>\n"
        f"⏰ Seans: {session} | {now_utc}\n"
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
        # BUG FIX: is_valid() metodu olmayabilir — AttributeError yakala
        try:
            if not sig.is_valid():
                return False
        except AttributeError:
            # is_valid() yoksa temel alan kontrolü yap
            _entry = getattr(sig, 'entry_zone', None) or getattr(sig, 'entry_price', None)
            if not sig.symbol or not _entry:
                return False
        # BUG FIX: entry_zone None olabilir — güvenli al
        _entry_zone = getattr(sig, 'entry_zone', None) or getattr(sig, 'entry_price', None) or 0
        dedupe_key = f"{sig.symbol}:{sig.direction}:{round(float(_entry_zone), 6)}:{datetime.now(timezone.utc).strftime('%Y-%m-%d')}"
        msg = format_signal(sig)
        saved = True
        try:
            saved = save_telegram_message(sig.id, sig.symbol, dedupe_key, msg, status="queued")
        except Exception as e:
            logger.warning(f"save_telegram_message error: {e}")

        if not saved:
            logger.debug(f"Duplicate Telegram message blocked by DB dedupe_key: {sig.symbol}")
            return False

        _queue.push(msg, "HTML", dedupe_key)
        # BUG FIX: _sent_ids_set memory leak — deque trim edilince set de trim edilmeli
        if len(_sent_ids) >= 1000:
            evicted = _sent_ids[0]  # Bu eleman popleft ile atılacak
            _sent_ids_set.discard(evicted)
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
    """Trade açılış bildirimi."""
    try:
        direction_emoji = "🟢 LONG" if str(data.get("direction", "")).upper() == "LONG" else "🔴 SHORT"
        mode_label = "📄 PAPER" if EXECUTION_MODE != "live" else "🔴 LIVE"
        symbol = data.get('symbol', '-')
        
        text = (
            f"🚀 <b>YENİ İŞLEM AÇILDI</b> | {mode_label}\n\n"
            f"{direction_emoji} <b>{symbol}</b> (x{data.get('leverage', '?')})\n"
            f"━━━━━━━━━━━━━━━━━━\n"
            f"🎯 <b>Giriş:</b> {_fmt(data.get('entry', 0))}\n"
            f"🛑 <b>Stop:</b>  {_fmt(data.get('sl', 0))}\n"
            f"🏆 <b>TP1:</b>   {_fmt(data.get('tp1', 0))}\n"
            f"━━━━━━━━━━━━━━━━━━\n"
            f"💰 <b>Risk USD:</b> ${_fmt(data.get('risk_usd', 0), 2)}\n"
            f"⚡ <b>Kalite:</b>   {data.get('setup_quality', '-')} (Skor: {_fmt(data.get('final_score', 0), 1)})\n"
        )
        _queue.push(text)
    except Exception as e:
        logger.error(f"Trade open bildirim hatası: {e}")


def send_tp_hit(symbol: str, tp_level: int, net_pnl: float,
                remaining_qty: float, balance_after: float = 0):
    """TP1 veya TP2 vurdu bildirimi."""
    try:
        emoji  = "🎯" if tp_level == 1 else "🏆"
        sign   = "+" if net_pnl >= 0 else ""
        text = (
            f"{emoji} <b>TP{tp_level} VURDU!</b> | {symbol}\n\n"
            f"💵 <b>Kâr:</b> {sign}${net_pnl:.2f}\n"
        )
        if tp_level == 1:
            text += "🛡️ <i>SL giriş noktasına çekildi (Breakeven). Kalan miktar için işlem devam ediyor.</i>\n"
        elif tp_level == 2:
            text += "🏃 <i>Runner aktif. İşlem izleniyor.</i>\n"
        
        text += f"💳 Bakiye: <b>${balance_after:.2f}</b>\n"
        _queue.push(text)
    except Exception as e:
        logger.error(f"TP{tp_level} bildirim hatası: {e}")


def send_trade_close(symbol: str, net_pnl: float, total_fee: float,
                     reason: str, duration_str: str,
                     direction: str = "", r_multiple: float = 0,
                     balance_after: float = 0):
    """Trade kapanış bildirimi."""
    try:
        sign   = "+" if net_pnl >= 0 else ""
        if net_pnl > 0:
            result_header = "✅ <b>BAŞARILI İŞLEM</b>"
        elif net_pnl < 0:
            result_header = "❌ <b>STOP/ZARAR</b>"
        else:
            result_header = "⚖️ <b>BAŞA BAŞ (Breakeven)</b>"
            
        reason_map = {
            "sl":               "Stop Loss Vurdu",
            "tp1":              "TP1'de Kapandı",
            "tp2":              "TP2'de Kapandı",
            "tp3":              "TP3 Tamamlandı",
            "manual":           "Manuel Kapatıldı",
            "finish":           "Finish Modu",
            "timeout":          "Süre Doldu",
            "max_hold_timeout": "Maksimum Süre Doldu",
            "trail":            "Trailing Stop Vurdu",
            "runner":           "Runner Kapandı",
            "breakeven":        "Giriş Fiyatında Kapandı",
        }
        reason_str = reason_map.get(reason.lower(), reason.upper())
        dir_emoji  = "LONG" if "LONG" in direction.upper() else "SHORT"
        
        text = (
            f"{result_header} | {symbol} ({dir_emoji})\n\n"
            f"📌 <b>Sebep:</b> {reason_str}\n"
            f"💵 <b>Net PnL:</b> {sign}${net_pnl:.2f}\n"
            f"⏱️ <b>Süre:</b> {duration_str}\n\n"
            f"💳 <b>Güncel Bakiye:</b> ${balance_after:.2f}"
        )
        _queue.push(text)
    except Exception as e:
        logger.error(f"Trade close bildirim hatası: {e}")


def send_message(text):
    try:
        _queue.push(text)
    except Exception as e:
        logger.error(f"Telegram mesaj hatası: {e}")
