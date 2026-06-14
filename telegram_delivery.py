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
import os
from collections import deque
from datetime import datetime, timezone
from typing import Any, Optional

import requests
import config

try:
    from database import save_telegram_message
except Exception:
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
        mode = getattr(config, "EXECUTION_MODE", "paper")
    except Exception:
        mode = "paper"
    return "🔴 LIVE" if mode == "live" else "PAPER"


LINE = "─" * 22


# ═══════════════════════════════════════════════════════════════════════════════
# FAZ 5 — MERKEZİ MESAJ ŞABLONLARI ("Cep Komuta Hattı")
# İlke: Her mesaj 3 saniyede taranabilir. Sabit görsel imza + SABİT sayı formatı.
# Para $1,234.50 · Yüzde +1.57% · R +1.27R — tutarsızlık YASAK.
# ═══════════════════════════════════════════════════════════════════════════════

def fmt_money(v) -> str:
    """Para: $1,234.50 (işaretsiz — bakiye/risk için)."""
    try:
        v = float(v or 0)
        return ("-" if v < 0 else "") + f"${abs(v):,.2f}"
    except Exception:
        return "$0.00"


def fmt_money_signed(v) -> str:
    """İşaretli para: +$31.40 / -$10.00 (PnL için)."""
    try:
        v = float(v or 0)
        return ("+" if v >= 0 else "-") + f"${abs(v):,.2f}"
    except Exception:
        return "+$0.00"


def fmt_pct(v) -> str:
    """Yüzde: +1.57% / -1.58%."""
    try:
        v = float(v or 0)
        return f"{'+' if v >= 0 else ''}{v:.2f}%"
    except Exception:
        return "0.00%"


def fmt_r(v) -> str:
    """R-multiple: +1.27R / -1.00R."""
    try:
        v = float(v or 0)
        return f"{'+' if v >= 0 else ''}{v:.2f}R"
    except Exception:
        return "0.00R"


def fmt_price(v) -> str:
    """Fiyat: büyüklüğe göre uyarlanır (BTC 60000.0 · SOL 142.350 · SHIB 0.00002431)."""
    try:
        v = float(v or 0)
        if v == 0:
            return "0"
        a = abs(v)
        if a >= 1000:
            return f"{v:,.2f}"
        if a >= 1:
            return f"{v:.3f}"
        if a >= 0.01:
            return f"{v:.5f}"
        return f"{v:.8f}".rstrip("0").rstrip(".")
    except Exception:
        return str(v)


def _short_regime(regime: str) -> str:
    """Rejimi kısa ok'lu etikete çevirir: TRENDING_HIGH_VOL → TREND↑, CHOPPY → CHOP."""
    r = str(regime or "").upper()
    if "TRENDING" in r:
        return "TREND↑" if "HIGH" in r else "TREND"
    if "CHOPPY" in r:
        return "CHOP↑" if "HIGH" in r else "CHOP"
    if "BULL" in r:
        return "BULL↑"
    if "BEAR" in r:
        return "BEAR↓"
    return r or "NEUTRAL"


def _r_at_target(entry: float, sl: float, target: float, direction: str) -> Optional[float]:
    """Bir hedefteki R-multiple = (hedef mesafesi) / (stop mesafesi)."""
    try:
        risk = abs(entry - sl)
        if risk <= 0 or target <= 0 or entry <= 0:
            return None
        if str(direction).upper() == "LONG":
            return (target - entry) / risk
        return (entry - target) / risk
    except Exception:
        return None


def _signed_pct(entry: float, target: float, direction: str) -> str:
    """Girişten hedefe yön-duyarlı yüzde, +1.57% formatında (parantezsiz)."""
    try:
        if entry <= 0 or target <= 0:
            return ""
        diff = (target - entry) / entry * 100
        if str(direction).upper() == "SHORT":
            diff = -diff
        return fmt_pct(diff)
    except Exception:
        return ""


def tpl_trade_open(symbol: str, direction: str, leverage, entry: float, sl: float,
                   tp1: float, tp2: float, risk_usd: float, risk_pct: float,
                   score: float, regime: Optional[str] = None,
                   ghost_wr: Optional[float] = None, ghost_n: Optional[int] = None) -> str:
    """Trade açılış şablonu (Faz 5.1) — sabit satır düzeni."""
    direction = str(direction).upper()
    side_icon = "🟢" if direction == "LONG" else "🔵"  # LONG altın-yeşil, SHORT gümüş-mavi
    lev_str = f"x{leverage}" if leverage not in (None, "", "?") else ""

    sl_pct = _signed_pct(entry, sl, direction)
    tp1_pct = _signed_pct(entry, tp1, direction)
    tp2_pct = _signed_pct(entry, tp2, direction)
    tp1_r = _r_at_target(entry, sl, tp1, direction)
    tp2_r = _r_at_target(entry, sl, tp2, direction)

    lines = [
        f"{side_icon} <b>{direction} • {symbol}</b>{('  ' + lev_str) if lev_str else ''}",
        LINE,
        f"Giriş   <code>{fmt_price(entry)}</code>",
    ]
    if sl > 0:
        lines.append(f"Stop    <code>{fmt_price(sl)}</code>   ({sl_pct})")
    if tp1 > 0:
        r_part = f"  R {tp1_r:.2f}" if tp1_r is not None else ""
        lines.append(f"TP1     <code>{fmt_price(tp1)}</code>   ({tp1_pct}){r_part}")
    if tp2 > 0:
        r_part = f"  R {tp2_r:.2f}" if tp2_r is not None else ""
        lines.append(f"TP2     <code>{fmt_price(tp2)}</code>   ({tp2_pct}){r_part}")
    lines.append(LINE)

    info = f"Risk {fmt_money(risk_usd)} ({risk_pct:.1f}%) • Skor {score:.0f}"
    if regime:
        info += f" • Rejim {regime}"
    lines.append(info)
    if ghost_wr is not None:
        n_part = f" (n={ghost_n})" if ghost_n is not None else ""
        lines.append(f"👻 Ghost WR(setup): {ghost_wr:.0f}%{n_part}")
    return "\n".join(lines)


def tpl_trade_close(symbol: str, direction: str, net_pnl: float, r_multiple: float,
                    duration_str: str, reason: str, balance_after: float,
                    today_wins: Optional[int] = None, today_losses: Optional[int] = None,
                    today_pnl: Optional[float] = None, expectancy_r: Optional[float] = None) -> str:
    """Trade kapanış şablonu (Faz 5.1) — en kritik mesaj. Kutlama/teselli tonu,
    format DEĞİŞMEZ; sadece ikon/başlık değişir."""
    direction = str(direction).upper()
    if net_pnl > 0:
        head = f"✅ <b>KAZANÇ</b> • {symbol} {direction}"
    elif net_pnl < 0:
        head = f"🔻 <b>ZARAR</b> • {symbol} {direction}"
    else:
        head = f"⚖️ <b>BAŞA BAŞ</b> • {symbol} {direction}"

    reason_map = {
        "sl": "Stop Loss", "tp1": "TP1", "tp2": "TP2", "tp3": "TP3",
        "manual": "Manuel", "timeout": "Süre Doldu", "max_hold_timeout": "Max Süre",
        "trail": "Trailing", "runner": "Runner", "breakeven": "Breakeven", "finish": "Finish",
    }
    reason_label = reason_map.get(str(reason).lower(), str(reason).upper())

    pnl_line = f"PnL    {fmt_money_signed(net_pnl)}  ({fmt_r(r_multiple)})"
    bal_line = f"Bakiye {fmt_money(balance_after)}"
    if today_pnl is not None:
        bal_line += f"  (gün: {fmt_money_signed(today_pnl)})"

    lines = [head, LINE, pnl_line, f"Süre   {duration_str or '—'} • Sebep: {reason_label}", bal_line, LINE]

    # Bugünkü performans + 30g expectancy
    footer_parts = []
    if today_wins is not None and today_losses is not None:
        footer_parts.append(f"Bugün: {today_wins}W-{today_losses}L")
    if expectancy_r is not None:
        footer_parts.append(f"E(30g): {fmt_r(expectancy_r)}")
    if footer_parts:
        lines.append("📊 " + " • ".join(footer_parts))
    return "\n".join(lines)


def tpl_anomaly(title: str, body_lines: list, suggestion: Optional[str] = None,
                footer: str = "/teshis ile tam rapor") -> str:
    """Anomali/Kritik şablonu (Faz 5.1) — sabit görsel imza."""
    lines = [f"🚨 <b>ANOMALİ • {title}</b>"]
    lines.extend(body_lines)
    if suggestion:
        lines.append(f"Öneri: {suggestion}")
    if footer:
        lines.append(footer)
    return "\n".join(lines)


def _today_perf(environment: Optional[str] = None) -> dict:
    """Bugünün W-L ve net PnL'i + 30g expectancy (kapanış şablonu footer'ı için)."""
    out = {"wins": None, "losses": None, "pnl": None, "expectancy_r": None}
    try:
        import database
        from datetime import datetime as _dt, timezone as _tz
        env = environment or getattr(config, "EXECUTION_MODE", "paper")
        today = _dt.now(_tz.utc).strftime("%Y-%m-%d")
        with database.get_conn() as conn:
            row = conn.execute(
                "SELECT SUM(CASE WHEN net_pnl>0 THEN 1 ELSE 0 END), "
                "SUM(CASE WHEN net_pnl<=0 THEN 1 ELSE 0 END), COALESCE(SUM(net_pnl),0) "
                "FROM trades WHERE DATE(close_time)=? AND status='closed' "
                "AND COALESCE(is_valid_for_stats,1)=1 AND environment=?",
                (today, env),
            ).fetchone()
        out["wins"] = int(row[0] or 0)
        out["losses"] = int(row[1] or 0)
        out["pnl"] = round(float(row[2] or 0), 2)
    except Exception:
        pass
    try:
        from core.accounting import calculate_expectancy
        exp = calculate_expectancy(days=30, environment=environment)
        if exp.get("n", 0) > 0:
            out["expectancy_r"] = exp["expectancy_r"]
    except Exception:
        pass
    return out




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
        risk_pct = float(data.get("risk_pct", 0) or 0)
        score = float(data.get("final_score", 0) or 0)

        # Rejim — kısa ok'lu etiket
        regime = data.get("market_regime") or None
        if not regime:
            try:
                import database
                regime = database.get_market_regime()
            except Exception:
                regime = None
        if regime:
            regime = _short_regime(regime)

        # NEDEN (Faz 5.1): Merkezi şablon — tüm açılış mesajları aynı görsel imza.
        text = tpl_trade_open(
            symbol=symbol, direction=direction, leverage=lev, entry=entry, sl=sl,
            tp1=tp1, tp2=tp2, risk_usd=risk, risk_pct=risk_pct, score=score,
            regime=regime, ghost_wr=data.get("ghost_wr"), ghost_n=data.get("ghost_n"),
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
        # NEDEN (Faz 5.1): Merkezi şablon — bugünkü W-L + 30g expectancy footer.
        perf = _today_perf()
        text = tpl_trade_close(
            symbol=symbol, direction=direction, net_pnl=net_pnl, r_multiple=r_multiple,
            duration_str=duration_str, reason=reason, balance_after=balance_after,
            today_wins=perf.get("wins"), today_losses=perf.get("losses"),
            today_pnl=perf.get("pnl"), expectancy_r=perf.get("expectancy_r"),
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
    if not getattr(config, "FRIDAY_VOICE_REPORTS_ENABLED", False):
        logger.debug("[Telegram] Sesli rapor devre dışı — atlanıyor.")
        return False
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


def send_document(file_path: str, caption: str = "") -> bool:
    """Bir dosyayı Telegram'a sendDocument ile gönderir (Faz 6.1 — Trade Journal)."""
    token = config.TELEGRAM_BOT_TOKEN
    chat_id = config.TELEGRAM_CHAT_ID
    if not token or not chat_id:
        logger.debug("[Telegram] Token/chat_id boş — döküman atlandı.")
        return False
    if not os.path.exists(file_path):
        logger.warning("[Telegram] Döküman bulunamadı: %s", file_path)
        return False
    try:
        url = f"https://api.telegram.org/bot{token}/sendDocument"
        # Çoklu chat_id desteği — ilkine gönder
        cid = str(chat_id).split(",")[0].strip().strip('"').strip("'")
        with open(file_path, "rb") as f:
            files = {"document": (os.path.basename(file_path), f)}
            data = {"chat_id": cid, "caption": caption[:1024], "parse_mode": "HTML"}
            resp = requests.post(url, data=data, files=files, timeout=30)
        if resp.status_code == 200:
            return True
        logger.warning("[Telegram] sendDocument HTTP %d — %s", resp.status_code, resp.text[:100])
        return False
    except Exception as e:
        logger.error("[Telegram] send_document: %s", e)
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
