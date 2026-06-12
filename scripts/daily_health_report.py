"""
scripts/daily_health_report.py — Günlük Otomatik Sağlık Raporu v2.0
Signal funnel tablosu + reject_reason analizi ile genişletildi.
"""
import sys
import os
import sqlite3
import logging
from datetime import datetime, timezone, timedelta

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from config import DB_PATH

log_path = os.path.join(os.path.dirname(DB_PATH) or ".", "health_report.log")
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
    conn.execute("PRAGMA busy_timeout=5000")

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
        "SELECT COUNT(*) FROM trades WHERE LOWER(status) NOT IN ('closed')"
    ).fetchone()[0]

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

    # ── Signal funnel (last 24h) ─────────────────────────────────────
    funnel_rows = conn.execute("""
        SELECT stage, COUNT(*) as cnt
        FROM signal_events
        WHERE created_at >= datetime('now', '-1 day')
        GROUP BY stage
        ORDER BY cnt DESC
    """).fetchall()
    funnel = {r["stage"]: r["cnt"] for r in funnel_rows}

    reject_rows = conn.execute("""
        SELECT reject_reason, COUNT(*) as cnt
        FROM signal_events
        WHERE created_at >= datetime('now', '-1 day')
          AND reject_reason IS NOT NULL AND reject_reason != ''
        GROUP BY reject_reason
        ORDER BY cnt DESC
        LIMIT 5
    """).fetchall()

    conn.close()

    n   = yd["n"] or 0
    pnl = float(yd["pnl"] or 0)
    wr  = round((yd["wins"] or 0) / n * 100, 1) if n > 0 else 0.0

    # ── Funnel table ─────────────────────────────────────────────────
    stage_order = ["SCANNED", "TREND_CHECKED", "TRIGGER_CHECKED",
                   "RISK_APPROVED", "AI_VALIDATED", "EXECUTION_REJECTED", "EXECUTED"]
    funnel_lines = []
    for s in stage_order:
        cnt = funnel.get(s, 0)
        if cnt > 0:
            funnel_lines.append(f"  {s:<22} {cnt}")

    reject_lines = [f"  {r['reject_reason'][:35]:<35} {r['cnt']}"
                    for r in reject_rows]

    report = (
        f"📊 <b>GÜNLÜK RAPOR — {yesterday}</b>\n"
        f"{'─'*30}\n"
        f"Dün: <b>{n} trade</b> | WR: <b>{wr}%</b> | PnL: <b>{'+' if pnl >= 0 else ''}{pnl:.4f}</b>\n"
        f"Bakiye: <b>${balance:.2f}</b> ({'+' if net_gain >= 0 else ''}{net_gain:.2f})\n"
        f"Açık: {open_c} | Bot: {'🟢' if bot_ok else '🔴'}\n"
        f"{'─'*30}\n"
        f"<b>Sinyal Funnel (24s):</b>\n" +
        ("\n".join(f"<code>{l}</code>" for l in funnel_lines) or "  (veri yok)") +
        f"\n{'─'*30}\n"
        f"<b>En Sık Ret Nedenleri:</b>\n" +
        ("\n".join(f"<code>{l}</code>" for l in reject_lines) or "  (ret yok)") +
        f"\n[PAPER/DRY-RUN]"
    )

    logger.info(report.replace("\n", " | ").replace("<b>", "").replace("</b>", "").replace("<code>", "").replace("</code>", ""))

    try:
        import telegram_delivery
        telegram_delivery.send_message(report)
        logger.info("Telegram report sent.")
    except Exception as e:
        logger.warning(f"Telegram: {e}")

    print(report.replace("<b>", "").replace("</b>", "").replace("<code>", "").replace("</code>", ""))


if __name__ == "__main__":
    generate()
