"""
ai_brain.py — AI Post-Trade Analysis Brain
===========================================
Her kapanan trade için postmortem analiz yapar.
AI Decision Engine'i besler.
"""
import sqlite3
import logging
from datetime import datetime, timezone
from config import DB_PATH

logger = logging.getLogger(__name__)

def _conn():
    c = sqlite3.connect(DB_PATH, timeout=30, check_same_thread=False)
    c.row_factory = sqlite3.Row
    c.execute("PRAGMA journal_mode=WAL")
    return c

def _init_table():
    try:
        with _conn() as c:
            c.execute("""
                CREATE TABLE IF NOT EXISTS ai_postmortem (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    trade_id INTEGER UNIQUE,
                    symbol TEXT,
                    direction TEXT,
                    result TEXT,
                    net_pnl REAL,
                    entry REAL,
                    close_price REAL,
                    mfe_r REAL DEFAULT 0,
                    mae_r REAL DEFAULT 0,
                    hold_minutes REAL DEFAULT 0,
                    setup_quality TEXT,
                    notes TEXT,
                    created_at TEXT DEFAULT (datetime('now'))
                )
            """)
    except Exception as e:
        logger.warning(f"[AIBrain] init hatasi: {e}")

_init_table()

def post_trade_analysis(trade_id: int):
    """
    Trade kapandıktan sonra postmortem analiz yapar.
    Execution engine tarafından ayrı thread'de çağrılır.
    """
    try:
        with _conn() as c:
            trade = c.execute("SELECT * FROM trades WHERE id=?", (trade_id,)).fetchone()
            if not trade:
                logger.warning(f"[AIBrain] Trade #{trade_id} bulunamadi")
                return

            t = dict(trade)
            symbol    = t.get("symbol", "")
            direction = t.get("direction", "")
            entry     = t.get("entry", 0) or 0
            close_p   = t.get("current_price", 0) or entry
            net_pnl   = t.get("net_pnl", 0) or 0
            sl        = t.get("sl", 0) or 0
            result    = "WIN" if net_pnl > 0 else "LOSS"
            setup_q   = t.get("setup_quality", "B") or "B"

            # MFE/MAE hesapla (entry/sl bazli)
            sl_dist = abs(entry - sl) if sl else 1e-10
            if direction == "LONG":
                mfe_r = round(max(0, close_p - entry) / sl_dist, 3)
                mae_r = round(max(0, entry - close_p) / sl_dist, 3)
            else:
                mfe_r = round(max(0, entry - close_p) / sl_dist, 3)
                mae_r = round(max(0, close_p - entry) / sl_dist, 3)

            # Hold süresi
            hold_min = 0.0
            open_t = t.get("open_time")
            close_t = t.get("close_time")
            if open_t and close_t:
                try:
                    ot = datetime.fromisoformat(open_t.replace("Z", "+00:00"))
                    ct = datetime.fromisoformat(close_t.replace("Z", "+00:00"))
                    hold_min = (ct - ot).total_seconds() / 60
                except Exception:
                    pass

            # Postmortem kaydet
            c.execute("""
                INSERT OR REPLACE INTO ai_postmortem
                    (trade_id, symbol, direction, result, net_pnl, entry, close_price,
                     mfe_r, mae_r, hold_minutes, setup_quality, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, datetime('now'))
            """, (trade_id, symbol, direction, result, net_pnl, entry, close_p,
                  mfe_r, mae_r, hold_min, setup_q))

            # trade_postmortem tablosuna da yaz (execution_engine okur)
            c.execute("""
                INSERT OR REPLACE INTO trade_postmortem (trade_id, mfe_r, mae_r, created_at)
                VALUES (?, ?, ?, datetime('now'))
            """, (trade_id, mfe_r, mae_r))

            logger.info(
                f"[AIBrain] Postmortem #{trade_id} {symbol} {result} "
                f"pnl={net_pnl:+.3f}$ mfe={mfe_r:.2f}R mae={mae_r:.2f}R hold={hold_min:.0f}dk"
            )

    except Exception as e:
        logger.warning(f"[AIBrain] post_trade_analysis #{trade_id} hatasi: {e}")

def get_brain_stats(symbol: str = None) -> dict:
    """AI beyin istatistiklerini döner."""
    try:
        with _conn() as c:
            if symbol:
                rows = c.execute(
                    "SELECT * FROM ai_postmortem WHERE symbol=? ORDER BY created_at DESC LIMIT 50",
                    (symbol,)
                ).fetchall()
            else:
                rows = c.execute(
                    "SELECT * FROM ai_postmortem ORDER BY created_at DESC LIMIT 100"
                ).fetchall()
            return [dict(r) for r in rows]
    except Exception as e:
        logger.warning(f"[AIBrain] get_brain_stats hatasi: {e}")
        return []
