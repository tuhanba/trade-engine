"""
telegram_manager.py — AX Telegram Komut Yöneticisi
====================================================
Bot'a Telegram üzerinden komut göndermeyi sağlar.
Komutlar: /status /pause /resume /stats /help
Token/chat_id eksikse crash olmaz, sadece log yazar.
"""
from __future__ import annotations
import logging
import threading
import time
from typing import Callable, Optional
import requests
import config

logger = logging.getLogger("ax.telegram_manager")

_POLL_URL = "https://api.telegram.org/bot{token}/getUpdates"
_TIMEOUT = 10


class TelegramManager:
    """Telegram polling ile komut dinleyici."""

    def __init__(self, send_fn: Callable[[str], bool]):
        self.send_fn = send_fn
        self.token = config.TELEGRAM_BOT_TOKEN
        self.chat_id = str(config.TELEGRAM_CHAT_ID)
        self.is_paused = False
        self._running = False
        self._thread: Optional[threading.Thread] = None
        self._last_update_id = 0
        self._start_time = time.time()

    def _is_configured(self) -> bool:
        return bool(self.token) and bool(self.chat_id)

    def start(self):
        if not self._is_configured():
            logger.warning("TelegramManager: token/chat_id eksik — komut dinleyici başlatılmadı")
            return
        self._running = True
        self._thread = threading.Thread(target=self._poll_loop, daemon=True, name="tg-manager")
        self._thread.start()
        logger.info("TelegramManager başlatıldı — komut dinleniyor")

    def stop(self):
        self._running = False

    def _poll_loop(self):
        while self._running:
            try:
                self._poll_once()
            except Exception as e:
                logger.debug("TelegramManager poll hatası: %s", e)
            time.sleep(3)

    def _poll_once(self):
        url = _POLL_URL.format(token=self.token)
        params = {"timeout": 5, "offset": self._last_update_id + 1, "limit": 10}
        try:
            resp = requests.get(url, params=params, timeout=_TIMEOUT)
            if resp.status_code != 200:
                return
            data = resp.json()
            if not data.get("ok"):
                return
            for update in data.get("result", []):
                uid = update.get("update_id", 0)
                if uid > self._last_update_id:
                    self._last_update_id = uid
                self._handle_update(update)
        except Exception as e:
            logger.debug("Poll isteği hatası: %s", e)

    def _handle_update(self, update: dict):
        msg = update.get("message") or update.get("channel_post") or {}
        text = (msg.get("text") or "").strip().lower()
        from_chat = str(msg.get("chat", {}).get("id", ""))
        if not text.startswith("/"):
            return
        if from_chat and from_chat != self.chat_id:
            logger.warning("Yetkisiz komut kaynağı: %s", from_chat)
            return
        cmd = text.split()[0]
        logger.info("Telegram komutu alındı: %s", cmd)
        if cmd == "/pause":
            self.is_paused = True
            self.send_fn("⏸ <b>Bot duraklatıldı.</b>\nYeni sinyal üretilmeyecek. /resume ile devam et.")
        elif cmd == "/resume":
            self.is_paused = False
            self.send_fn("▶️ <b>Bot devam ediyor.</b>")
        elif cmd == "/status":
            self._send_status()
        elif cmd == "/stats":
            self._send_stats()
        elif cmd == "/help":
            self.send_fn(
                "📖 <b>Komut Listesi</b>\n"
                "/status — Bot durumu\n"
                "/stats — İstatistikler\n"
                "/pause — Botu duraklat\n"
                "/resume — Botu devam ettir\n"
                "/help — Bu mesaj"
            )

    def _send_status(self):
        try:
            import database
            bal = database.get_paper_balance() or 0
            open_t = len(database.get_open_trades())
            uptime_sec = int(time.time() - self._start_time)
            h, rem = divmod(uptime_sec, 3600)
            m = rem // 60
            paused_txt = "⏸ DURAKLATILDI" if self.is_paused else "✅ Aktif"
            mode = config.EXECUTION_MODE.upper()
            self.send_fn(
                f"🤖 <b>AurvexAI Durum</b>\n"
                f"Mod: {mode}\n"
                f"Durum: {paused_txt}\n"
                f"Bakiye: ${bal:.2f}\n"
                f"Açık trade: {open_t}\n"
                f"Uptime: {h}s {m}dk"
            )
        except Exception as e:
            self.send_fn(f"⚠️ Status hatası: {e}")

    def _send_stats(self):
        try:
            import database
            stats = database.get_dashboard_stats()
            total = stats.get("total_trades", 0)
            wins = stats.get("win_trades", 0)
            losses = stats.get("loss_trades", 0)
            pnl = stats.get("total_pnl", 0)
            wr = stats.get("win_rate", 0)
            bal = database.get_paper_balance() or 0
            self.send_fn(
                f"📊 <b>AurvexAI İstatistikler</b>\n"
                f"Toplam trade: {total}\n"
                f"Kazanç/Kayıp: {wins}W / {losses}L\n"
                f"Winrate: {wr:.1f}%\n"
                f"Toplam PnL: ${pnl:.2f}\n"
                f"Bakiye: ${bal:.2f}"
            )
        except Exception as e:
            self.send_fn(f"⚠️ Stats hatası: {e}")
