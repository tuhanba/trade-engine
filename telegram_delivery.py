"""
Telegram Delivery — AX Scalp Engine v4.3
=========================================
Tüm PnL değerleri core/accounting modülünden gelir.
Açılış, TP hit ve kapanış mesajları aynı net_pnl mantığını kullanır.
Duplicate engeli, kuyruk sistemi ve retry koruması mevcut.
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

# ─── Duplicate Engeli ─────────────────────────────────────────────────────────
_sent_hashes: set = set()
_sent_lock = threading.Lock()

# ─── Token/Chat Yardımcıları ──────────────────────────────────────────────────
def _token():
    return os.getenv("TELEGRAM_BOT_TOKEN", TELEGRAM_BOT_TOKEN)

def _chat():
    return os.getenv("TELEGRAM_CHAT_ID", TELEGRAM_CHAT_ID)

# ─── Ham Gönderim ─────────────────────────────────────────────────────────────
def _send_raw(text: str, parse_mode: str = "HTML", retries: int = 3) -> bool:
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
                timeout=10,
            )
            if resp.status_code == 200:
                return True
        except Exception:
            pass
        time.sleep(1)
    return False

# ─── Mesaj Kuyruğu ────────────────────────────────────────────────────────────
class _Queue:
    def __init__(self):
        self._q      = deque()
        self._lock   = threading.Lock()
        self._event  = threading.Event()
        self._thread = threading.Thread(target=self._worker, daemon=True)
        self._thread.start()

    def push(self, text: str):
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

# ─── Yardımcı Formatlayıcılar ─────────────────────────────────────────────────
def _pnl_str(pnl: float) -> str:
    sign = "+" if pnl >= 0 else ""
    return f"{sign}{pnl:.3f}$"

def _ts() -> str:
    return datetime.now(timezone.utc).strftime("%H:%M UTC")

# ─── Sinyal Formatı ───────────────────────────────────────────────────────────
def format_signal(sig) -> str:
    dir_emoji = "📈 LONG" if sig.direction == "LONG" else "📉 SHORT"
    conf_pct  = int((sig.confidence or 0.8) * 100)
    conf_bar  = "🟢" * (conf_pct // 20) + "⚪" * (5 - conf_pct // 20)
    tp2_line  = f"🎯 TP2:    <code>{sig.tp2:.6f}</code>\n" if sig.tp2 else ""
    tp3_line  = f"🎯 TP3:    <code>{sig.tp3:.6f}</code>\n" if sig.tp3 else ""
    lev = getattr(sig, "leverage", None) or getattr(sig, "leverage_suggestion", 10) or 10
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
        f"⏰ {_ts()}"
    )

def deliver_signal(sig):
    _queue.push(format_signal(sig))

# ─── Trade Açılış Mesajı ──────────────────────────────────────────────────────
def send_trade_open(trade: dict):
    """
    Trade açılışında gönderilir.
    Tüm alanlar execution_engine.py'nin save_trade dict'inden gelir.
    margin_used, open_fee, max_loss_usd, risk_usd alanlarını kullanır.
    """
    try:
        symbol    = trade.get("symbol", "?")
        direction = (trade.get("direction") or "LONG").upper()
        leverage  = int(trade.get("leverage") or 10)
        entry     = float(trade.get("entry") or 0)
        sl        = float(trade.get("sl") or 0)
        tp1       = float(trade.get("tp1") or 0)
        tp2       = float(trade.get("tp2") or 0)
        tp3       = float(trade.get("tp3") or 0)
        notional  = float(trade.get("notional_size") or 0)
        margin    = float(trade.get("margin_used") or trade.get("position_size") or 0)
        risk_usd  = float(trade.get("risk_usd") or 0)
        max_loss  = float(trade.get("max_loss_usd") or 0)
        open_fee  = float(trade.get("open_fee") or 0)
        quality   = trade.get("setup_quality") or "?"
        score     = float(trade.get("score") or 0)

        sl_dist_pct  = abs(entry - sl) / entry * 100 if entry else 0
        tp1_dist_pct = abs(tp1 - entry) / entry * 100 if entry else 0
        dir_emoji    = "🟢" if direction == "LONG" else "🔴"

        tp2_line = f"🎯 TP2:    <code>{tp2:.6f}</code>\n" if tp2 else ""
        tp3_line = f"🎯 TP3:    <code>{tp3:.6f}</code>\n" if tp3 else ""

        msg = (
            f"{dir_emoji} <b>TRADE AÇILDI</b>\n"
            f"━━━━━━━━━━━━━━━━━━━━━\n"
            f"🪙 <b>{symbol}</b> | {direction} | ⚡ {leverage}x\n"
            f"🏆 Kalite: <code>{quality}</code> | Skor: <code>{score:.0f}</code>\n"
            f"━━━━━━━━━━━━━━━━━━━━━\n"
            f"📍 Entry:  <code>{entry:.6f}</code>\n"
            f"🛑 Stop:   <code>{sl:.6f}</code>  ({sl_dist_pct:.2f}%)\n"
            f"🎯 TP1:    <code>{tp1:.6f}</code>  (+{tp1_dist_pct:.2f}%)\n"
            f"{tp2_line}"
            f"{tp3_line}"
            f"━━━━━━━━━━━━━━━━━━━━━\n"
            f"📦 Notional:    <code>{notional:.2f}$</code>\n"
            f"💳 Teminat:     <code>{margin:.2f}$</code>\n"
            f"⚠️ Risk:        <code>{risk_usd:.2f}$</code>\n"
            f"🔻 Max Zarar:   <code>{max_loss:.2f}$</code>\n"
            f"💸 Açılış Fee:  <code>{open_fee:.4f}$</code>\n"
            f"⏰ {_ts()}"
        )
        _queue.push(msg)
    except Exception as e:
        logger.error(f"[Telegram] send_trade_open hatası: {e}")

# ─── Trade Kapanış Mesajı ─────────────────────────────────────────────────────
def send_trade_close(trade: dict, pnl: float, reason: str,
                     gross_pnl: float = 0, total_fee: float = 0,
                     r_multiple: float = 0, hold_minutes: int = 0):
    """
    Trade kapanışında gönderilir.
    pnl: accounting.calculate_close_pnl'den gelen net_pnl (DB'ye yazılan ile aynı).
    """
    try:
        symbol    = trade.get("symbol", "?")
        direction = (trade.get("direction") or "LONG").upper()
        result    = "WIN 🟢" if pnl > 0 else "LOSS 🔴"

        reason_map = {
            "sl":      "Stop Loss",
            "tp3":     "TP3 Hedef",
            "trail":   "Trailing Stop",
            "timeout": "Timeout",
            "manual":  "Manuel",
        }
        reason_tr = reason_map.get(reason, reason.upper())

        hold_str = f"{hold_minutes // 60}s {hold_minutes % 60}dk" if hold_minutes >= 60 \
            else f"{hold_minutes}dk"

        gross_line = f"📈 Brüt PnL:   <code>{_pnl_str(gross_pnl)}</code>\n" if gross_pnl else ""
        fee_line   = f"💸 Toplam Fee: <code>-{total_fee:.4f}$</code>\n" if total_fee else ""
        r_line     = f"📐 R-Multiple: <code>{r_multiple:+.2f}R</code>\n" if r_multiple else ""

        msg = (
            f"{'🟢' if pnl > 0 else '🔴'} <b>TRADE KAPANDI — {result}</b>\n"
            f"━━━━━━━━━━━━━━━━━━━━━\n"
            f"🪙 <b>{symbol}</b> | {direction}\n"
            f"📌 Neden: <b>{reason_tr}</b>\n"
            f"━━━━━━━━━━━━━━━━━━━━━\n"
            f"{gross_line}"
            f"{fee_line}"
            f"💰 Net PnL:    <b>{_pnl_str(pnl)}</b>\n"
            f"{r_line}"
            f"⏱️ Süre: <code>{hold_str}</code>\n"
            f"⏰ {_ts()}"
        )
        _queue.push(msg)
    except Exception as e:
        logger.error(f"[Telegram] send_trade_close hatası: {e}")

# ─── TP Hit Mesajı ────────────────────────────────────────────────────────────
def send_tp_hit(symbol: str, direction: str, tp_level: int,
                pnl: float, remaining_pct: float = None,
                new_sl: float = None, next_target: str = None):
    """
    TP1 veya TP2 vurulduğunda gönderilir.
    pnl: sadece bu kısmi kapanışın net PnL'i.
    """
    try:
        stage_map = {
            1: "→ Breakeven SL aktif, TP2 bekleniyor",
            2: "→ Runner aktif, trailing stop başladı",
            3: "→ Trade tamamen kapandı",
        }
        stage_note = stage_map.get(tp_level, "")
        dir_emoji  = "🟢" if direction.upper() == "LONG" else "🔴"

        rem_line = f"📊 Kalan Pozisyon: <code>%{remaining_pct:.0f}</code>\n" \
            if remaining_pct is not None else ""
        sl_line  = f"🛡️ Yeni SL (Breakeven): <code>{new_sl:.6f}</code>\n" \
            if new_sl else ""
        next_line = f"➡️ Sonraki Hedef: <b>{next_target}</b>\n" \
            if next_target else ""

        msg = (
            f"🎯 <b>TP{tp_level} HIT</b>\n"
            f"🪙 <b>{symbol}</b> | {direction.upper()} {dir_emoji}\n"
            f"💰 Kısmi Net PnL: <b>{_pnl_str(pnl)}</b>\n"
            f"{rem_line}"
            f"{sl_line}"
            f"{next_line}"
            f"📌 {stage_note}\n"
            f"⏰ {_ts()}"
        )
        _queue.push(msg)
    except Exception as e:
        logger.error(f"[Telegram] send_tp_hit hatası: {e}")

# ─── SL Hit ──────────────────────────────────────────────────────────────────
def send_sl_hit(symbol: str, direction: str, pnl: float, r_multiple: float = 0):
    try:
        r_str = f"  ({r_multiple:.2f}R)" if r_multiple else ""
        msg = (
            f"🛑 <b>STOP LOSS</b>\n"
            f"🪙 <b>{symbol}</b> | {direction.upper()}\n"
            f"💰 {_pnl_str(pnl)}{r_str}\n"
            f"⏰ {_ts()}"
        )
        _queue.push(msg)
    except Exception as e:
        logger.error(f"[Telegram] send_sl_hit hatası: {e}")

# ─── Genel Mesajlar ───────────────────────────────────────────────────────────
def send_message(text: str):
    _queue.push(text)

def send_info(message: str):
    _queue.push(f"ℹ️ {message}")

def send_alert(message: str):
    _queue.push(f"⚠️ <b>UYARI</b>\n{message}")

def send_error(message: str):
    _queue.push(f"🚨 <b>HATA</b>\n{message}")
