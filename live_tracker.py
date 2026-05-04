"""
live_tracker.py — Trade Lifecycle Tracker
==========================================
Açık tradelerin fiyat hareketini takip eder.
record_open / record_close / get_live_stats fonksiyonları sağlar.
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
                CREATE TABLE IF NOT EXISTS live_tracker (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    trade_id INTEGER UNIQUE,
                    symbol TEXT,
                    direction TEXT,
                    entry REAL,
                    open_price REAL,
                    close_price REAL,
                    open_time TEXT,
                    close_time TEXT,
                    close_reason TEXT,
                    max_price REAL,
                    min_price REAL,
                    created_at TEXT DEFAULT (datetime('now'))
                )
            """)
    except Exception as e:
        logger.warning(f"[LiveTracker] init hatasi: {e}")

_init_table()

def record_open(trade_id: int, symbol: str, direction: str, entry: float):
    """Trade açıldığında kaydeder."""
    try:
        with _conn() as c:
            c.execute("""
                INSERT OR IGNORE INTO live_tracker
                    (trade_id, symbol, direction, entry, open_price, open_time, max_price, min_price)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """, (trade_id, symbol, direction, entry, entry,
                  datetime.now(timezone.utc).isoformat(), entry, entry))
    except Exception as e:
        logger.warning(f"[LiveTracker] record_open hatasi: {e}")

def record_close(trade_id: int, close_price: float, reason: str):
    """Trade kapandığında kaydeder."""
    try:
        with _conn() as c:
            c.execute("""
                UPDATE live_tracker SET
                    close_price=?, close_time=?, close_reason=?
                WHERE trade_id=?
            """, (close_price, datetime.now(timezone.utc).isoformat(), reason, trade_id))
    except Exception as e:
        logger.warning(f"[LiveTracker] record_close hatasi: {e}")

def update_price(trade_id: int, current_price: float):
    """Anlık fiyatı ve max/min günceller."""
    try:
        with _conn() as c:
            row = c.execute(
                "SELECT max_price, min_price FROM live_tracker WHERE trade_id=?", (trade_id,)
            ).fetchone()
            if row:
                new_max = max(row["max_price"] or current_price, current_price)
                new_min = min(row["min_price"] or current_price, current_price)
                c.execute(
                    "UPDATE live_tracker SET max_price=?, min_price=? WHERE trade_id=?",
                    (new_max, new_min, trade_id)
                )
    except Exception as e:
        logger.debug(f"[LiveTracker] update_price hatasi: {e}")

def get_live_stats(trade_id: int) -> dict:
    """Trade istatistiklerini döner."""
    try:
        with _conn() as c:
            row = c.execute(
                "SELECT * FROM live_tracker WHERE trade_id=?", (trade_id,)
            ).fetchone()
            return dict(row) if row else {}
    except Exception as e:
        logger.warning(f"[LiveTracker] get_live_stats hatasi: {e}")
        return {}
