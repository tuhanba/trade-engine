"""
telegram_manager.py - AurvexAI Telegram Komut Merkezi v2.0
Komutlar: /help /status /stats /trades /balance /open /ghost /daily /mode /pause /resume /finish
"""
from __future__ import annotations
import logging
import threading
import time
from datetime import datetime, timezone
from typing import Callable, Optional
import requests
import config

logger = logging.getLogger("ax.telegram_manager")
_POLL_URL = "https://api.telegram.org/bot{token}/getUpdates"
_TIMEOUT  = 10


class TelegramManager:
    def __init__(self, send_fn: Callable[[str], bool]):
        self.send_fn        = send_fn
        self.token          = config.TELEGRAM_BOT_TOKEN
        self.chat_id        = str(config.TELEGRAM_CHAT_ID)
        self.is_paused      = False
        self.is_finish_mode = False
        self._running       = False
        self._thread: Optional[threading.Thread] = None
        self._last_update_id = 0
        self._start_time    = time.time()

    def _is_configured(self) -> bool:
        return bool(self.token) and bool(self.chat_id)

    def start(self):
        if not self._is_configured():
            logger.warning("TelegramManager: token/chat_id eksik")
            return
        self._running = True
        self._thread = threading.Thread(
            target=self._poll_loop, daemon=True, name="tg-manager"
        )
        self._thread.start()
        logger.info("TelegramManager basladi — /help yaz")

    def stop(self):
        self._running = False

    def _poll_loop(self):
        while self._running:
            try:
                self._poll_once()
            except Exception as e:
                logger.debug("Poll hatasi: %s", e)
            time.sleep(3)

    def _poll_once(self):
        url = _POLL_URL.format(token=self.token)
        params = {"timeout": 5, "offset": self._last_update_id + 1, "limit": 10}
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

    def _handle_update(self, update: dict):
        msg       = update.get("message") or update.get("channel_post") or {}
        text      = (msg.get("text") or "").strip()
        from_chat = str(msg.get("chat", {}).get("id", ""))
        if not text.startswith("/"):
            return
        if from_chat and from_chat != self.chat_id:
            return
        cmd = text.lower().split()[0].split("@")[0]
        logger.info("Komut: %s", cmd)
        handlers = {
            "/help":    self._cmd_help,
            "/status":  self._cmd_status,
            "/stats":   self._cmd_stats,
            "/trades":  self._cmd_trades,
            "/balance": self._cmd_balance,
            "/open":    self._cmd_open,
            "/ghost":   self._cmd_ghost,
            "/daily":   self._cmd_daily,
            "/mode":    self._cmd_mode,
            "/pause":   self._cmd_pause,
            "/resume":  self._cmd_resume,
            "/finish":  self._cmd_finish,
        }
        handler = handlers.get(cmd)
        if handler:
            try:
                handler()
            except Exception as e:
                self.send_fn(f"Komut hatasi ({cmd}): {e}")
        else:
            self.send_fn(f"Bilinmeyen komut: {cmd}\n/help yazin.")

    def _cmd_help(self):
        self.send_fn(
            "AurvexAI Komutlari\n\n"
            "Durum\n"
            "/status  - Bot durumu + bakiye\n"
            "/mode    - Calisma modu\n"
            "/open    - Acik tradeler\n\n"
            "Istatistik\n"
            "/stats   - Genel performans\n"
            "/daily   - Bugunun ozeti\n"
            "/balance - Bakiye detayi\n"
            "/trades  - Son 5 trade\n"
            "/ghost   - Ghost learning\n\n"
            "Kontrol\n"
            "/pause   - Duraklatir\n"
            "/resume  - Devam ettirir\n"
            "/finish  - Yeni sinyal almaz, aciklarini kapatir"
        )

    def _cmd_status(self):
        import database
        bal    = database.get_paper_balance() or 0
        open_t = database.get_open_trades()
        uptime = int(time.time() - self._start_time)
        h, rem = divmod(uptime, 3600)
        m = rem // 60
        paused_txt = "DURAKLATILDI" if self.is_paused else "Aktif"
        open_lines = ""
        for t in open_t[:3]:
            sym  = t.get("symbol", "?")
            side = t.get("side") or t.get("direction", "?")
            ep   = float(t.get("entry_price") or t.get("entry") or 0)
            upnl = float(t.get("unrealized_pnl") or 0)
            open_lines += f"\n  {sym} {side} @{ep:.4f} ({upnl:+.2f}$)"
        self.send_fn(
            f"AurvexAI Durum\n"
            f"Durum: {paused_txt}\n"
            f"Mod: {config.EXECUTION_MODE.upper()} | {config.AX_MODE.upper()}\n"
            f"Bakiye: ${bal:.2f}\n"
            f"Acik trade: {len(open_t)}{open_lines}\n"
            f"Uptime: {h}s {m}dk"
        )

    def _cmd_stats(self):
        import database
        stats = database.get_dashboard_stats()
        total = stats.get("total_trades", 0)
        wins  = stats.get("win_trades", 0)
        loss  = stats.get("loss_trades", 0)
        pnl   = stats.get("total_pnl", 0)
        wr    = stats.get("win_rate", 0)
        bal   = database.get_paper_balance() or 0
        init  = getattr(config, "INITIAL_PAPER_BALANCE", 250.0)
        roi   = ((bal - init) / init * 100) if init else 0
        self.send_fn(
            f"Performans Istatistikleri\n\n"
            f"Toplam trade: {total}\n"
            f"Kazanc / Kayip: {wins}W / {loss}L\n"
            f"Winrate: {wr:.1f}%\n"
            f"Toplam PnL: ${pnl:+.2f}\n"
            f"Bakiye: ${bal:.2f}\n"
            f"ROI: {roi:+.1f}%\n"
            f"Baslangic: ${init:.2f}"
        )

    def _cmd_trades(self):
        import database
        trades = database.get_recent_trades(5)
        if not trades:
            self.send_fn("Henuez kapatilmis trade yok.")
            return
        lines = []
        for t in trades:
            sym    = t.get("symbol", "?")
            side   = t.get("side") or t.get("direction", "?")
            pnl    = float(t.get("realized_pnl") or 0)
            reason = t.get("close_reason") or "?"
            icon   = "WIN" if pnl > 0 else "LOSS"
            lines.append(f"{icon} {sym} {side} | {pnl:+.3f}$ | {reason}")
        self.send_fn("Son 5 Trade\n\n" + "\n".join(lines))

    def _cmd_balance(self):
        import database
        bal  = database.get_paper_balance() or 0
        init = getattr(config, "INITIAL_PAPER_BALANCE", 250.0)
        diff = bal - init
        try:
            conn  = database.get_connection()
            today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
            row   = conn.execute(
                "SELECT COALESCE(SUM(realized_pnl),0) FROM trades WHERE DATE(closed_at)=? AND status='CLOSED'",
                (today,)
            ).fetchone()
            conn.close()
            today_pnl = float(row[0]) if row else 0.0
        except Exception:
            today_pnl = 0.0
        self.send_fn(
            f"Bakiye Detayi\n\n"
            f"Anlik: ${bal:.2f}\n"
            f"Baslangic: ${init:.2f}\n"
            f"Toplam kar/zarar: ${diff:+.2f}\n"
            f"Bugun: ${today_pnl:+.2f}"
        )

    def _cmd_open(self):
        import database
        trades = database.get_open_trades()
        if not trades:
            self.send_fn("Acik trade yok.")
            return
        lines = []
        now = datetime.now(timezone.utc)
        for t in trades:
            sym  = t.get("symbol", "?")
            side = t.get("side") or t.get("direction", "?")
            ep   = float(t.get("entry_price") or t.get("entry") or 0)
            sl   = float(t.get("stop_loss") or t.get("sl") or 0)
            tp1  = float(t.get("tp1") or 0)
            upnl = float(t.get("unrealized_pnl") or 0)
            opened = t.get("opened_at", "")
            hold = ""
            if opened:
                try:
                    dt = datetime.fromisoformat(opened.replace("Z", "+00:00"))
                    mins = int((now - dt).total_seconds() / 60)
                    hold = f" {mins}dk"
                except Exception:
                    pass
            lines.append(
                f"{sym} {side}{hold}\n"
                f"  Giris: ${ep:.4f} SL: ${sl:.4f}\n"
                f"  TP1: ${tp1:.4f} PnL: {upnl:+.2f}$"
            )
        self.send_fn(f"Acik Tradeler ({len(trades)})\n\n" + "\n\n".join(lines))

    def _cmd_ghost(self):
        import database
        try:
            conn  = database.get_connection()
            total = conn.execute("SELECT COUNT(*) FROM signal_candidates").fetchone()[0]
            tp    = conn.execute("SELECT COUNT(*) FROM signal_candidates WHERE status='TP_HIT'").fetchone()[0]
            sl    = conn.execute("SELECT COUNT(*) FROM signal_candidates WHERE status='SL_HIT'").fetchone()[0]
            pnl   = conn.execute(
                "SELECT COALESCE(SUM(ghost_pnl),0) FROM signal_candidates WHERE status IN ('TP_HIT','SL_HIT')"
            ).fetchone()[0]
            conn.close()
            resolved = tp + sl
            wr = round(tp / resolved * 100, 1) if resolved > 0 else 0
            self.send_fn(
                f"Ghost Learning\n\n"
                f"Toplam sinyal adayi: {total}\n"
                f"TP vurdu: {tp} | SL vurdu: {sl}\n"
                f"Ghost winrate: {wr:.1f}%\n"
                f"Ghost PnL: ${float(pnl):+.2f}\n"
                f"Bekleyen: {total - resolved}"
            )
        except Exception as e:
            self.send_fn(f"Ghost bilgisi alinamadi: {e}")

    def _cmd_daily(self):
        import database
        try:
            conn  = database.get_connection()
            today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
            row   = conn.execute(
                """SELECT COUNT(*),
                          SUM(CASE WHEN realized_pnl > 0 THEN 1 ELSE 0 END),
                          SUM(CASE WHEN realized_pnl <= 0 THEN 1 ELSE 0 END),
                          COALESCE(SUM(realized_pnl), 0)
                   FROM trades WHERE DATE(closed_at)=? AND status='CLOSED'""",
                (today,)
            ).fetchone()
            conn.close()
            total  = row[0] or 0
            wins   = row[1] or 0
            losses = row[2] or 0
            pnl    = float(row[3] or 0)
            wr     = round(wins / total * 100, 1) if total else 0
            self.send_fn(
                f"Bugun ({today})\n\n"
                f"Trade: {total} | {wins}W / {losses}L\n"
                f"Winrate: {wr:.1f}%\n"
                f"Gunluk PnL: ${pnl:+.2f}"
            )
        except Exception as e:
            self.send_fn(f"Gunluk ozet alinamadi: {e}")

    def _cmd_mode(self):
        thr = getattr(config, "TRADE_THRESHOLD", 68)
        bad = getattr(config, "BAD_HOURS_UTC", [])
        self.send_fn(
            f"Calisma Modu\n\n"
            f"EXECUTION_MODE: {config.EXECUTION_MODE.upper()}\n"
            f"AX_MODE: {config.AX_MODE.upper()}\n"
            f"TRADE_THRESHOLD: {thr}\n"
            f"Kapali saatler (UTC): {bad}"
        )

    def _cmd_pause(self):
        self.is_paused = True
        self.send_fn(
            "Bot duraklatildi.\n"
            "Acik tradeler izlenmeye devam eder.\n"
            "Yeni sinyal uretilmeyecek.\n"
            "/resume ile devam et."
        )

    def _cmd_resume(self):
        self.is_paused = False
        self.send_fn("Bot devam ediyor. Sinyal uretimi aktif.")

    def _cmd_finish(self):
        self.is_finish_mode = True
        self.send_fn(
            "Finish modu aktif.\n"
            "Yeni sinyal alinmayacak.\n"
            "Acik tradeler kapaninca bot duracak."
        )
