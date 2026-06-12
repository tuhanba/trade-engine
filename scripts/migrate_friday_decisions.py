"""
scripts/migrate_friday_decisions.py — Faz 2.1 idempotent migration.

friday_decisions tablosunu ve indexini oluşturur. Birden çok kez
çalıştırılabilir (CREATE TABLE IF NOT EXISTS) — veri kaybı riski yok.

Kullanım: python scripts/migrate_friday_decisions.py
"""
import os
import sqlite3
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from config import DB_PATH  # noqa: E402
from core.friday_decisions import FRIDAY_DECISIONS_DDL, FRIDAY_DECISIONS_INDEX_DDL  # noqa: E402


def migrate(db_path: str = "") -> None:
    path = db_path or DB_PATH
    print(f"[migrate_friday_decisions] DB: {path}")
    conn = sqlite3.connect(path, timeout=30)
    try:
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA busy_timeout=5000")
        conn.execute(FRIDAY_DECISIONS_DDL)
        conn.execute(FRIDAY_DECISIONS_INDEX_DDL)
        conn.commit()
        # Doğrulama
        row = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='friday_decisions'"
        ).fetchone()
        if row:
            cnt = conn.execute("SELECT COUNT(*) FROM friday_decisions").fetchone()[0]
            print(f"[migrate_friday_decisions] OK — tablo mevcut, {cnt} kayıt.")
        else:
            print("[migrate_friday_decisions] HATA — tablo oluşturulamadı!")
            sys.exit(1)
    finally:
        conn.close()


if __name__ == "__main__":
    migrate()
