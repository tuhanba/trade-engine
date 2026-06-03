"""
telegram_delivery.py — AX Telegram Bildirim Katmanı v6.0

Tasarım İlkeleri:
  - TEK yol: Event Bus → NotificationService → burası. Başka hiçbir dosya
    doğrudan Telegram çağrısı yapmamalı.
  - Mesajlar telefonda 3 saniyede okunabilir olmalı.
  - Her mesaj tipi kendi dedupe_key'ini kendisi üretir.
  - Altyapı değişmez: _Queue, _send_raw_detailed, recover_queued_messages.
  - Token yoksa crash yok, sadece debug log.
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

_client_instance = None

def _get_binance_client():
    global _client_instance
    if _client_instance is None:
        try:
            from binance.client import Client
            _client_instance = Client(config.BINANCE_API_KEY or "", config.BINANCE_API_SECRET or "")
        except Exception as e:
            logger.debug("[Telegram] Binance client başlatılamadı: %s", e)
    return _client_instance


# ── Yardımcı ──────────────────────────────────────────────────────────────────

def _fmt(val, decimals: int = 4) -> str:
    try:
        return f"{float(val):.{decimals}f}"
    except Exception:
        return str(val)


def _pct(entry: float, target: float, direction: str) -> str:
    """Entry'den target'a yüzde fark — yön duyarlı."""
    try:
        if entry <= 0 or target <= 0:
            return ""
        diff = (target - entry) / entry * 100
        if direction.upper() == "SHORT":
            diff = -diff
        sign = "+" if diff >= 0 else ""
        return f"  ({sign}{diff:.1f}%)"
    except Exception:
        return ""


def _now_utc() -> str:
    return datetime.now(timezone.utc).strftime("%H:%M UTC")


def _session() -> str:
    h = datetime.now(timezone.utc).hour
    if 8 <= h < 18:
        return "🇬🇧🇺🇸 London/NY"
    if 0 <= h < 6:
        return "🌏 Asian"
    return "🌙 Late"


def _mode_tag() -> str:
    try:
        mode = EXECUTION_MODE
    except Exception:
        mode = "paper"
    return "🔴 LIVE" if mode == "live" else "PAPER"


LINE = "─" * 22


# ── Altyapı — değişmez ────────────────────────────────────────────────────────

class TelegramDelivery:
    """Geriye dönük uyumluluk — eski kod bu sınıfı import ediyor."""

    def __init__(self, token: str = "", chat_id: str = ""):
        self.token = token or config.TELEGRAM_BOT_TOKEN
        self.chat_id = chat_id or config.TELEGRAM_CHAT_ID

    def is_configured(self) -> bool:
        return bool(self.token) and bool(self.chat_id)

    def send_message(self, text: str) -> bool:
        return send_message(text)

    def send_trade_open(self, trade: dict) -> bool:
        return send_trade_open(trade)

    def send_trade_close(self, trade: dict) -> bool:
        pnl = trade.get("realized_pnl", 0)
        return send_trade_close(
            symbol=trade.get("symbol", "?"),
            net_pnl=float(pnl),
            total_fee=float(trade.get("total_fee", 0)),
            reason=trade.get("close_reason", ""),
            duration_str="",
            direction=trade.get("direction", trade.get("side", "")),
        )

    def send_error(self, title: str, error: Any) -> bool:
        return send_message(f"⚠️ <b>{title}</b>\n{str(error)[:500]}")

    def send_voice(self, voice_bytes: bytes, caption: Optional[str] = None) -> bool:
        return send_voice(voice_bytes, caption)


def _send_raw_detailed(text: str, parse_mode: str = "HTML", reply_markup: Optional[dict] = None) -> tuple[bool, int]:
    token = config.TELEGRAM_BOT_TOKEN
    chat_id = config.TELEGRAM_CHAT_ID
    if not token or not chat_id:
        logger.debug("[Telegram] Token/chat_id boş — mesaj atlandı.")
        return False, 0
    try:
        payload = {"chat_id": chat_id, "text": text[:4096], "parse_mode": parse_mode}
        if reply_markup:
            payload["reply_markup"] = reply_markup
        resp = requests.post(
            f"https://api.telegram.org/bot{token}/sendMessage",
            json=payload,
            timeout=_TIMEOUT,
        )
        if resp.status_code == 200:
            return True, 200
        logger.warning("[Telegram] HTTP %d — %s", resp.status_code, resp.text[:100])
        return False, resp.status_code
    except requests.exceptions.ConnectTimeout:
        return False, 499
    except requests.exceptions.ReadTimeout:
        return False, 408
    except Exception as e:
        logger.debug("[Telegram] send hata: %s", e)
        return False, 500


def _send_photo_raw(photo_bytes: bytes, caption: str, parse_mode: str = "HTML") -> tuple[bool, int]:
    token = config.TELEGRAM_BOT_TOKEN
    chat_id = config.TELEGRAM_CHAT_ID
    if not token or not chat_id:
        logger.debug("[Telegram] Token/chat_id boş — fotoğraf atlandı.")
        return False, 0
    try:
        files = {"photo": ("chart.png", photo_bytes, "image/png")}
        payload = {"chat_id": chat_id, "caption": caption[:1024], "parse_mode": parse_mode}
        resp = requests.post(
            f"https://api.telegram.org/bot{token}/sendPhoto",
            data=payload,
            files=files,
            timeout=15,
        )
        if resp.status_code == 200:
            return True, 200
        logger.warning("[Telegram] sendPhoto HTTP %d — %s", resp.status_code, resp.text[:100])
        return False, resp.status_code
    except Exception as e:
        logger.debug("[Telegram] sendPhoto hata: %s", e)
        return False, 500


def _send_raw(text: str, parse_mode: str = "HTML") -> bool:
    ok, _ = _send_raw_detailed(text, parse_mode)
    return ok


class _Queue:
    """Thread-safe Telegram mesaj kuyruğu. Retry + dedupe."""

    def __init__(self):
        self._q = deque()
        self._lock = threading.Lock()
        self._event = threading.Event()
        self._thread = threading.Thread(target=self._worker, daemon=True)
        self._thread.start()

    def push(self, text: str, parse_mode: str = "HTML",
             dedupe_key: str = None, sig_id=None, symbol: str = None,
             attempts: int = 0, reply_markup: Optional[dict] = None,
             photo_bytes: Optional[bytes] = None):
        if not dedupe_key:
            import uuid
            dedupe_key = f"msg:{uuid.uuid4()}"
            try:
                save_telegram_message(sig_id or "", symbol or "", dedupe_key,
                                      text, status="queued")
            except Exception as e:
                logger.debug("[Telegram] save_telegram_message: %s", e)
        with self._lock:
            self._q.append((text, parse_mode, dedupe_key, attempts, reply_markup, photo_bytes))
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
                    text, pm, dk, attempts, reply_markup, photo_bytes = item[0], item[1], item[2], item[3], item[4], item[5]
                    if photo_bytes:
                        ok, status_code = _send_photo_raw(photo_bytes, text, pm)
                    else:
                        ok, status_code = _send_raw_detailed(text, pm, reply_markup)
                    if ok:
                        if dk:
                            try:
                                from database import mark_telegram_message_sent
                                mark_telegram_message_sent(dk)
                            except Exception:
                                pass
                    else:
                        is_client_err = status_code in (400, 401, 404)
                        is_read_timeout = status_code == 408
                        if is_client_err:
                            logger.error("[TGQueue] Kalıcı hata HTTP %d key=%s", status_code, dk)
                            attempts = 99
                            if dk:
                                try:
                                    from database import get_conn
                                    with get_conn() as conn:
                                        conn.execute(
                                            "UPDATE telegram_messages SET status='failed' WHERE dedupe_key=?",
                                            (dk,)
                                        )
                                except Exception:
                                    pass
                        elif is_read_timeout:
                            logger.warning("[TGQueue] Read timeout — muhtemelen gitti key=%s", dk)
                            attempts = 99
                            if dk:
                                try:
                                    from database import mark_telegram_message_sent
                                    mark_telegram_message_sent(dk)
                                except Exception:
                                    pass
                        else:
                            attempts += 1
                        if attempts < 5:
                            backoff = min(30, 2 ** attempts)
                            logger.warning("[TGQueue] Retry %d/5 in %ds key=%s",
                                           attempts, backoff, dk)
                            time.sleep(backoff)
                            with self._lock:
                                self._q.appendleft((text, pm, dk, attempts, reply_markup, photo_bytes))
                            self._event.set()
                        elif attempts != 99:
                            logger.error("[TGQueue] Kalıcı başarısız key=%s", dk)
                            if dk:
                                try:
                                    from database import get_conn
                                    with get_conn() as conn:
                                        conn.execute(
                                            "UPDATE telegram_messages SET status='failed' WHERE dedupe_key=?",
                                            (dk,)
                                        )
                                except Exception:
                                    pass
                except Exception as e:
                    logger.error("[TGQueue] worker hata: %s", e)
                time.sleep(0.5)


_queue = _Queue()


def recover_queued_messages():
    """Startup'ta DB'deki gönderilmemiş mesajları kuyruğa al."""
    try:
        from database import get_conn
        with get_conn() as conn:
            rows = conn.execute(
                "SELECT sig_id, symbol, dedupe_key, text FROM telegram_messages "
                "WHERE status='queued' ORDER BY id ASC"
            ).fetchall()
        if rows:
            logger.info("[Telegram] %d mesaj recovery.", len(rows))
            for row in rows:
                sig_id, symbol, dedupe_key, text = row
                if text:
                    _queue.push(text, dedupe_key=dedupe_key, sig_id=sig_id, symbol=symbol)
    except Exception as e:
        logger.warning("[Telegram] recovery hatası: %s", e)


def _push_with_dedupe(text: str, dedupe_key: str,
                      symbol: str = "", sig_id: str = "",
                      photo_bytes: Optional[bytes] = None,
                      reply_markup: Optional[dict] = None) -> bool:
    """DB'ye kaydet (duplicate kontrolü), kuyruğa ekle."""
    try:
        saved = save_telegram_message(sig_id, symbol, dedupe_key, text, status="queued")
        if not saved:
            logger.debug("[Telegram] duplicate engellendi: %s", dedupe_key)
            return False
    except Exception as e:
        logger.warning("[Telegram] save_telegram_message: %s", e)
    _queue.push(text, dedupe_key=dedupe_key, symbol=symbol, photo_bytes=photo_bytes, reply_markup=reply_markup)
    return True


# ── Mesaj Formatları ──────────────────────────────────────────────────────────
#
# Tasarım kararları:
#   - ─ ayırıcısı kullan (━ değil — daha ince, daha az gürültü)
#   - Sayılar her zaman <code> içinde (monospace, hizalı)
#   - Önemli değerler <b> içinde
#   - Her mesaj: başlık → detay → bakiye → saat
#   - WIN/LOSS görsel ayrımı net ama abartısız

def format_signal(sig) -> str:
    """
    Sinyal bildirimi (Telegram threshold geçti, henüz trade açılmadı).
    Trader'ın kendisi karar verebileceği düzeyde bilgi.
    """
    quality = sig.setup_quality or "B"
    direction = sig.direction or "?"

    quality_map = {
        "S":  ("⭐", "S-CLASS",  "Tam pozisyon"),
        "A+": ("🔥", "A+ SETUP", "Tam pozisyon"),
        "A":  ("⚡", "A  SETUP", "Normal pozisyon"),
        "B":  ("🔶", "B  SETUP", "Yarım pozisyon"),
        "C":  ("⚪", "C  SETUP", "Küçük pozisyon"),
    }
    q_emoji, q_label, q_size = quality_map.get(quality, ("⚪", quality, ""))
    dir_icon  = "▲" if direction == "LONG" else "▼"

    conf_pct = int((getattr(sig, "confidence", 0) or 0) * 100)
    conf_bar = "█" * (conf_pct // 10) + "░" * (10 - conf_pct // 10)

    _entry = getattr(sig, "entry_zone", None) or getattr(sig, "entry_price", None) or 0
    _sl    = getattr(sig, "stop_loss", 0) or 0
    _tp1   = getattr(sig, "tp1", 0) or 0
    _tp2   = getattr(sig, "tp2", 0) or 0
    _tp3   = getattr(sig, "tp3", 0) or 0
    _rr    = getattr(sig, "rr", 0) or 0
    _score = getattr(sig, "final_score", 0) or 0
    _risk  = getattr(sig, "risk_percent", 0) or 0
    _loss  = getattr(sig, "max_loss", 0) or 0
    _lev   = getattr(sig, "leverage_suggestion", None) or "?"
    _why   = getattr(sig, "reason", None) or "—"

    tp1_pct = _pct(_entry, _tp1, direction)
    tp2_pct = _pct(_entry, _tp2, direction)
    tp3_pct = _pct(_entry, _tp3, direction)
    sl_pct  = _pct(_entry, _sl, "SHORT" if direction == "LONG" else "LONG")  # SL her zaman negatif

    msg = (
        f"{q_emoji} <b>{q_label}</b>  [{_mode_tag()}]\n"
        f"{LINE}\n"
        f"{dir_icon} <b>{sig.symbol}</b>  {direction}  ·  {q_size}\n"
        f"{LINE}\n"
        f"📍 Giriş  <code>{_fmt(_entry)}</code>\n"
        f"🛑 Stop   <code>{_fmt(_sl)}</code>{sl_pct}\n"
        f"🎯 TP1    <code>{_fmt(_tp1)}</code>{tp1_pct}\n"
        f"🎯 TP2    <code>{_fmt(_tp2)}</code>{tp2_pct}\n"
        f"🚀 TP3    <code>{_fmt(_tp3)}</code>{tp3_pct}\n"
        f"{LINE}\n"
        f"📊 Skor   <b>{_score:.1f}</b>  ·  RR  <b>{_rr:.2f}R</b>\n"
        f"💰 Risk   <b>${_loss:.2f}</b>  ({_risk:.1f}%)  ·  {_lev}x\n"
        f"🧠 Güven  {conf_bar}  {conf_pct}%\n"
        f"{LINE}\n"
        f"💡 <i>{_why}</i>\n"
        f"{LINE}\n"
        f"⏰ {_session()}  ·  {_now_utc()}\n"
    )

    if quality == "B":
        msg += "\n<i>⚠️ B kalite — yarım pozisyon önerilir</i>"
    elif quality == "C":
        msg += "\n<i>⬇️ C kalite — küçük pozisyon</i>"

    return msg


def send_trade_open(data: dict) -> bool:
    """
    Trade açılış bildirimi.
    Pozisyon açıldı — trader bu mesajı görünce işlem ekranda.
    """
    try:
        direction = str(data.get("direction", "") or data.get("side", "")).upper()
        dir_icon  = "▲" if direction == "LONG" else "▼"
        symbol    = data.get("symbol", "?")

        entry = float(data.get("entry", 0) or data.get("entry_price", 0) or 0)
        sl    = float(data.get("sl", 0) or data.get("stop_loss", 0) or 0)
        tp1   = float(data.get("tp1", 0) or 0)
        tp2   = float(data.get("tp2", 0) or 0)
        lev   = data.get("leverage", "?")
        risk  = float(data.get("risk_usd", 0) or 0)
        qual  = data.get("setup_quality", "-")
        score = float(data.get("final_score", 0) or 0)

        text = (
            f"✅ <b>İŞLEM AÇILDI</b>  [{_mode_tag()}]\n"
            f"{LINE}\n"
            f"{dir_icon} <b>{symbol}</b>  {direction}  ·  {lev}x\n"
            f"{LINE}\n"
            f"📍 Giriş  <code>{_fmt(entry)}</code>\n"
            f"🛑 Stop   <code>{_fmt(sl)}</code>{_pct(entry, sl, 'SHORT' if direction == 'LONG' else 'LONG')}\n"
            f"🎯 TP1    <code>{_fmt(tp1)}</code>{_pct(entry, tp1, direction)}\n"
            f"🎯 TP2    <code>{_fmt(tp2)}</code>{_pct(entry, tp2, direction)}\n"
            f"{LINE}\n"
            f"💰 Risk   <b>${_fmt(risk, 2)}</b>  ·  Kalite: <b>{qual}</b>  ({score:.1f}p)\n"
            f"⏰ {_now_utc()}\n"
        )

        reply_markup = None
        trade_id = data.get("trade_id") or data.get("id")
        if trade_id:
            reply_markup = {
                "inline_keyboard": [
                    [
                        {"text": "🔒 Breakeven'a Çek", "callback_data": f"cmd:be_trade_{trade_id}"},
                        {"text": "🚨 İşlemi Kapat", "callback_data": f"cmd:close_trade_{trade_id}"}
                    ]
                ]
            }

        dk = f"open:{symbol}:{direction}:{_fmt(entry)}:{datetime.now(timezone.utc).strftime('%Y%m%d%H%M')}"
        return _push_with_dedupe(text, dk, symbol=symbol, reply_markup=reply_markup)
    except Exception as e:
        logger.error("[Telegram] send_trade_open: %s", e)
        return False


def send_tp_hit(symbol: str, tp_level: int, net_pnl: float,
                remaining_qty: float, balance_after: float = 0,
                entry: float = 0, tp_price: float = 0,
                direction: str = "") -> bool:
    """
    TP bildirimi.
    Ne kazandı, ne kaldı, şimdi ne oluyor — 3 şey.
    """
    try:
        sign = "+" if net_pnl >= 0 else ""

        if tp_level == 1:
            header  = f"🎯 <b>TP1 VURDU!</b>  {symbol}"
            next_st = "🛡 SL → breakeven. Bu trade artık sıfır riskli."
        elif tp_level == 2:
            header  = f"🏆 <b>TP2 VURDU!</b>  {symbol}"
            next_st = "🏃 Runner devrede — TP3'e koşuyor."
        else:
            header  = f"🚀 <b>TP{tp_level} VURDU!</b>  {symbol}"
            next_st = "✅ Tüm hedefler tamamlandı."

        rem_line = (
            f"📦 Kalan      <code>{remaining_qty:.4f}</code>\n"
            if remaining_qty > 0 else ""
        )

        text = (
            f"{header}\n"
            f"{LINE}\n"
            f"💵 Kısmi Kâr  <b>{sign}${net_pnl:.2f}</b>\n"
            f"{rem_line}"
            f"{LINE}\n"
            f"<i>{next_st}</i>\n"
            f"💳 Bakiye     <b>${balance_after:.2f}</b>\n"
            f"⏰ {_now_utc()}\n"
        )

        dk = f"tp{tp_level}:{symbol}:{_fmt(net_pnl)}:{datetime.now(timezone.utc).strftime('%Y%m%d%H%M')}"
        return _push_with_dedupe(text, dk, symbol=symbol)
    except Exception as e:
        logger.error("[Telegram] send_tp_hit TP%d: %s", tp_level, e)
        return False


def send_trade_close(symbol: str, net_pnl: float, total_fee: float,
                     reason: str, duration_str: str,
                     direction: str = "", r_multiple: float = 0,
                     balance_after: float = 0) -> bool:
    """
    Kapanış bildirimi.

    WIN  → 🟢 ön planda, kâr ve R-multiple belirgin.
    LOSS → 🔴 sakin, factual. Eziyet yok. Sebep ve süre var.
    BE   → ⚖️ nötr.
    """
    try:
        sign      = "+" if net_pnl >= 0 else ""
        dir_label = "LONG" if "LONG" in str(direction).upper() else "SHORT"

        reason_map = {
            "sl":               "Stop Loss",
            "tp1":              "TP1",
            "tp2":              "TP2",
            "tp3":              "TP3",
            "manual":           "Manuel Kapatma",
            "timeout":          "Süre Doldu",
            "max_hold_timeout": "Max Süre Doldu",
            "trail":            "Trailing Stop",
            "runner":           "Runner Kapandı",
            "breakeven":        "Breakeven",
            "finish":           "Finish Modu",
        }
        reason_label = reason_map.get(str(reason).lower(), str(reason).upper())

        if net_pnl > 0:
            header  = f"🟢 <b>KAZANÇ!</b>  {symbol}  ({dir_label})"
            pnl_ln  = f"💵 Net Kâr    <b>{sign}${net_pnl:.2f}</b>"
            r_ln    = f"📈 R-Multiple  <b>+{r_multiple:.2f}R</b>\n" if r_multiple > 0 else ""
        elif net_pnl < 0:
            header  = f"🔴 <b>ZARAR</b>  {symbol}  ({dir_label})"
            pnl_ln  = f"💵 Zarar      <b>${net_pnl:.2f}</b>"
            r_ln    = f"📉 R-Multiple  <b>{r_multiple:.2f}R</b>\n" if r_multiple != 0 else ""
        else:
            header  = f"⚖️ <b>BAŞA BAŞ</b>  {symbol}  ({dir_label})"
            pnl_ln  = "💵 PnL        <b>$0.00</b>"
            r_ln    = ""

        fee_ln = f"💸 Komisyon   ${total_fee:.3f}\n" if total_fee and total_fee > 0 else ""
        dur_ln = f"⏱ Süre       {duration_str}\n" if duration_str else ""

        text = (
            f"{header}\n"
            f"{LINE}\n"
            f"📋 Sebep      {reason_label}\n"
            f"{pnl_ln}\n"
            f"{r_ln}"
            f"{fee_ln}"
            f"{dur_ln}"
            f"{LINE}\n"
            f"💳 Bakiye     <b>${balance_after:.2f}</b>\n"
            f"⏰ {_now_utc()}\n"
        )

        dk = (f"close:{symbol}:{direction}:{reason}:{_fmt(net_pnl)}"
              f":{datetime.now(timezone.utc).strftime('%Y%m%d%H%M')}")
        return _push_with_dedupe(text, dk, symbol=symbol)
    except Exception as e:
        logger.error("[Telegram] send_trade_close: %s", e)
        return False


def deliver_signal(sig) -> bool:
    """
    Sinyal Telegram eşiğini geçti — bildirim gönder.
    Trader'a "bakmalısın" mesajı, henüz açılmadı.
    """
    try:
        if sig.setup_quality not in ("S", "A+", "A", "B", "C"):
            return False

        try:
            valid = sig.is_valid() if hasattr(sig, "is_valid") else bool(
                sig.symbol and (getattr(sig, "entry_zone", None) or getattr(sig, "entry_price", None))
            )
            if not valid:
                return False
        except Exception:
            return False

        _entry = getattr(sig, "entry_zone", None) or getattr(sig, "entry_price", None) or 0
        dk = (
            f"sig:{sig.symbol}:{sig.direction}:{round(float(_entry), 6)}"
            f":{datetime.now(timezone.utc).strftime('%Y-%m-%d:%H:%M')}"
        )

        try:
            from database import get_conn
            with get_conn() as conn:
                if conn.execute(
                    "SELECT 1 FROM telegram_messages WHERE dedupe_key=?", (dk,)
                ).fetchone():
                    return False
        except Exception:
            pass

        # Generate chart bytes
        photo_bytes = None
        try:
            from core.signal_visualizer import generate_chart_bytes
            client = _get_binance_client()
            if client:
                photo_bytes = generate_chart_bytes(
                    symbol=sig.symbol,
                    entry=float(_entry),
                    sl=float(sig.stop_loss),
                    tp1=float(sig.tp1) if sig.tp1 else None,
                    tp2=float(sig.tp2) if sig.tp2 else None,
                    tp3=float(sig.tp3) if sig.tp3 else None,
                    direction=sig.direction,
                    client=client
                )
        except Exception as e:
            logger.warning("[Telegram] Grafik oluşturulamadı: %s", e)

        msg = format_signal(sig)
        saved = _push_with_dedupe(msg, dk, symbol=sig.symbol,
                                  sig_id=str(getattr(sig, "id", "")),
                                  photo_bytes=photo_bytes)
        if saved:
            sig.telegram_status = "sent"
            logger.info("[Telegram] Sinyal gönderildi: %s %s %s RR=%.2f",
                        sig.symbol, sig.direction, sig.setup_quality,
                        getattr(sig, "rr", 0) or 0)
        return saved
    except Exception as e:
        logger.error("[Telegram] deliver_signal: %s", e)
        return False


def send_message(text: str, parse_mode: str = "HTML", reply_markup: Optional[dict] = None) -> bool:
    """Genel sistem mesajı — piyasa rejimi, uyarı, bilgi."""
    try:
        _queue.push(text, parse_mode=parse_mode, reply_markup=reply_markup)
        return True
    except Exception as e:
        logger.error("[Telegram] send_message: %s", e)
        return False


def send_voice(voice_bytes: bytes, caption: Optional[str] = None) -> bool:
    """Sends a voice message to Telegram."""
    token = config.TELEGRAM_BOT_TOKEN
    chat_id = config.TELEGRAM_CHAT_ID
    if not token or not chat_id:
        logger.debug("[Telegram] Token/chat_id boş — sesli mesaj atlandı.")
        return False
    try:
        url = f"https://api.telegram.org/bot{token}/sendVoice"
        files = {"voice": ("voice.ogg", voice_bytes, "audio/ogg")}
        payload = {"chat_id": chat_id}
        if caption:
            payload["caption"] = caption[:1024]
            payload["parse_mode"] = "HTML"
        resp = requests.post(url, data=payload, files=files, timeout=30)
        if resp.status_code == 200:
            return True
        logger.warning("[Telegram] sendVoice HTTP %d — %s", resp.status_code, resp.text[:100])
        return False
    except Exception as e:
        logger.error("[Telegram] send_voice hatası: %s", e)
        return False


def send_photo(photo_bytes: bytes, caption: str = "") -> bool:
    """Public wrapper to send a photo via Telegram."""
    try:
        ok, _ = _send_photo_raw(photo_bytes, caption)
        return ok
    except Exception as e:
        logger.error("[Telegram] send_photo: %s", e)
        return False


def send_veto_alert(sig_data: dict | Any, candidate_id: int) -> bool:
    """
    AI tarafından veto edilen veya watchlist'e alınan yüksek skorlu sinyal
    için Telegram'a interaktif butonlar içeren bir veto bildirimi gönderir.
    """
    try:
        def get_val(keys, default=None):
            if isinstance(sig_data, dict):
                for k in keys:
                    if k in sig_data:
                        return sig_data[k]
            else:
                for k in keys:
                    if hasattr(sig_data, k):
                        return getattr(sig_data, k)
            return default

        symbol = get_val(["symbol"], "?")
        direction = get_val(["direction", "side"], "LONG")
        quality = get_val(["setup_quality", "quality"], "B")
        entry = get_val(["entry_price", "entry"], 0.0)
        sl = get_val(["stop_loss", "sl", "stop"], 0.0)
        tp1 = get_val(["tp1"], 0.0)
        tp2 = get_val(["tp2"], 0.0)
        tp3 = get_val(["tp3"], 0.0)
        score = get_val(["final_score", "score", "ai_score"], 0.0)
        reason = get_val(["veto_reason", "reason", "reject_reason"], "AI Veto")
        lev = get_val(["leverage_suggestion", "leverage"], 10)
        risk = get_val(["risk_amount", "max_loss", "risk_usd"], 0.0)

        dir_icon = "▲" if direction == "LONG" else "▼"
        
        text = (
            f"🚫 <b>AI VETO / WATCHLIST UYARISI</b>\n"
            f"{LINE}\n"
            f"{dir_icon} <b>{symbol}</b> {direction} (Aday ID: {candidate_id})\n"
            f"{LINE}\n"
            f"📍 Giriş  <code>{_fmt(entry)}</code>\n"
            f"🛑 Stop   <code>{_fmt(sl)}</code>\n"
            f"🎯 TP1    <code>{_fmt(tp1)}</code>\n"
            f"🎯 TP2    <code>{_fmt(tp2)}</code>\n"
            f"🚀 TP3    <code>{_fmt(tp3)}</code>\n"
            f"{LINE}\n"
            f"📊 Skor   <b>{score:.1f}</b> · Kalite <b>{quality}</b>\n"
            f"💡 Sebep  <i>{reason}</i>\n"
            f"{LINE}\n"
            f"⏰ {_now_utc()}\n"
            f"\n<i>Bu sinyali manuel olarak açabilir veya coini sessize alabilirsiniz:</i>"
        )

        reply_markup = {
            "inline_keyboard": [
                [
                    {"text": "🚀 Force Trade", "callback_data": f"cmd:force:{candidate_id}"},
                    {"text": "🔕 Mute Coin (4h)", "callback_data": f"cmd:mute:{symbol}"}
                ]
            ]
        }

        # _queue'ya push et
        dk = f"veto:{symbol}:{candidate_id}:{datetime.now(timezone.utc).strftime('%Y%m%d%H%M')}"
        _queue.push(text, parse_mode="HTML", reply_markup=reply_markup, dedupe_key=dk, symbol=symbol)
        logger.info(f"[Telegram] Veto alert queued for candidate {candidate_id} ({symbol})")
        return True
    except Exception as e:
        logger.error(f"[Telegram] send_veto_alert hatası: {e}")
        return False


# ── Geriye dönük compat ───────────────────────────────────────────────────────
# Eski kod bu fonksiyonları import ediyor — imzaları korunuyor.

def format_trade_open(trade: dict) -> str:
    """Geriye dönük uyum — send_trade_open() kullan."""
    direction = trade.get("direction", "LONG")
    dir_icon  = "▲" if direction == "LONG" else "▼"
    return (
        f"✅ <b>İŞLEM AÇILDI</b>\n"
        f"{LINE}\n"
        f"{dir_icon} <b>{trade.get('symbol')}</b>  {direction}\n"
        f"📍 Giriş  <code>{_fmt(trade.get('entry', 0))}</code>\n"
        f"🛑 SL     <code>{_fmt(trade.get('sl', 0))}</code>\n"
        f"🎯 TP1    <code>{_fmt(trade.get('tp1', 0))}</code>\n"
        f"⚖️ RR     <b>{_fmt(trade.get('rr', 0), 2)}R</b>\n"
        f"⏰ {_now_utc()}"
    )


def format_trade_close(trade: dict, pnl: float, reason: str) -> str:
    """Geriye dönük uyum — send_trade_close() kullan."""
    reason_map = {
        "tp1": "TP1", "tp2": "TP2", "trail": "Trailing Stop",
        "sl": "Stop Loss", "timeout": "Zaman Aşımı",
    }
    icon = "🟢" if pnl > 0 else "🔴"
    return (
        f"{icon} <b>{'KAZANÇ' if pnl > 0 else 'ZARAR'}</b>\n"
        f"{LINE}\n"
        f"🪙 <b>{trade.get('symbol')}</b>  {trade.get('direction')}\n"
        f"📍 Giriş  <code>{_fmt(trade.get('entry', 0))}</code>\n"
        f"🏁 Çıkış  <code>{_fmt(trade.get('close_price', 0))}</code>\n"
        f"💰 PnL    <b>{'+' if pnl >= 0 else ''}{pnl:.3f}$</b>\n"
        f"📋 Sebep  {reason_map.get(reason, reason.upper() if reason else '?')}\n"
        f"⏰ {_now_utc()}"
    )


def send_heatmap(days: int = 30) -> bool:
    """Son 30 gündeki PnL dağılımını gösteren ısı haritasını çizip Telegram'a gönderir."""
    try:
        from core.signal_visualizer import generate_heatmap_image_bytes
        photo_bytes = generate_heatmap_image_bytes(days)
        if photo_bytes:
            _queue.push(f"📊 <b>Portföy Isı Haritası (Son {days} Gün)</b>", photo_bytes=photo_bytes)
            return True
        else:
            send_message(f"⚠️ Son {days} günde kapatılmış işlem bulunmadığından ısı haritası çizilemedi.")
            return False
    except Exception as e:
        logger.error(f"[Telegram] send_heatmap hatası: {e}")
        return False


def generate_weekly_report_card(stats: dict) -> bytes:
    """
    Pillow kullanarak premium, karanlık temalı haftalık özet kartı (800x450) üretir.
    """
    from PIL import Image, ImageDraw, ImageFont
    import io
    import os

    # Canvas boyutları
    width, height = 800, 450
    image = Image.new("RGBA", (width, height))
    draw = ImageDraw.Draw(image)

    # 1. Premium gradyan arka plan (Derin mor -> Derin mavi)
    for y in range(height):
        r = int(18 + (10 - 18) * (y / height))
        g = int(10 + (22 - 10) * (y / height))
        b = int(35 + (55 - 35) * (y / height))
        draw.line([(0, y), (width, y)], fill=(r, g, b, 255))

    # Altın rengi dış çerçeve
    draw.rectangle([10, 10, width - 10, height - 10], outline=(212, 168, 67, 80), width=2)
    # İç ince sınır çizgisi
    draw.rectangle([15, 15, width - 15, height - 15], outline=(255, 255, 255, 15), width=1)

    # Yazı tiplerini yükle
    font_large = None
    font_medium = None
    font_small = None
    font_title = None

    # Windows ve Linux ortak font yolları
    font_paths = [
        "C:\\Windows\\Fonts\\Outfit-Bold.ttf",
        "C:\\Windows\\Fonts\\Outfit-Regular.ttf",
        "C:\\Windows\\Fonts\\Inter-Bold.ttf",
        "C:\\Windows\\Fonts\\Inter-Regular.ttf",
        "C:\\Windows\\Fonts\\arialbd.ttf",
        "C:\\Windows\\Fonts\\arial.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    ]

    for path in font_paths:
        try:
            if os.path.exists(path):
                font_title = ImageFont.truetype(path, 32)
                font_large = ImageFont.truetype(path, 26)
                font_medium = ImageFont.truetype(path, 18)
                font_small = ImageFont.truetype(path, 12)
                break
        except Exception:
            continue

    if not font_large:
        font_title = ImageFont.load_default()
        font_large = ImageFont.load_default()
        font_medium = ImageFont.load_default()
        font_small = ImageFont.load_default()

    # Başlık Alanı
    draw.text((45, 45), "AURVEX AI", fill=(212, 168, 67, 255), font=font_title)
    draw.text((45, 85), "WEEKLY PERFORMANCE DIGEST", fill=(255, 255, 255, 220), font=font_medium)

    # Yatay çizgi ayırıcı
    draw.line([(45, 120), (width - 45, 120)], fill=(255, 255, 255, 30), width=1)

    # Değerleri al
    total_trades = stats.get("total_trades", 0)
    win_rate = stats.get("win_rate", 0.0)
    wins = stats.get("wins_count", 0)
    losses = stats.get("losses_count", 0)
    net_pnl = stats.get("net_pnl", 0.0)

    # Sol Sütun: Temel Özet
    draw.text((50, 145), "SUMMARY STATISTICS", fill=(212, 168, 67, 180), font=font_small)
    draw.text((50, 180), f"Executed Trades:  {total_trades}", fill=(255, 255, 255, 220), font=font_medium)
    draw.text((50, 215), f"Success Ratio:   %{win_rate:.1f} ({wins}W / {losses}L)", fill=(255, 255, 255, 220), font=font_medium)

    # Net PnL renklendirmesi (Kazanılınca Yeşil, kaybedilince Kırmızı)
    pnl_color = (0, 230, 118, 255) if net_pnl >= 0 else (239, 68, 68, 255)
    pnl_sign = "+" if net_pnl >= 0 else ""
    draw.text((50, 255), f"Net PnL:  {pnl_sign}${net_pnl:.2f}", fill=pnl_color, font=font_large)

    # Sağ Sütun: Varlık Detayları
    draw.text((450, 145), "ASSET PERFORMANCE", fill=(212, 168, 67, 180), font=font_small)

    best_coin = stats.get("best_coin", "Yok").replace("USDT", "")
    best_pnl = stats.get("best_pnl", 0.0)
    worst_coin = stats.get("worst_coin", "Yok").replace("USDT", "")
    worst_pnl = stats.get("worst_pnl", 0.0)

    best_sign = "+" if best_pnl >= 0 else ""

    draw.text((450, 180), f"🏆 Best Coin: {best_coin} ({best_sign}${best_pnl:.2f})", fill=(0, 230, 118, 220), font=font_medium)
    draw.text((450, 215), f"💀 Worst Coin: {worst_coin} (${worst_pnl:.2f})", fill=(239, 68, 68, 220), font=font_medium)

    # Alt Ayırıcı
    draw.line([(45, 315), (width - 45, 315)], fill=(255, 255, 255, 30), width=1)

    # Yapay Zeka Teşhis Değerleri
    avg_win_score = stats.get("avg_win_score", 0.0)
    avg_loss_score = stats.get("avg_loss_score", 0.0)

    draw.text((50, 335), "AI BRAIN DIAGNOSTICS", fill=(212, 168, 67, 180), font=font_small)
    draw.text((50, 365), f"Avg Score on Winners: {avg_win_score:.1f}p", fill=(255, 255, 255, 180), font=font_medium)
    draw.text((450, 365), f"Avg Score on Losers: {avg_loss_score:.1f}p", fill=(255, 255, 255, 180), font=font_medium)

    # Rapor alt bilgi (footer)
    draw.text((50, 410), "AurvexAI Intelligent Trading System - Automated Weekly Analytics", fill=(255, 255, 255, 100), font=font_small)

    # Byte stream olarak kaydet
    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    return buffer.getvalue()


def generate_weekly_digest() -> str:
    """Haftalık kâr/zarar performans raporunu oluşturur (Yazı sürümü)."""
    try:
        from database import get_conn
        from datetime import datetime, timezone, timedelta
        
        # Son 7 günde kapatılan işlemleri UTC zamanına göre çek
        seven_days_ago = (datetime.now(timezone.utc) - timedelta(days=7)).strftime("%Y-%m-%d %H:%M:%S")
        
        with get_conn() as conn:
            rows = conn.execute("""
                SELECT symbol, net_pnl, result, final_score 
                FROM trades 
                WHERE status = 'closed' AND close_time >= ?
            """, (seven_days_ago,)).fetchall()
            
        if not rows:
            return (
                "📊 <b>AurvexAI Haftalık Özet Raporu</b>\n"
                "──────────────────────\n"
                "Son 7 günde kapatılan herhangi bir işlem bulunmuyor."
            )
            
        total_trades = len(rows)
        wins = [t for t in rows if (t[1] or 0.0) > 0]
        losses = [t for t in rows if (t[1] or 0.0) <= 0]
        win_rate = (len(wins) / total_trades * 100) if total_trades > 0 else 0.0
        net_pnl = sum(float(t[1] or 0.0) for t in rows)
        
        # Coin bazlı performans
        coin_pnls = {}
        for t in rows:
            sym = t[0]
            coin_pnls[sym] = coin_pnls.get(sym, 0.0) + float(t[1] or 0.0)
            
        sorted_coins = sorted(coin_pnls.items(), key=lambda x: x[1])
        best_coin, best_pnl = sorted_coins[-1] if sorted_coins else ("Yok", 0.0)
        worst_coin, worst_pnl = sorted_coins[0] if sorted_coins else ("Yok", 0.0)
        
        # Yapay Zekâ ortalama skorları
        win_scores = [t[3] for t in wins if t[3] is not None]
        loss_scores = [t[3] for t in losses if t[3] is not None]
        avg_win_score = sum(win_scores) / len(win_scores) if win_scores else 0.0
        avg_loss_score = sum(loss_scores) / len(loss_scores) if loss_scores else 0.0
        
        emoji_pnl = "📈" if net_pnl >= 0 else "📉"
        pnl_sign = "+" if net_pnl >= 0 else ""
        
        msg = (
            f"📊 <b>AurvexAI Haftalık Performans Raporu</b>\n"
            f"<i>Son 7 günlük karne</i>\n"
            f"──────────────────────\n"
            f"🔄 <b>Toplam İşlem</b>: {total_trades}\n"
            f"🎯 <b>Başarı Oranı (Win Rate)</b>: %{win_rate:.1f} ({len(wins)}W / {len(losses)}L)\n"
            f"{emoji_pnl} <b>Net Kâr/Zarar (PnL)</b>: {pnl_sign}${net_pnl:.2f}\n"
            f"──────────────────────\n"
            f"🏆 <b>En Kârlı Coin</b>: {best_coin.replace('USDT', '')} ({pnl_sign}${best_pnl:.2f})\n"
            f"💀 <b>En Zararlı Coin</b>: {worst_coin.replace('USDT', '')} (${worst_pnl:.2f})\n"
            f"──────────────────────\n"
            f"🧠 <b>Yapay Zekâ Skorer Durumu</b>:\n"
            f"  • Kazanan Sinyal Ort. Skoru: {avg_win_score:.1f}\n"
            f"  • Kaybeden Sinyal Ort. Skoru: {avg_loss_score:.1f}\n"
            f"──────────────────────\n"
            f"<i>AurvexAI akıllı işlem motoru otomatik özet raporu.</i>"
        )
        return msg
    except Exception as e:
        logger.error(f"[Telegram] generate_weekly_digest hatası: {e}")
        return f"⚠️ Haftalık rapor hazırlanırken hata oluştu: {e}"


def send_weekly_digest() -> bool:
    """Haftalık özet raporunu hazırlar ve Telegram grubuna gönderir (PNG kart öncelikli)."""
    try:
        from database import get_conn
        from datetime import datetime, timezone, timedelta
        
        seven_days_ago = (datetime.now(timezone.utc) - timedelta(days=7)).strftime("%Y-%m-%d %H:%M:%S")
        
        with get_conn() as conn:
            rows = conn.execute("""
                SELECT symbol, net_pnl, result, final_score 
                FROM trades 
                WHERE status = 'closed' AND close_time >= ?
            """, (seven_days_ago,)).fetchall()
            
        if not rows:
            msg = (
                "📊 <b>AurvexAI Haftalık Özet Raporu</b>\n"
                "──────────────────────\n"
                "Son 7 günde kapatılan herhangi bir işlem bulunmuyor."
            )
            send_message(msg)
            return True
            
        total_trades = len(rows)
        wins = [t for t in rows if (t[1] or 0.0) > 0]
        losses = [t for t in rows if (t[1] or 0.0) <= 0]
        win_rate = (len(wins) / total_trades * 100) if total_trades > 0 else 0.0
        net_pnl = sum(float(t[1] or 0.0) for t in rows)
        
        coin_pnls = {}
        for t in rows:
            sym = t[0]
            coin_pnls[sym] = coin_pnls.get(sym, 0.0) + float(t[1] or 0.0)
            
        sorted_coins = sorted(coin_pnls.items(), key=lambda x: x[1])
        best_coin, best_pnl = sorted_coins[-1] if sorted_coins else ("Yok", 0.0)
        worst_coin, worst_pnl = sorted_coins[0] if sorted_coins else ("Yok", 0.0)
        
        win_scores = [t[3] for t in wins if t[3] is not None]
        loss_scores = [t[3] for t in losses if t[3] is not None]
        avg_win_score = sum(win_scores) / len(win_scores) if win_scores else 0.0
        avg_loss_score = sum(loss_scores) / len(loss_scores) if loss_scores else 0.0
        
        stats = {
            "total_trades": total_trades,
            "win_rate": win_rate,
            "wins_count": len(wins),
            "losses_count": len(losses),
            "net_pnl": net_pnl,
            "best_coin": best_coin,
            "best_pnl": best_pnl,
            "worst_coin": worst_coin,
            "worst_pnl": worst_pnl,
            "avg_win_score": avg_win_score,
            "avg_loss_score": avg_loss_score,
        }
        
        # PNG Görsel Rapor Oluşturmayı Dene
        photo_bytes = None
        try:
            photo_bytes = generate_weekly_report_card(stats)
        except Exception as e:
            logger.warning("[Telegram] Görsel özet oluşturulamadı: %s", e)
            
        caption = (
            "📊 <b>AurvexAI Haftalık Performans Raporu</b>\n"
            f"<i>Son 7 günlük karne</i>\n"
            "──────────────────────\n"
            f"🔄 <b>Toplam İşlem</b>: {total_trades}\n"
            f"🎯 <b>Başarı Oranı (Win Rate)</b>: %{win_rate:.1f} ({len(wins)}W / {len(losses)}L)\n"
            f"📈 <b>Net Kâr/Zarar (PnL)</b>: {'+' if net_pnl >= 0 else ''}${net_pnl:.2f}\n"
            "──────────────────────\n"
            "<i>AurvexAI akıllı işlem motoru otomatik özet raporu.</i>"
        )
        
        if photo_bytes:
            _push_with_dedupe(caption, f"weekly_photo:{datetime.now().strftime('%Y%m%d%H%M')}", photo_bytes=photo_bytes)
        else:
            msg = generate_weekly_digest()
            send_message(msg)
        return True
    except Exception as e:
        logger.error(f"[Telegram] send_weekly_digest hatası: {e}")
        return False
