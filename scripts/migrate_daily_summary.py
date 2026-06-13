"""
scripts/migrate_daily_summary.py — Faz 3.1 idempotent migration.

daily_summary tablosuna expectancy + funnel kolonlarını ekler. Mevcut tablo
korunur (CREATE TABLE IF NOT EXISTS + ALTER TABLE try/except). Birden çok kez
çalıştırılabilir — veri kaybı riski yok.

Kullanım: python scripts/migrate_daily_summary.py
"""
import os
import sqlite3
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from config import DB_PATH  # noqa: E402

# Faz 3.1 ile eklenen kolonlar (database._EXPECTED_COLUMNS["daily_summary"] ile eş)
_NEW_COLUMNS = [
    ("expectancy_r",     "REAL DEFAULT 0"),
    ("funnel_scanned",   "INTEGER DEFAULT 0"),
    ("funnel_candidate", "INTEGER DEFAULT 0"),
    ("funnel_telegram",  "INTEGER DEFAULT 0"),
    ("funnel_executed",  "INTEGER DEFAULT 0"),
    ("environment",      "TEXT DEFAULT 'paper'"),
]

_CREATE = """
CREATE TABLE IF NOT EXISTS daily_summary (
    date         TEXT PRIMARY KEY,
    trade_count  INTEGER DEFAULT 0,
    win_count    INTEGER DEFAULT 0,
    loss_count   INTEGER DEFAULT 0,
    win_rate     REAL DEFAULT 0,
    gross_pnl    REAL DEFAULT 0,
    net_pnl      REAL DEFAULT 0,
    avg_r        REAL DEFAULT 0,
    max_drawdown REAL DEFAULT 0,
    balance_eod  REAL DEFAULT 0,
    sent         INTEGER DEFAULT 0,
    best_coin    TEXT DEFAULT '',
    worst_coin   TEXT DEFAULT ''
)
"""


def migrate(db_path: str = "") -> None:
    path = db_path or DB_PATH
    print(f"[migrate_daily_summary] DB: {path}")
    conn = sqlite3.connect(path, timeout=30)
    try:
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA busy_timeout=5000")
        conn.execute(_CREATE)
        existing = {r[1] for r in conn.execute("PRAGMA table_info(daily_summary)").fetchall()}
        added = []
        for col, ddl in _NEW_COLUMNS:
            if col not in existing:
                try:
                    conn.execute(f"ALTER TABLE daily_summary ADD COLUMN {col} {ddl}")
                    added.append(col)
                except Exception as e:
                    print(f"  ! {col} eklenemedi: {e}")
        conn.commit()
        print(f"[migrate_daily_summary] OK — eklenen kolonlar: {added or 'yok (zaten güncel)'}")
    finally:
        conn.close()


if __name__ == "__main__":
    migrate()
