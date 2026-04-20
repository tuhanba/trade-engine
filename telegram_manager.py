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

TG_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
TG_CHAT  = os.getenv("TELEGRAM_CHAT_ID", "")

API_BASE = f"https://api.telegram.org/bot{TG_TOKEN}"


# ─────────────────────────────────────────────────────────────────
# DÜŞÜK SEVİYE: retry'lı gönderici
# ─────────────────────────────────────────────────────────────────

def _send_raw(text, parse_mode="HTML", retries=3):
    """Telegram'a doğrudan HTTP POST — en fazla `retries` kez dener."""
    if not TG_TOKEN or not TG_CHAT:
        logger.warning("Telegram: TOKEN veya CHAT_ID eksik, mesaj gönderilemedi.")
        return False
    for attempt in range(1, retries + 1):
        try:
            resp = requests.post(
                f"{API_BASE}/sendMessage",
                json={
                    "chat_id":    TG_CHAT,
                    "text":       text[:4096],   # Telegram limiti
                    "parse_mode": parse_mode,
                },
                timeout=8,
            )
            if resp.status_code == 200:
                return True
            # 429 = rate limit
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
            time.sleep(2 ** attempt)   # exponential backoff
    return False


# ─────────────────────────────────────────────────────────────────
# TelegramManager
# ─────────────────────────────────────────────────────────────────

class TelegramManager:

    def __init__(self, client=None):
        self._client         = client
        self.paper_mode      = False
        self._paused         = False
        self._finish_mode    = False
        self._running        = False
        self._poll_thread    = None
        self._send_thread    = None
        self._queue          = deque()
        self._queue_lock     = threading.Lock()
        self._get_balance    = None
        self._get_open_trades = None

        # Drawdown takibi
        self._peak_balance   = None
        self._dd_warned      = False
        self.DD_ALERT_PCT    = 10.0   # %10 drawdown'da uyar

    # ── PROPERTY'LER ───────────────────────────────────────────

    @property
    def is_paused(self):
        return self._paused

    @property
    def is_finish_mode(self):
        return self._finish_mode

    # ── BAŞLATMA / DURDURMA ────────────────────────────────────

    def start(self, get_balance_fn=None, get_open_trades_fn=None):
        self._get_balance     = get_balance_fn
        self._get_open_trades = get_open_trades_fn
        self._running         = True

        # Mesaj kuyruğu işçisi
        self._send_thread = threading.Thread(
            target=self._queue_worker, daemon=True, name="tg-send")
        self._send_thread.start()

        # Komut dinleyici (long-polling)
        self._poll_thread = threading.Thread(
            target=self._poll_loop, daemon=True, name="tg-poll")
        self._poll_thread.start()

        logger.info("TelegramManager başlatıldı.")

    def stop(self):
        self._running = False
        self.send("🔴 <b>AX Bot durduruldu.</b>")
        time.sleep(1)

    # ── MESAJ GÖNDERME ─────────────────────────────────────────

    def send(self, msg):
        """Kuyruğa ekle — bloklama yok."""
        prefix = "🧪 <b>[PAPER]</b> " if self.paper_mode else ""
        with self._queue_lock:
            self._queue.append(prefix + msg)

    def _queue_worker(self):
        """Kuyruktaki mesajları sırayla gönderir."""
        while self._running:
            msg = None
            with self._queue_lock:
                if self._queue:
                    msg = self._queue.popleft()
            if msg:
                _send_raw(msg)
                time.sleep(0.4)   # Telegram rate limit (30 msg/s)
            else:
                time.sleep(0.2)

    # ── STARTUP ────────────────────────────────────────────────

    def send_startup(self, balance, params, symbol_count, ai_available):
        ai_tag = "✅ AX v4.0 aktif" if ai_available else "⚠️ AX devre dışı"
        mode   = "🧪 PAPER" if self.paper_mode else "💰 GERÇEK"
        p      = params or {}
        self.send(
            f"🟢 <b>AURVEX BOT BAŞLADI</b>\n"
            f"⏰ {datetime.now(timezone.utc).strftime('%H:%M:%S UTC')}\n\n"
            f"💰 Bakiye: <code>${balance:.2f}</code>\n"
            f"📊 Mod: {mode}\n"
            f"🎯 {symbol_count} sembol taranıyor\n"
            f"🤖 {ai_tag}\n\n"
            f"📌 <b>Mevcut Parametreler:</b>\n"
            f"  SL: {p.get('sl_atr_mult', '?')} | TP: {p.get('tp_atr_mult', '?')}\n"
            f"  Risk: %{p.get('risk_pct', '?')} | Vol: {p.get('vol_ratio_min', '?')}x\n\n"
            f"💬 Komutlar için /yardim yaz."
        )

    # ── DRAWDOWN KONTROLÜ ──────────────────────────────────────

    def check_drawdown(self, balance):
        if balance is None:
            return
        if self._peak_balance is None or balance > self._peak_balance:
            self._peak_balance = balance
            self._dd_warned    = False
            return
        if self._peak_balance <= 0:
            return
        dd_pct = (self._peak_balance - balance) / self._peak_balance * 100
        if dd_pct >= self.DD_ALERT_PCT and not self._dd_warned:
            self._dd_warned = True
            self.send(
                f"⚠️ <b>DRAWDOWN UYARISI</b>\n\n"
                f"📉 Zirve: <code>${self._peak_balance:.2f}</code>\n"
                f"💰 Şu an: <code>${balance:.2f}</code>\n"
                f"📊 Düşüş: <code>%{dd_pct:.1f}</code>\n\n"
                f"⛔ Dikkatli ol! Riski azaltmayı düşün."
            )

    # ── FİNİSH MODU ────────────────────────────────────────────

    def notify_finish_complete(self, balance):
        self._finish_mode = False
        self.send(
            f"✅ <b>Tüm trade'ler kapatıldı.</b>\n"
            f"💰 Final bakiye: <code>${balance:.2f}</code>\n"
            f"Bot şimdi yeni trade açmayacak. Yeniden başlatmak için /devam yaz."
        )

    # ── LONG-POLLING ───────────────────────────────────────────

    def _poll_loop(self):
        offset = None
        while self._running:
            try:
                params = {"timeout": 20, "allowed_updates": ["message"]}
                if offset:
                    params["offset"] = offset
                resp = requests.get(f"{API_BASE}/getUpdates", params=params, timeout=25)
                if resp.status_code != 200:
                    time.sleep(5)
                    continue
                updates = resp.json().get("result", [])
                for upd in updates:
                    offset = upd["update_id"] + 1
                    self._handle_update(upd)
            except requests.exceptions.Timeout:
                pass
            except Exception as e:
                logger.warning(f"Telegram polling hatası: {e}")
                time.sleep(5)

    def _handle_update(self, update):
        msg = update.get("message") or update.get("edited_message")
        if not msg:
            return

        chat_id = str(msg.get("chat", {}).get("id", ""))
        text    = (msg.get("text") or "").strip()

        # Sadece yetkili chat
        if chat_id != str(TG_CHAT):
            return

        if not text.startswith("/") and not text.startswith("ax "):
            # AX sohbet modu: "ax nasıl gidiyoruz" gibi
            if text.lower().startswith("ax"):
                self._cmd_ax(text[2:].strip())
            return

        cmd = text.split()[0].lower().lstrip("/")
        self._dispatch(cmd, text)

    def _dispatch(self, cmd, full_text):
        cmds = {
            "status":  self._cmd_status,
            "durum":   self._cmd_status,
            "pause":   self._cmd_pause,
            "dur":     self._cmd_pause,
            "devam":   self._cmd_resume,
            "resume":  self._cmd_resume,
            "bitir":   self._cmd_finish,
            "finish":  self._cmd_finish,
            "bakiye":  self._cmd_balance,
            "balance": self._cmd_balance,
            "trades":  self._cmd_trades,
            "islemler":self._cmd_trades,
            "rapor":   self._cmd_report,
            "report":  self._cmd_report,
            "ax":      lambda: self._cmd_ax(full_text.partition(" ")[2]),
            "yardim":  self._cmd_help,
            "help":    self._cmd_help,
        }
        fn = cmds.get(cmd)
        if fn:
            try:
                fn()
            except Exception as e:
                logger.error(f"Komut hatası [{cmd}]: {e}")
                self.send(f"⚠️ Komut hatası: {e}")
        else:
            self.send(f"❓ Bilinmeyen komut: /{cmd}\nKomutlar için /yardim")

    # ── KOMUTLAR ───────────────────────────────────────────────

    def _cmd_status(self):
        bal   = self._get_balance() if self._get_balance else "?"
        trades = self._get_open_trades() if self._get_open_trades else {}
        pause_tag  = "⏸ DURAKLATILDI" if self._paused else "▶️ Aktif"
        finish_tag = " | 🏁 Finish Modu" if self._finish_mode else ""
        lines = [
            f"📊 <b>AX Bot Durumu</b>",
            f"• Mod: {pause_tag}{finish_tag}",
            f"• Bakiye: <code>${bal:.2f}</code>",
            f"• Açık trade: {len(trades)}",
        ]
        if trades:
            lines.append("\n<b>Açık Pozisyonlar:</b>")
            for sym, t in list(trades.items())[:5]:
                entry = t.get("entry", 0)
                lines.append(f"  {'⬆️' if t.get('direction')=='LONG' else '⬇️'} {sym} @ {entry:.4f}")
        self.send("\n".join(lines))

    def _cmd_pause(self):
        self._paused = True
        self.send(
            "⏸ <b>Bot duraklatıldı.</b>\n"
            "Mevcut trade'ler takip edilmeye devam eder.\n"
            "Yeni trade açılmaz. Devam ettirmek için /devam"
        )

    def _cmd_resume(self):
        self._paused      = False
        self._finish_mode = False
        self.send("▶️ <b>Bot devam ediyor.</b> Tarama yeniden aktif.")

    def _cmd_finish(self):
        self._finish_mode = True
        trades = self._get_open_trades() if self._get_open_trades else {}
        self.send(
            f"🏁 <b>Finish Modu aktif.</b>\n"
            f"Mevcut {len(trades)} trade kapanınca bot durur.\n"
            f"İptal için /devam"
        )

    def _cmd_balance(self):
        bal = self._get_balance() if self._get_balance else None
        if bal is None:
            self.send("⚠️ Bakiye alınamadı.")
            return
        dd_str = ""
        if self._peak_balance:
            dd = (self._peak_balance - bal) / self._peak_balance * 100
            dd_str = f"\n📉 Zirve: ${self._peak_balance:.2f} (DD: %{dd:.1f})"
        self.send(f"💰 <b>Bakiye: ${bal:.2f}</b>{dd_str}")

    def _cmd_trades(self):
        try:
            import sqlite3
            db_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "trading.db")
            conn = sqlite3.connect(db_path)
            conn.row_factory = sqlite3.Row
            rows = conn.execute(
                "SELECT symbol, direction, net_pnl, status, close_time "
                "FROM trades WHERE status!='OPEN' "
                "ORDER BY close_time DESC LIMIT 8"
            ).fetchall()
            conn.close()
            if not rows:
                self.send("📋 Henüz kapanan trade yok.")
                return
            lines = ["📋 <b>Son 8 Trade:</b>"]
            for r in rows:
                e   = "✅" if (r["net_pnl"] or 0) > 0 else "❌"
                pnl = r["net_pnl"] or 0
                t   = (r["close_time"] or "")[-8:-3]
                lines.append(f"{e} {r['symbol']} {r['direction']} {pnl:+.3f}$ [{t}]")
            self.send("\n".join(lines))
        except Exception as e:
            self.send(f"⚠️ Trade listesi alınamadı: {e}")

    def _cmd_report(self):
        self.send("🤖 <b>AX raporu hazırlanıyor...</b>")
        try:
            from trade_engine.ai_brain import analyze_and_adapt
            report = analyze_and_adapt()
            # Raporu 3800 karakter parçalara böl
            for i in range(0, len(report), 3800):
                self.send(report[i:i+3800])
                time.sleep(0.5)
        except Exception as e:
            self.send(f"⚠️ Rapor alınamadı: {e}")

    def _cmd_ax(self, question):
        if not question:
            self.send("🤖 <b>AX:</b> Bir şey sor! Örnek: ax nasıl gidiyoruz")
            return
        try:
            from trade_engine.ai_brain import ax_chat
            reply = ax_chat(question)
            self.send(reply)
        except Exception as e:
            self.send(f"⚠️ AX yanıt veremedi: {e}")

    def _cmd_help(self):
        self.send(
            "🤖 <b>AX — Komutlar</b>\n\n"
            "/status  — Bot durumu ve açık trade'ler\n"
            "/bakiye  — Güncel bakiye\n"
            "/trades  — Son 8 trade\n"
            "/rapor   — AX tam rapor\n"
            "/pause   — Botu duraklat (trade'ler devam)\n"
            "/devam   — Botu devam ettir\n"
            "/bitir   — Finish modu (yeni trade açma)\n\n"
            "💬 <b>AX Sohbet:</b>\n"
            "ax nasıl gidiyoruz\n"
            "ax en iyi coin\n"
            "ax piyasa durumu\n"
            "ax bugün"
        )
