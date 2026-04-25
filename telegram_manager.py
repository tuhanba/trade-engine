"""
telegram_manager.py — AX Telegram Yöneticisi
=============================================
• Mesaj gönderme (retry + kuyruk)
• Komut alma (long-polling)
• /status /pause /devam /bitir /bakiye /trades /ax /rapor
• Drawdown uyarısı, finish modu, pause modu
"""

import os
import threading
import time
import logging
import requests
from datetime import datetime, timezone
from collections import deque

logger = logging.getLogger(__name__)

# Token'lar modül yüklenirken değil, kullanılırken okunur (load_dotenv sonrası)
def _get_token():
    return os.getenv("TELEGRAM_BOT_TOKEN", "")

def _get_chat():
    return os.getenv("TELEGRAM_CHAT_ID", "")


# ─────────────────────────────────────────────────────────────────
# DÜŞÜK SEVİYE: retry'lı gönderici
# ─────────────────────────────────────────────────────────────────

def _send_raw(text, parse_mode="HTML", retries=3):
    """Telegram'a doğrudan HTTP POST — en fazla `retries` kez dener."""
    token = _get_token()
    chat  = _get_chat()
    if not token or not chat:
        logger.warning("Telegram: TOKEN veya CHAT_ID eksik, mesaj gönderilemedi.")
        return False
    api_base = f"https://api.telegram.org/bot{token}"
    for attempt in range(1, retries + 1):
        try:
            resp = requests.post(
                f"{api_base}/sendMessage",
                json={
                    "chat_id":    chat,
                    "text":       text[:4096],
                    "parse_mode": parse_mode,
                },
                timeout=8,
            )
            if resp.status_code == 200:
                return True
            if resp.status_code == 429:
                retry_after = resp.json().get("parameters", {}).get("retry_after", 5)
                logger.warning(f"Telegram rate limit — {retry_after}s bekleniyor")
                time.sleep(retry_after)
                continue
            logger.warning(f"Telegram API hata {resp.status_code}: {resp.text[:200]}")
        except requests.exceptions.Timeout:
            logger.warning(f"Telegram timeout (deneme {attempt}/{retries})")
        except Exception as e:
            logger.warning(f"Telegram gönderim hatası: {e}")
        if attempt < retries:
            time.sleep(2 ** attempt)
    return False


# ─────────────────────────────────────────────────────────────────
# KUYRUK: ana thread'i bloke etmeden gönder
# ─────────────────────────────────────────────────────────────────

class _SendQueue:
    def __init__(self):
        self._q = deque()
        self._lock = threading.Lock()
        self._event = threading.Event()
        self._thread = threading.Thread(target=self._worker, daemon=True)
        self._thread.start()

    def push(self, text, parse_mode="HTML"):
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
                    text, pm = self._q.popleft()
                _send_raw(text, pm)
                time.sleep(0.05)


_queue = _SendQueue()


# ─────────────────────────────────────────────────────────────────
# KOMUT POLLING
# ─────────────────────────────────────────────────────────────────

class TelegramManager:
    """
    Kullanım:
        tg = TelegramManager(binance_client)
        tg.paper_mode = True
        tg.start()          # polling thread başlar
        tg.send("mesaj")
    """

    def __init__(self, client=None):
        self.client     = client
        self.paper_mode = True
        self.paused     = False
        self.finish_mode = False
        self._offset    = 0
        self._poll_thread = None
        self._handlers  = {}   # komut → callable
        self._get_balance_fn = None
        self._get_open_trades_fn = None
        self._run_ai_brain_fn = None
        self._get_circuit_breaker_fn = None

    @property
    def is_paused(self):
        return self.paused

    @property
    def is_finish_mode(self):
        return self.finish_mode

    # ── public API ──────────────────────────────────────────────

    def send(self, text, parse_mode="HTML"):
        token = _get_token()
        chat  = _get_chat()
        if not token or not chat:
            logger.warning("Telegram: TOKEN veya CHAT_ID eksik, mesaj gönderilemedi.")
            return
        prefix = "🧪 <b>[PAPER]</b> " if self.paper_mode else ""
        _queue.push(prefix + text, parse_mode)

    def register(self, command: str, handler):
        """Komut handler kaydet. handler(args: str) şeklinde çağrılır."""
        self._handlers[command.lstrip("/")] = handler

    def start(self, get_balance_fn=None, get_open_trades_fn=None,
              run_ai_brain_fn=None, get_circuit_breaker_fn=None):
        """Polling thread'i başlat. Opsiyonel callback'ler kaydedilir."""
        if get_balance_fn:
            self._get_balance_fn = get_balance_fn
        if get_open_trades_fn:
            self._get_open_trades_fn = get_open_trades_fn
        if run_ai_brain_fn:
            self._run_ai_brain_fn = run_ai_brain_fn
        if get_circuit_breaker_fn:
            self._get_circuit_breaker_fn = get_circuit_breaker_fn
        if self._poll_thread and self._poll_thread.is_alive():
            return
        self._poll_thread = threading.Thread(target=self._poll_loop, daemon=True)
        self._poll_thread.start()
        logger.info("TelegramManager başlatıldı.")

    def stop(self):
        pass   # daemon thread otomatik kapanır

    def notify_finish_complete(self, balance):
        """Finish modu tamamlandı, tüm trade'ler kapandı."""
        self.send(
            f"🏁 <b>Finish Modu Tamamlandı</b>\n"
            f"💰 Son Bakiye: ${balance:.2f}\n"
            f"⏰ {datetime.now(timezone.utc).strftime('%H:%M UTC')}"
        )

    def send_startup(self, balance, params, symbol_count, ai_available):
        """Bot başlangıç mesajı gönder."""
        ai_str = "✅ Aktif" if ai_available else "❌ Pasif"
        self.send(
            f"🟢 <b>AURVEX BOT AKTİF</b>\n"
            f"⏰ {datetime.now(timezone.utc).strftime('%H:%M:%S UTC')}\n\n"
            f"💰 Bakiye: ${balance:.2f}\n"
            f"🕐 7/24 tarama aktif\n"
            f"✅ {symbol_count} sembol\n"
            f"🧠 AI Brain: {ai_str}\n"
            f"⛔ Devre Kesici: 5 kayıp = 120dk"
        )

    # ── polling ─────────────────────────────────────────────────

    def _poll_loop(self):
        while True:
            try:
                self._fetch_updates()
            except Exception as e:
                logger.debug(f"Telegram poll hata: {e}")
            time.sleep(1)

    def _fetch_updates(self):
        token = _get_token()
        chat  = _get_chat()
        if not token or not chat:
            time.sleep(5)
            return
        api_base = f"https://api.telegram.org/bot{token}"
        resp = requests.get(
            f"{api_base}/getUpdates",
            params={"offset": self._offset, "timeout": 10, "limit": 10},
            timeout=15,
        )
        if resp.status_code != 200:
            return
        data = resp.json()
        for upd in data.get("result", []):
            self._offset = upd["update_id"] + 1
            msg = upd.get("message") or upd.get("edited_message")
            if not msg:
                continue
            # sadece kendi chat'imizden gelen komutları işle
            if str(msg.get("chat", {}).get("id", "")) != str(chat):
                continue
            text = msg.get("text", "").strip()
            if not text.startswith("/"):
                continue
            parts   = text.split(None, 1)
            cmd     = parts[0].lstrip("/").split("@")[0].lower()
            args    = parts[1] if len(parts) > 1 else ""
            self._dispatch(cmd, args)

    def _dispatch(self, cmd, args):
        # built-in komutlar
        if cmd == "status":
            self._cmd_status()
        elif cmd in ("pause", "duraklat"):
            self.paused = True
            self.send("⏸ Bot duraklatıldı. Devam için /devam")
        elif cmd in ("devam", "resume"):
            self.paused = False
            self.send("▶️ Bot devam ediyor.")
        elif cmd in ("bitir", "finish"):
            self.finish_mode = True
            self.send("🏁 Finish modu aktif — açık trade'ler kapanınca bot durur.")
        elif cmd in ("bakiye", "balance"):
            self._cmd_balance()
        elif cmd in ("trades", "pozisyon"):
            self._cmd_trades()
        elif cmd in ("rapor", "report"):
            self._cmd_report()
        elif cmd == "ax":
            self._cmd_ax()
        elif cmd == "help":
            self._cmd_help()
        # kayıtlı dış handler'lar
        elif cmd in self._handlers:
            try:
                self._handlers[cmd](args)
            except Exception as e:
                logger.warning(f"Komut handler hatası [{cmd}]: {e}")
        else:
            self.send(f"❓ Bilinmeyen komut: /{cmd}\n/help ile komutları gör.")

    # ── built-in komut yanıtları ─────────────────────────────────

    def _cmd_help(self):
        self.send(
            "📋 <b>AX Komutları</b>\n\n"
            "/status — Bot durumu\n"
            "/bakiye — Güncel bakiye\n"
            "/trades — Açık pozisyonlar\n"
            "/pause — Botu duraklat\n"
            "/devam — Botu devam ettir\n"
            "/bitir — Finish modu (açık trade'ler kapanınca dur)\n"
            "/rapor — AI Brain özeti\n"
            "/ax — AX durum raporu"
        )

    def _cmd_status(self):
        mode = "PAPER" if self.paper_mode else "LIVE"
        state = "⏸ DURAKLATILDI" if self.paused else ("🏁 FİNİSH" if self.finish_mode else "✅ AKTİF")
        self.send(
            f"🤖 <b>AX Bot Durumu</b>\n\n"
            f"Mod: {mode}\n"
            f"Durum: {state}\n"
            f"Zaman: {datetime.now(timezone.utc).strftime('%H:%M UTC')}"
        )

    def _cmd_balance(self):
        try:
            if self._get_balance_fn:
                bal = self._get_balance_fn()
            else:
                from database import get_paper_account
                acc = get_paper_account()
                bal = acc.get("balance", 0) if acc else 0
            self.send(f"💰 <b>Bakiye</b>: ${bal:.2f}")
        except Exception as e:
            self.send(f"Bakiye alınamadı: {e}")

    def _cmd_trades(self):
        try:
            from database import get_trades
            trades = get_trades(limit=20, status="open")
            if not trades:
                self.send("📭 Açık pozisyon yok.")
                return
            lines = ["📊 <b>Açık Pozisyonlar</b>\n"]
            for t in trades:
                lines.append(
                    f"• {t['symbol']} {t['direction']} "
                    f"@ {t.get('entry', t.get('entry_price', '?'))}"
                )
            self.send("\n".join(lines))
        except Exception as e:
            self.send(f"Trade listesi alınamadı: {e}")

    def _cmd_report(self):
        if not self._run_ai_brain_fn:
            self.send("❌ AI Brain bağlı değil.")
            return
        try:
            self.send("🧠 AI Brain analiz ediliyor, bekleyin...")
            self._run_ai_brain_fn()
        except Exception as e:
            self.send(f"Rapor alınamadı: {e}")

    def _cmd_ax(self):
        mode  = "PAPER" if self.paper_mode else "LIVE"
        if self.paused:
            durum = "⏸ DURAKLATILDI"
        elif self.finish_mode:
            durum = "🏁 FİNİSH"
        else:
            durum = "RUNNING"

        try:
            bal = self._get_balance_fn() if self._get_balance_fn else 0
        except Exception:
            bal = 0

        try:
            trades = self._get_open_trades_fn() if self._get_open_trades_fn else []
            trade_count = len(trades)
        except Exception:
            trade_count = 0

        cb_line = ""
        if self._get_circuit_breaker_fn:
            try:
                active, remaining = self._get_circuit_breaker_fn()
                if active:
                    cb_line = f"Circuit Breaker: 🔴 Aktif ({remaining}dk kaldı)\n"
                else:
                    cb_line = "Circuit Breaker: 🟢 Kapalı\n"
            except Exception:
                pass

        self.send(
            f"🤖 <b>AX Bot Durumu</b>\n\n"
            f"Mod: <b>{mode}</b> | Durum: <b>{durum}</b>\n"
            f"{cb_line}"
            f"Açık trade: {trade_count}\n"
            f"Bakiye: <b>${bal:.2f}</b>\n"
            f"⏰ {datetime.now(timezone.utc).strftime('%H:%M UTC')}"
        )
