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
            "signals":  self._cmd_signals,
            "scan":     self._cmd_scan,
            "veto":     self._cmd_veto,
            "pipeline": self._cmd_pipeline,
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
            "/ax — Detaylı durum\n"
            "/balance — Güncel bakiye\n"
            "/trades — Açık pozisyonlar\n"
            "/mode — Mevcut mod\n\n"
            "<b>Pipeline</b>\n"
            "/scan — Son scan sonucu\n"
            "/signals — Son 10 candidate\n"
            "/veto — En çok veto sebepleri\n"
            "/pipeline — Tüm pipeline sayıları\n"
            "/debug — Sistem debug bilgisi\n\n"
            "<b>Kontrol</b>\n"
            "/pause — Duraklat\n"
            "/resume — Devam et\n"
            "/finish — Finish modu\n\n"
            "<b>Analiz</b>\n"
            "/report — AI Brain raporu\n"
            "/calendar — Günlük PnL\n"
            "/weekly — Haftalık özet\n"
            "/signal — Sinyal istatistikleri\n"
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
        """Son scan sonucu."""
        try:
            from database import get_pipeline_stats
            rows = get_pipeline_stats(limit=1)
            if not rows:
                self.send("📡 Henüz scan yapılmadı.")
                return
            r = rows[0]
            self.send(
                f"📡 <b>Son Scan</b> — {r.get('scan_time','?')[:16]} UTC\n\n"
                f"🔭 Taranan: {r.get('scanned_symbols',0)} coin\n"
                f"✅ Filtre geçti: {r.get('passed_market_filter',0)}\n"
                f"📊 Candidate: {r.get('candidates_created',0)}\n"
                f"✅ ALLOW: {r.get('ax_allow',0)}\n"
                f"❌ VETO: {r.get('ax_veto',0)}\n"
                f"👀 WATCH: {r.get('ax_watch',0)}\n"
                f"🚫 Exec filter: {r.get('risk_rejected',0)}\n"
                f"💼 Trade açıldı: {r.get('paper_trades_opened',0)}\n"
                f"📍 Seans: {r.get('session','?')} | Rejim: {r.get('market_regime','?')}"
            )
        except Exception as e:
            self.send(f"Scan verisi alınamadı: {e}")

    def _cmd_signals(self):
        """Son 10 candidate."""
        try:
            from database import get_conn
            with get_conn() as conn:
                rows = conn.execute(
                    "SELECT symbol, direction, decision, veto_reason, score, rr, created_at "
                    "FROM signal_candidates ORDER BY id DESC LIMIT 10"
                ).fetchall()
            if not rows:
                self.send("📡 Henüz sinyal yok.")
                return
            lines = ["📡 <b>Son 10 Candidate</b>\n"]
            for r in rows:
                icon = {"ALLOW":"✅","VETO":"❌","WATCH":"👀"}.get(r["decision"],"⏳")
                reason = f" ({r['veto_reason']})" if r.get("veto_reason") else ""
                lines.append(
                    f"{icon} <b>{r['symbol']}</b> {r['direction'] or '?'} "
                    f"| {r['decision']}{reason} "
                    f"| S:{r['score']:.0f} R:{r['rr']:.2f}"
                )
            self.send("\n".join(lines))
        except Exception as e:
            self.send(f"Sinyal verisi alınamadı: {e}")

    def _cmd_veto(self):
        """En çok veto sebepleri."""
        try:
            from database import get_veto_stats
            data = get_veto_stats(days=7)
            total = data.get("total", 0)
            if total == 0:
                self.send("📡 Henüz veto kaydı yok.")
                return
            lines = [
                f"❌ <b>Veto Analizi (7 gün)</b>\n",
                f"Toplam: {total} | "
                f"ALLOW: {data.get('ALLOW',0)} | "
                f"VETO: {data.get('VETO',0)} | "
                f"WATCH: {data.get('WATCH',0)}\n",
            ]
            veto_rate = data.get("VETO", 0) / total * 100 if total else 0
            if veto_rate > 85:
                lines.append("⚠️ AX çok sert veto ediyor!\n")
            lines.append("\n<b>En çok veto nedenleri:</b>")
            for item in data.get("top_veto_reasons", []):
                lines.append(f"• {item['reason']}: {item['count']}")
            if data.get("avg_veto_outcome_pct") is not None:
                lines.append(
                    f"\n📊 Veto edilen sinyallerin ort. 60dk sonuç: "
                    f"{data['avg_veto_outcome_pct']:+.2f}% "
                    f"({data.get('tracked_veto_outcomes',0)} takipli)"
                )
            self.send("\n".join(lines))
        except Exception as e:
            self.send(f"Veto verisi alınamadı: {e}")

    def _cmd_pipeline(self):
        """Tüm pipeline sayıları."""
        try:
            from database import get_pipeline_totals, get_pipeline_stats
            totals = get_pipeline_totals(hours=24)
            recent = get_pipeline_stats(limit=1)
            last_time = recent[0]["scan_time"][:16] if recent else "?"
            lines = [
                f"🔧 <b>Pipeline (Son 24 Saat)</b>\n",
                f"Son scan: {last_time} UTC\n",
                f"🔭 Taranan:     {totals.get('scanned',0)}",
                f"✅ Filtre geçti: {totals.get('passed',0)}",
                f"📊 Candidate:   {totals.get('candidates',0)}",
                f"✅ ALLOW:        {totals.get('allow',0)}",
                f"❌ VETO:         {totals.get('veto',0)}",
                f"👀 WATCH:        {totals.get('watch',0)}",
                f"🚫 Exec filter: {totals.get('risk_rejected',0)}",
                f"💼 Trade açıldı: {totals.get('trades_opened',0)}",
                f"🔄 Scan sayısı: {totals.get('scan_count',0)}",
            ]
            cands = totals.get("candidates", 0)
            allow = totals.get("allow", 0)
            trades = totals.get("trades_opened", 0)
            if cands == 0:
                lines.append("\n⚠️ 0 candidate — filtreler çok sıkı olabilir")
            elif allow == 0:
                lines.append("\n⚠️ AX hepsini veto etti")
            elif trades == 0:
                lines.append("\n⚠️ ALLOW var ama trade açılmadı")
            self.send("\n".join(lines))
        except Exception as e:
            self.send(f"Pipeline verisi alınamadı: {e}")

    def _cmd_debug(self):
        """Sistem debug bilgisi."""
        try:
            from config import DB_PATH, AX_MODE, EXECUTION_MODE, DEBUG_SIGNAL_MODE
            from database import get_pipeline_stats
            import os

            db_exists = os.path.exists(DB_PATH)
            rows = get_pipeline_stats(limit=1)
            last_scan = rows[0]["scan_time"][:16] if rows else "Hiç scan yapılmadı"

            bal = 0
            last_error = "—"
            try:
                if self._get_balance_fn:
                    bal = self._get_balance_fn()
            except Exception as e:
                last_error = str(e)

            self.send(
                f"🔧 <b>Debug Bilgisi</b>\n\n"
                f"<b>DB:</b> {DB_PATH}\n"
                f"DB var mı: {'✅' if db_exists else '❌'}\n\n"
                f"<b>Mod:</b> AX={AX_MODE} | EXEC={EXECUTION_MODE}\n"
                f"DEBUG_SIGNAL: {'✅ Açık' if DEBUG_SIGNAL_MODE else '❌ Kapalı'}\n\n"
                f"<b>Bot:</b> {'✅ AKTİF' if self._get_balance_fn else '❓'}\n"
                f"Bakiye: ${bal:.2f}\n"
                f"Son scan: {last_scan}\n\n"
                f"<b>Son hata:</b> {last_error}"
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
