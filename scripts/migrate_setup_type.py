"""scripts/migrate_setup_type.py — P1-1b idempotent migration.

Adds setup_type / setup_reason columns to `trades` and `signal_candidates` so
the mandatory setup taxonomy (directive Section 4/9) is persisted for per-setup
expectancy reporting. Safe to run multiple times (skips existing columns).

Usage: python scripts/migrate_setup_type.py
"""
import os
import sqlite3
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from config import DB_PATH  # noqa: E402

# table -> [(column, "DDL type + default"), ...]
_COLUMNS = {
    "trades": [
        ("setup_type", "TEXT DEFAULT 'UNKNOWN'"),
        ("setup_reason", "TEXT DEFAULT ''"),
    ],
    "signal_candidates": [
        ("setup_type", "TEXT DEFAULT 'UNKNOWN'"),
        ("setup_reason", "TEXT DEFAULT ''"),
    ],
}


def migrate(db_path: str = "") -> None:
    path = db_path or DB_PATH
    print(f"[migrate_setup_type] DB: {path}")
    conn = sqlite3.connect(path, timeout=30)
    try:
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA busy_timeout=5000")
        for table, cols in _COLUMNS.items():
            t = conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name=?", (table,)
            ).fetchone()
            if not t:
                print(f"  - {table}: tablo yok, atlandı")
                continue
            existing = {r[1] for r in conn.execute(f"PRAGMA table_info({table})").fetchall()}
            for col, ddl in cols:
                if col in existing:
                    print(f"  - {table}.{col}: zaten var")
                    continue
                try:
                    conn.execute(f"ALTER TABLE {table} ADD COLUMN {col} {ddl}")
                    print(f"  + {table}.{col} eklendi")
                except Exception as e:
                    print(f"  ! {table}.{col} eklenemedi: {e}")
        conn.commit()
        print("[migrate_setup_type] Tamamlandı.")
    finally:
        conn.close()


if __name__ == "__main__":
    migrate()
