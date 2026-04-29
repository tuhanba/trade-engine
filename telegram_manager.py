"""
telegram_manager.py — AX Telegram Yöneticisi
=============================================
Komutlar:
  /status   /pause    /resume   /finish
  /balance  /trades   /ax       /report
  /calendar /weekly   /coin     /mode
  /paper    /live     /signal   /execute
  /help
"""

import os
import threading
import time
import logging
import requests
from datetime import datetime, timezone, timedelta
from collections import deque

from config import TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID, PAPER_MODE

logger = logging.getLogger(__name__)


# ─────────────────────────────────────────────────────────────────────────────
# DÜŞÜK SEVİYE — retry'lı HTTP gönderici
# ─────────────────────────────────────────────────────────────────────────────

def _token() -> str:
    return os.getenv("TELEGRAM_BOT_TOKEN", TELEGRAM_BOT_TOKEN)

def _chat() -> str:
    return os.getenv("TELEGRAM_CHAT_ID", TELEGRAM_CHAT_ID)

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


# ─────────────────────────────────────────────────────────────────────────────
# KUYRUK — ana thread'i bloke etmeden gönder
# ─────────────────────────────────────────────────────────────────────────────

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
                    text, pm = self._q.popleft()
                _send_raw(text, pm)
                time.sleep(0.05)

_queue = _Queue()


# ─────────────────────────────────────────────────────────────────────────────
# TELEGRAM MANAGER
# ─────────────────────────────────────────────────────────────────────────────

class TelegramManager:
    def __init__(self, client=None):
        self.client      = client
        self.paper_mode  = PAPER_MODE
        self.paused      = False
        self.finish_mode = False
        self._offset     = 0
        self._poll_thread = None
        self._handlers   = {}

        # Callback'ler
        self._get_balance_fn         = None
        self._get_open_trades_fn     = None
        self._run_ai_brain_fn        = None
        self._get_circuit_breaker_fn = None

    @property
    def is_paused(self) -> bool:
        return self.paused

    @property
    def is_finish_mode(self) -> bool:
        return self.finish_mode

    # ── Public API ───────────────────────────────────────────────────────────

    def send(self, text: str, parse_mode: str = "HTML"):
        if not _token() or not _chat():
            return
        prefix = "🧪 <b>[PAPER]</b> " if self.paper_mode else ""
        _queue.push(prefix + text, parse_mode)

    def register(self, command: str, handler):
        self._handlers[command.lstrip("/")] = handler

    def start(self, get_balance_fn=None, get_open_trades_fn=None,
              run_ai_brain_fn=None, get_circuit_breaker_fn=None):
        if get_balance_fn:         self._get_balance_fn         = get_balance_fn
        if get_open_trades_fn:     self._get_open_trades_fn     = get_open_trades_fn
        if run_ai_brain_fn:        self._run_ai_brain_fn        = run_ai_brain_fn
        if get_circuit_breaker_fn: self._get_circuit_breaker_fn = get_circuit_breaker_fn
        if self._poll_thread and self._poll_thread.is_alive():
            return
        self._poll_thread = threading.Thread(target=self._poll_loop, daemon=True)
        self._poll_thread.start()
        logger.info("TelegramManager başlatıldı.")

    def notify_finish_complete(self, balance: float):
        self.send(
            f"🏁 <b>Finish Modu Tamamlandı</b>\n"
            f"💰 Son Bakiye: ${balance:.2f}\n"
            f"⏰ {datetime.now(timezone.utc).strftime('%H:%M UTC')}"
        )

    # ── Polling ──────────────────────────────────────────────────────────────

    def _poll_loop(self):
        while True:
            try:
                self._fetch_updates()
            except Exception as e:
                logger.debug(f"Telegram poll hata: {e}")
            time.sleep(1)

    def _fetch_updates(self):
        token = _token()
        chat  = _chat()
        if not token or not chat:
            time.sleep(5)
            return
        resp = requests.get(
            f"https://api.telegram.org/bot{token}/getUpdates",
            params={"offset": self._offset, "timeout": 10, "limit": 10},
            timeout=15,
        )
        if resp.status_code != 200:
            return
        for upd in resp.json().get("result", []):
            self._offset = upd["update_id"] + 1
            msg = upd.get("message") or upd.get("edited_message")
            if not msg:
                continue
            if str(msg.get("chat", {}).get("id", "")) != str(chat):
                continue
            text = msg.get("text", "").strip()
            if not text.startswith("/"):
                continue
            parts = text.split(None, 1)
            cmd   = parts[0].lstrip("/").split("@")[0].lower()
            args  = parts[1] if len(parts) > 1 else ""
            self._dispatch(cmd, args)

    def _dispatch(self, cmd: str, args: str):
        handlers = {
            "status":   self._cmd_status,
            "ax":       self._cmd_ax,
            "help":     self._cmd_help,
            "balance":  self._cmd_balance,
            "bakiye":   self._cmd_balance,
            "trades":   self._cmd_trades,
            "pozisyon": self._cmd_trades,
            "rapor":    self._cmd_report,
            "report":   self._cmd_report,
            "calendar": self._cmd_calendar,
            "takvim":   self._cmd_calendar,
            "weekly":   self._cmd_weekly,
            "haftalik": self._cmd_weekly,
            "signal":   self._cmd_signal_stats,
            "signals":  self._cmd_signal_stats,
            "scan":     self._cmd_scan,
            "pipeline": self._cmd_pipeline,
            "veto":     self._cmd_veto,
            "debug":    self._cmd_debug,
            "coin":     lambda: self._cmd_coin(args),
            "mode":     self._cmd_mode,
        }

        if cmd in ("pause", "duraklat"):
            self.paused = True
            self.send("⏸ Bot duraklatıldı. Devam için /resume")
        elif cmd in ("resume", "devam"):
            self.paused = False
            self.send("▶️ Bot devam ediyor.")
        elif cmd in ("finish", "bitir"):
            self.finish_mode = True
            self.send("🏁 Finish modu aktif — açık trade'ler kapanınca bot durur.")
        elif cmd == "paper":
            self.paper_mode = True
            self.send("📝 Paper moda geçildi.")
        elif cmd == "live":
            self.paper_mode = False
            self.send("⚠️ Live moda geçildi. Dikkatli ol!")
        elif cmd == "execute":
            self.send("ℹ️ AX execute modu zaten aktif.")
        elif cmd in handlers:
            fn = handlers[cmd]
            fn() if cmd != "coin" else self._cmd_coin(args)
        elif cmd in self._handlers:
            try:
                self._handlers[cmd](args)
            except Exception as e:
                logger.warning(f"Handler hatası [{cmd}]: {e}")
        else:
            self.send(f"❓ Bilinmeyen komut: /{cmd}\n/help ile komutları gör.")

    # ── Komut Yanıtları ──────────────────────────────────────────────────────

    def _cmd_help(self):
        self.send(
            "📋 <b>AX Komutları</b>\n\n"
            "<b>Durum</b>\n"
            "/status — Bot durumu\n"
            "/ax — Detaylı durum (bakiye, CB, trade)\n"
            "/balance — Güncel bakiye\n"
            "/trades — Açık pozisyonlar\n"
            "/mode — Mevcut mod\n\n"
            "<b>Kontrol</b>\n"
            "/pause — Botu duraklat\n"
            "/resume — Devam et\n"
            "/finish — Finish modu\n\n"
            "<b>Analiz</b>\n"
            "/report — AI Brain raporu\n"
            "/calendar — Günlük PnL takvimi\n"
            "/weekly — Haftalık özet\n"
            "/signal — Sinyal istatistikleri\n"
            "/pipeline — Pipeline özeti\n"
            "/veto — Veto sebepleri\n"
            "/scan — Son tarama özeti\n"
            "/debug — Sistem sağlık durumu\n"
            "/coin BTCUSDT — Coin detayı"
        )

    def _cmd_status(self):
        mode  = "PAPER" if self.paper_mode else "LIVE"
        state = ("⏸ DURAKLATILDI" if self.paused
                 else "🏁 FİNİSH" if self.finish_mode
                 else "✅ AKTİF")
        self.send(
            f"🤖 <b>AX Bot Durumu</b>\n\n"
            f"Mod: {mode}\n"
            f"Durum: {state}\n"
            f"⏰ {datetime.now(timezone.utc).strftime('%H:%M UTC')}"
        )

    def _cmd_ax(self):
        mode  = "PAPER" if self.paper_mode else "LIVE"
        state = ("⏸ DURAKLATILDI" if self.paused
                 else "🏁 FİNİSH" if self.finish_mode
                 else "RUNNING")
        try:
            bal = self._get_balance_fn() if self._get_balance_fn else 0
        except Exception:
            bal = 0
        try:
            trades = self._get_open_trades_fn() if self._get_open_trades_fn else []
            tc = len(trades)
        except Exception:
            tc = 0
        cb_line = ""
        if self._get_circuit_breaker_fn:
            try:
                active, rem = self._get_circuit_breaker_fn()
                cb_line = f"Circuit Breaker: {'🔴 Aktif (' + str(rem) + 'dk)' if active else '🟢 Kapalı'}\n"
            except Exception:
                pass
        self.send(
            f"🤖 <b>AX Bot Durumu</b>\n\n"
            f"Mod: <b>{mode}</b> | Durum: <b>{state}</b>\n"
            f"{cb_line}"
            f"Açık trade: {tc}\n"
            f"Bakiye: <b>${bal:.2f}</b>\n"
            f"⏰ {datetime.now(timezone.utc).strftime('%H:%M UTC')}"
        )

    def _cmd_balance(self):
        try:
            bal = self._get_balance_fn() if self._get_balance_fn else None
            if bal is None:
                from database import get_paper_balance
                bal = get_paper_balance()
            self.send(f"💰 <b>Bakiye</b>: ${bal:.2f}")
        except Exception as e:
            self.send(f"Bakiye alınamadı: {e}")

    def _cmd_trades(self):
        try:
            trades = (self._get_open_trades_fn() if self._get_open_trades_fn
                      else __import__("database").get_open_trades())
            if not trades:
                self.send("📭 Açık pozisyon yok.")
                return
            lines = [f"📊 <b>Açık Pozisyonlar ({len(trades)})</b>\n"]
            for t in trades:
                lines.append(
                    f"• <b>{t['symbol']}</b> {t['direction']} "
                    f"@ {t.get('entry', '?')}"
                )
            self.send("\n".join(lines))
        except Exception as e:
            self.send(f"Trade listesi alınamadı: {e}")

    def _cmd_report(self):
        if not self._run_ai_brain_fn:
            self.send("❌ AI Brain bağlı değil.")
            return
        self.send("🧠 AI Brain analiz ediliyor, bekleyin...")
        threading.Thread(target=self._run_ai_brain_fn, daemon=True).start()

    def _cmd_mode(self):
        from config import AX_MODE, EXECUTION_MODE
        self.send(
            f"⚙️ <b>Mod Bilgisi</b>\n\n"
            f"AX_MODE: <code>{AX_MODE}</code>\n"
            f"EXECUTION_MODE: <code>{EXECUTION_MODE}</code>\n"
            f"Paper: {'✅' if self.paper_mode else '❌'}"
        )

    def _cmd_calendar(self):
        try:
            from database import get_daily_summaries
            days = get_daily_summaries(30)
            if not days:
                self.send("📅 Henüz veri yok.")
                return
            lines = ["📅 <b>Günlük PnL (Son 30 Gün)</b>\n"]
            for d in days[:14]:
                emoji = "🟢" if d["net_pnl"] > 0 else "🔴" if d["net_pnl"] < 0 else "⚪"
                lines.append(
                    f"{emoji} {d['date']} | {d['net_pnl']:+.2f}$ | "
                    f"{d['trade_count']} trade | WR:{d['win_rate']:.0%}"
                )
            self.send("\n".join(lines))
        except Exception as e:
            self.send(f"Takvim alınamadı: {e}")

    def _cmd_weekly(self):
        try:
            from database import get_stats
            stats7  = get_stats(hours=168)
            stats30 = get_stats(hours=720)
            self.send(
                f"📊 <b>Haftalık Özet</b>\n\n"
                f"<b>Son 7 Gün</b>\n"
                f"Trade: {stats7['total']} | WR: {stats7['win_rate']:.0%}\n"
                f"PnL: {stats7['total_pnl']:+.2f}$ | PF: {stats7['profit_factor']:.2f}\n"
                f"Avg R: {stats7['avg_r']:.2f} | DD: {stats7['max_drawdown']:.2f}$\n\n"
                f"<b>Son 30 Gün</b>\n"
                f"Trade: {stats30['total']} | WR: {stats30['win_rate']:.0%}\n"
                f"PnL: {stats30['total_pnl']:+.2f}$ | PF: {stats30['profit_factor']:.2f}"
            )
        except Exception as e:
            self.send(f"Haftalık özet alınamadı: {e}")

    def _cmd_signal_stats(self):
        try:
            from database import get_conn
            with get_conn() as conn:
                rows = conn.execute(
                    """SELECT decision, COUNT(*) as cnt
                       FROM signal_candidates
                       WHERE created_at >= datetime('now', '-7 days')
                       GROUP BY decision"""
                ).fetchall()
            if not rows:
                self.send("📡 Henüz sinyal kaydı yok.")
                return
            lines = ["📡 <b>Sinyal İstatistikleri (7 Gün)</b>\n"]
            for r in rows:
                emoji = {"ALLOW": "✅", "VETO": "❌", "WATCH": "👀"}.get(r["decision"], "❓")
                lines.append(f"{emoji} {r['decision']}: {r['cnt']}")
            self.send("\n".join(lines))
        except Exception as e:
            self.send(f"Sinyal istatistiği alınamadı: {e}")

    def _cmd_scan(self):
        """Son tarama özetini göster."""
        try:
            from database import get_pipeline_summary
            p = get_pipeline_summary(hours=1)
            self.send(
                f"📡 <b>Son Tarama Özeti (1s)</b>\n\n"
                f"Taranan: {p.get('scanned', 0)}\n"
                f"Geçen: {p.get('passed', 0)}\n"
                f"Candidate: {p.get('candidates', 0)}\n"
                f"ALLOW: {p.get('ax_allow', 0)} | "
                f"VETO: {p.get('ax_veto', 0)} | "
                f"WATCH: {p.get('ax_watch', 0)}\n"
                f"Açılan trade: {p.get('paper_trades', 0)}\n"
                f"Son tarama: {p.get('last_scan_time', '—')}"
            )
        except Exception as e:
            self.send(f"Tarama verisi alınamadı: {e}")

    def _cmd_pipeline(self):
        """24 saatlik pipeline özetini göster."""
        try:
            from database import get_pipeline_summary
            p = get_pipeline_summary(hours=24)
            total_cand = p.get('candidates', 0)
            allow      = p.get('ax_allow', 0)
            veto       = p.get('ax_veto', 0)
            allow_rate = round(allow / max(total_cand, 1) * 100, 1)
            self.send(
                f"🔀 <b>Pipeline Özeti (24s)</b>\n\n"
                f"Döngü sayısı: {p.get('scan_count', 0)}\n"
                f"Taranan coin: {p.get('scanned', 0)}\n"
                f"Filter geçen: {p.get('passed', 0)}\n"
                f"Candidate: {total_cand}\n"
                f"ALLOW: {allow} ({allow_rate}%)\n"
                f"VETO: {veto}\n"
                f"WATCH: {p.get('ax_watch', 0)}\n"
                f"Risk reject: {p.get('risk_rejected', 0)}\n"
                f"Paper trade: {p.get('paper_trades', 0)}"
            )
        except Exception as e:
            self.send(f"Pipeline verisi alınamadı: {e}")

    def _cmd_veto(self):
        """Veto sebeplerini listele."""
        try:
            from database import get_veto_stats
            stats = get_veto_stats(hours=24)
            if not stats:
                self.send("❌ Son 24 saatte veto kaydı yok.")
                return
            lines = ["❌ <b>Veto Sebepleri (24s)</b>\n"]
            for s in stats[:10]:
                lines.append(f"• {s['reason']}: {s['count']}")
            self.send("\n".join(lines))
        except Exception as e:
            self.send(f"Veto verisi alınamadı: {e}")

    def _cmd_debug(self):
        """Sistem sağlık durumunu göster."""
        try:
            from database import get_paper_balance, get_open_trades, get_pipeline_summary, get_conn
            bal    = get_paper_balance()
            trades = get_open_trades()
            pipe   = get_pipeline_summary(hours=1)
            last_scan = pipe.get("last_scan_time", "—")

            # DB kontrol
            try:
                with get_conn() as conn:
                    conn.execute("SELECT 1")
                db_ok = "✅"
            except Exception:
                db_ok = "❌"

            state = ("⏸ DURAKLATILDI" if self.paused
                     else "🏁 FİNİSH" if self.finish_mode
                     else "✅ AKTİF")

            self.send(
                f"🔧 <b>Debug / Sağlık</b>\n\n"
                f"Bot: {state}\n"
                f"DB: {db_ok}\n"
                f"Bakiye: ${bal:.2f}\n"
                f"Açık trade: {len(trades)}\n"
                f"Son tarama: {last_scan}\n"
                f"Son 1s candidate: {pipe.get('candidates', 0)}\n"
                f"Son 1s ALLOW: {pipe.get('ax_allow', 0)}\n"
                f"Son 1s paper trade: {pipe.get('paper_trades', 0)}\n"
                f"⏰ {datetime.now(timezone.utc).strftime('%H:%M UTC')}"
            )
        except Exception as e:
            self.send(f"Debug verisi alınamadı: {e}")

    def _cmd_coin(self, symbol: str):
        symbol = symbol.strip().upper()
        if not symbol:
            self.send("Kullanım: /coin BTCUSDT")
            return
        try:
            from database import get_coin_profile, get_trades
            profile = get_coin_profile(symbol)
            trades  = get_trades(limit=5, symbol=symbol)
            if not profile:
                self.send(f"❓ {symbol} için henüz veri yok.")
                return
            self.send(
                f"🪙 <b>{symbol} Profili</b>\n\n"
                f"Trade: {profile.get('trade_count', 0)} | WR: {profile.get('win_rate', 0):.0%}\n"
                f"Avg R: {profile.get('avg_r', 0):.2f} | PF: {profile.get('profit_factor', 0):.2f}\n"
                f"Avg MFE: {profile.get('avg_mfe', 0):.2f}R | MAE: {profile.get('avg_mae', 0):.2f}R\n"
                f"Danger: {profile.get('danger_score', 0):.2f} | Fakeout: {profile.get('fakeout_rate', 0):.0%}\n"
                f"Profil: {profile.get('volatility_profile', '?')}\n"
                f"Tercih: {profile.get('preferred_direction', '?')} | Seans: {profile.get('best_session', '?')}"
            )
        except Exception as e:
            self.send(f"Coin verisi alınamadı: {e}")
