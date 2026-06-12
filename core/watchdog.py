"""
core/watchdog.py — AX System Watchdog & Health Monitor
=======================================================
- Bot ve Dashboard süreçlerini izler
- Crash durumunda otomatik restart
- Sistem kaynaklarını monitör eder
- Günlük Telegram raporu gönderir
"""
import logging
import threading
import time
import os
import sqlite3
from datetime import datetime, timezone, timedelta

logger = logging.getLogger(__name__)


class SystemWatchdog:
    """7/24 çalışma için sistem izleme ve otomatik recovery."""

    def __init__(self, db_path: str, check_interval: int = 60):
        self.db_path = db_path
        self.check_interval = check_interval
        self._running = False
        self._thread = None
        self._start_time = time.time()
        self._last_daily_report = None
        self._daily_report_hour = 23  # Her gün saat 23:00 UTC'de
        self._error_count = 0
        self._restart_count = 0
        self._consecutive_latency_violations = 0

    def get_uptime(self) -> str:
        """Sistem uptime'ını döner."""
        elapsed = int(time.time() - self._start_time)
        days = elapsed // 86400
        hours = (elapsed % 86400) // 3600
        minutes = (elapsed % 3600) // 60
        if days > 0:
            return f"{days}g {hours}sa {minutes}dk"
        elif hours > 0:
            return f"{hours}sa {minutes}dk"
        return f"{minutes}dk"

    def get_system_health(self) -> dict:
        """Sistem sağlık özeti."""
        try:
            db_size = 0
            if os.path.exists(self.db_path):
                db_size = os.path.getsize(self.db_path) / (1024 * 1024)

            # DB connectivity check
            # NEDEN (Faz 1.2): WAL/busy_timeout disiplini için database.open_db
            db_ok = False
            try:
                from database import open_db
                with open_db(self.db_path, timeout=5) as conn:
                    conn.execute("SELECT 1").fetchone()
                db_ok = True
            except Exception:
                pass

            return {
                "status": "healthy" if db_ok else "degraded",
                "uptime": self.get_uptime(),
                "db_ok": db_ok,
                "db_size_mb": round(db_size, 2),
                "error_count": self._error_count,
                "restart_count": self._restart_count,
                "timestamp": datetime.now(timezone.utc).isoformat(),
            }
        except Exception as e:
            return {"status": "error", "error": str(e)}

    def _check_db_health(self) -> bool:
        """DB sağlık kontrolü — WAL temizleme dahil."""
        try:
            # NEDEN (Faz 1.2): WAL/busy_timeout disiplini için database.open_db
            from database import open_db
            with open_db(self.db_path, timeout=10) as conn:
                # WAL checkpoint — DB boyutunu kontrol altında tut
                conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")

                # Eski AI logları temizle (30 günden eski)
                cutoff = (datetime.now(timezone.utc) - timedelta(days=30)).isoformat()
                conn.execute(
                    "DELETE FROM ai_logs WHERE created_at < ?", (cutoff,)
                )

                # Eski signal_candidates temizle (ALLOW/WATCH hariç, 14 günden eski)
                cutoff_14d = (datetime.now(timezone.utc) - timedelta(days=14)).isoformat()
                conn.execute(
                    "DELETE FROM signal_candidates WHERE created_at < ? "
                    "AND decision NOT IN ('ALLOW', 'WATCH')",
                    (cutoff_14d,)
                )

                conn.commit()
            return True
        except Exception as e:
            logger.error(f"[Watchdog] DB health check hatası: {e}")
            self._error_count += 1
            return False

    def _generate_daily_report(self) -> str:
        """Günlük performans raporu oluştur."""
        try:
            # NEDEN (Faz 1.2): WAL/busy_timeout disiplini için database.open_db
            from database import open_db
            with open_db(self.db_path) as conn:
                today = datetime.now(timezone.utc).strftime("%Y-%m-%d")

                # Bugünkü trade'ler
                row = conn.execute(
                    "SELECT COUNT(*), SUM(net_pnl), "
                    "SUM(CASE WHEN net_pnl > 0 THEN 1 ELSE 0 END) "
                    "FROM trades WHERE DATE(close_time) = ? AND status = 'closed'",
                    (today,)
                ).fetchone()
                today_trades = row[0] or 0
                today_pnl = round(row[1] or 0, 2)
                today_wins = row[2] or 0

                # Açık trade'ler
                open_count = conn.execute(
                    "SELECT COUNT(*) FROM trades WHERE status != 'closed'"
                ).fetchone()[0]

                # Toplam istatistik
                total_row = conn.execute(
                    "SELECT COUNT(*), SUM(net_pnl), "
                    "SUM(CASE WHEN net_pnl > 0 THEN 1 ELSE 0 END) "
                    "FROM trades WHERE status = 'closed' AND is_valid_for_stats = 1"
                ).fetchone()
                total_trades = total_row[0] or 0
                total_pnl = round(total_row[1] or 0, 2)
                total_wins = total_row[2] or 0
                total_wr = round(total_wins / total_trades * 100, 1) if total_trades > 0 else 0

                # Bugünkü sinyaller
                signals = conn.execute(
                    "SELECT COUNT(*), "
                    "SUM(CASE WHEN decision = 'ALLOW' THEN 1 ELSE 0 END), "
                    "SUM(CASE WHEN decision = 'VETO' THEN 1 ELSE 0 END) "
                    "FROM signal_candidates WHERE DATE(created_at) = ?",
                    (today,)
                ).fetchone()
                total_signals = signals[0] or 0
                allowed = signals[1] or 0
                vetoed = signals[2] or 0

                # Balance
                balance_row = conn.execute(
                    "SELECT balance FROM paper_account WHERE id = 1"
                ).fetchone()
                balance = round(balance_row[0], 2) if balance_row else 0

            health = self.get_system_health()

            report = (
                f"📊 <b>AX Günlük Rapor</b> — {today}\n"
                f"{'─' * 30}\n"
                f"💰 Bakiye: <b>${balance}</b>\n"
                f"📈 Bugün: {today_trades} trade | "
                f"{'🟢' if today_pnl >= 0 else '🔴'} {'+' if today_pnl >= 0 else ''}{today_pnl}$\n"
                f"🎯 Win Rate: {today_wins}/{today_trades}\n"
                f"📊 Açık: {open_count} pozisyon\n"
                f"{'─' * 30}\n"
                f"📋 Toplam: {total_trades} trade | WR: {total_wr}%\n"
                f"💵 Toplam PnL: {'+' if total_pnl >= 0 else ''}{total_pnl}$\n"
                f"{'─' * 30}\n"
                f"🔍 Sinyal: {total_signals} | ✅ {allowed} | 🚫 {vetoed}\n"
                f"⏱ Uptime: {health['uptime']}\n"
                f"💾 DB: {health['db_size_mb']}MB\n"
                f"🔄 Restart: {self._restart_count} | ⚠️ Error: {self._error_count}"
            )
            return report
        except Exception as e:
            logger.error(f"[Watchdog] Rapor hatası: {e}")
            return f"⚠️ Rapor oluşturulamadı: {e}"

    def _should_send_daily_report(self) -> bool:
        """Günlük rapor zamanı geldi mi?"""
        now = datetime.now(timezone.utc)
        if now.hour != self._daily_report_hour:
            return False
        today = now.strftime("%Y-%m-%d")
        if self._last_daily_report == today:
            return False
        return True

    def _sd_notify(self, state: str) -> bool:
        """Sends a status notification to systemd UNIX socket if present."""
        import socket
        notify_socket = os.getenv("NOTIFY_SOCKET")
        if not notify_socket:
            return False
        try:
            if notify_socket.startswith("@"):
                notify_socket = "\0" + notify_socket[1:]
            with socket.socket(socket.AF_UNIX, socket.SOCK_DGRAM) as sock:
                sock.connect(notify_socket)
                sock.sendall(state.encode())
            return True
        except Exception:
            return False

    def _loop(self):
        """Ana watchdog döngüsü."""
        logger.info("[Watchdog] Başlatıldı.")
        self._sd_notify("READY=1")
        while self._running:
            try:
                self._sd_notify("WATCHDOG=1")
                # DB sağlık kontrolü
                self._check_db_health()

                # Günlük rapor
                if self._should_send_daily_report():
                    report = self._generate_daily_report()
                    try:
                        from telegram_delivery import send_message
                        send_message(report)
                        self._last_daily_report = datetime.now(timezone.utc).strftime("%Y-%m-%d")
                        logger.info("[Watchdog] Günlük rapor gönderildi.")
                    except Exception as e:
                        logger.error(f"[Watchdog] Telegram rapor hatası: {e}")

            except Exception as e:
                logger.error(f"[Watchdog] Loop hatası: {e}")
                self._error_count += 1
                
            # Panic Shutdown Mode Check
            self._check_panic_conditions()

            time.sleep(self.check_interval)

    def _check_panic_conditions(self):
        """Check for critical issues like high latency or too many errors and trigger panic shutdown if needed."""
        if self._error_count > 50:
            logger.critical("[Watchdog] ERROR THRESHOLD EXCEEDED. Triggering Panic Shutdown!")
            self._trigger_panic_shutdown("High Error Rate")
            return
            
        try:
            import time, requests
            start = time.time()
            r = requests.get("https://fapi.binance.com/fapi/v1/ping", timeout=5)
            latency = (time.time() - start) * 1000
            
            if latency > 2000: # 2 seconds latency is critical
                self._consecutive_latency_violations += 1
                logger.warning(
                    f"[Watchdog] High latency detected ({latency:.0f}ms) - "
                    f"violation {self._consecutive_latency_violations}/3"
                )
                if self._consecutive_latency_violations >= 3:
                    logger.critical(
                        f"[Watchdog] EXTREME LATENCY DETECTED FOR 3 CONSECUTIVE CHECKS ({latency:.0f}ms). "
                        f"Triggering Panic Shutdown!"
                    )
                    self._trigger_panic_shutdown(f"High Latency: {latency:.0f}ms (3 consecutive checks)")
            else:
                self._consecutive_latency_violations = 0
        except Exception as e:
            logger.warning(f"[Watchdog] Latency check failed: {e}")

    def _trigger_panic_shutdown(self, reason: str):
        """Emergency mode: Stops trading and sends critical alert."""
        try:
            # Broadcast PANIC event
            from core.event_bus import EventBus, EventType
            try:
                # We assume global event bus is accessible or we just log it and kill process
                EventBus.publish(EventType.SYSTEM_SHUTDOWN, {"reason": reason, "mode": "panic"})
            except Exception:
                pass
                
            from telegram_delivery import send_message
            send_message(f"🚨 <b>PANIC SHUTDOWN</b> 🚨\n\nSebep: {reason}\nSistem işlemleri durduruldu.")
            
            logger.critical(f"PANIC SHUTDOWN INITIATED: {reason}")
            # Exit process with error code
            import os
            os._exit(1)
        except Exception as e:
            logger.error(f"Panic shutdown trigger failed: {e}")
            import os
            os._exit(1)

    def start(self):
        if self._running:
            return
        self._running = True
        self._thread = threading.Thread(target=self._loop, daemon=True, name="watchdog")
        self._thread.start()
        logger.info("[Watchdog] Thread başlatıldı.")

    def stop(self):
        self._running = False
        logger.info("[Watchdog] Durduruldu.")
