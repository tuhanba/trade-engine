"""
scripts/daily_health_report.py — Günlük Otomatik Sağlık Raporu v1.0
Cron veya systemd timer ile her gün 08:00 UTC çalıştırılır.
"""
import sys
import os
import sqlite3
import logging
from datetime import datetime, timezone, timedelta

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from config import DB_PATH

log_path = os.path.join(os.path.dirname(DB_PATH), "health_report.log")
logging.basicConfig(
    filename=log_path, level=logging.INFO,
    format="%(asctime)s %(message)s"
)
logger = logging.getLogger(__name__)


def generate():
    now       = datetime.now(timezone.utc)
    yesterday = (now - timedelta(days=1)).strftime("%Y-%m-%d")

    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row

    bal = conn.execute(
        "SELECT balance, initial_balance FROM paper_account WHERE id=1"
    ).fetchone()
    balance = float(bal["balance"]) if bal else 0.0
    initial = float(bal["initial_balance"]) if bal else 250.0
    net_gain = balance - initial

    yd = conn.execute("""
        SELECT COUNT(*) as n,
               SUM(CASE WHEN net_pnl > 0 THEN 1 ELSE 0 END) as wins,
               SUM(net_pnl) as pnl, SUM(total_fee) as fees
        FROM trades
        WHERE status='closed' AND DATE(close_time)=? AND is_valid_for_stats=1
    """, (yesterday,)).fetchone()

    open_c = conn.execute(
        "SELECT COUNT(*) FROM trades WHERE status NOT IN ('closed')"
    ).fetchone()[0]

    ledger_sum = float(conn.execute(
        "SELECT COALESCE(SUM(amount), 0) FROM balance_ledger"
    ).fetchone()[0])
    audit_ok = abs((initial + ledger_sum) - balance) < 0.01

    hb = conn.execute(
        "SELECT value FROM system_state WHERE key='bot_heartbeat_at'"
    ).fetchone()
    bot_ok = False
    if hb:
        try:
            hb_dt = datetime.fromisoformat(hb["value"].replace("Z", "+00:00"))
            bot_ok = (now - hb_dt).total_seconds() < 300
        except Exception:
            pass

    conn.close()

    n   = yd["n"] or 0
    pnl = float(yd["pnl"] or 0)
    wr  = round((yd["wins"] or 0) / n * 100, 1) if n > 0 else 0.0

    report = (
        f"GUNLUK RAPOR — {yesterday}\n"
        f"{'='*30}\n"
        f"Dun: {n} trade | {wr}% WR | {'+' if pnl >= 0 else ''}{pnl:.4f}\n"
        f"Bakiye: ${balance:.2f} ({'+' if net_gain >= 0 else ''}{net_gain:.2f})\n"
        f"Acik: {open_c} | Audit: {'OK' if audit_ok else 'HATALI'} | "
        f"Bot: {'AKTIF' if bot_ok else 'KAPALI'}\n"
        f"[PAPER/DRY-RUN]"
    )

    logger.info(report.replace("\n", " | "))

    try:
        from telegram_delivery import _send
        _send(report)
    except Exception as e:
        logger.warning(f"Telegram: {e}")

    print(report)


if __name__ == "__main__":
    generate()
