"""
scripts/migrate_tenant_id.py — Faz 6.5 idempotent migration.

SaaS çoklu-kullanıcı temeli: environment kolonu olan tablolara tenant_id
(default 'main') ekler. Birden çok kez çalıştırılabilir (kolon varsa atlar).
İleride tenant-bazlı izolasyon migration acısı yaşanmaz.

Kullanım: python scripts/migrate_tenant_id.py
"""
import os
import sqlite3
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from config import DB_PATH  # noqa: E402

# environment kolonu olan tablolar → tenant_id eklenir
_TABLES = ["trades", "daily_summary"]


def migrate(db_path: str = "") -> None:
    path = db_path or DB_PATH
    print(f"[migrate_tenant_id] DB: {path}")
    conn = sqlite3.connect(path, timeout=30)
    try:
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA busy_timeout=5000")
        for table in _TABLES:
            # Tablo var mı?
            t = conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name=?", (table,)
            ).fetchone()
            if not t:
                print(f"  - {table}: tablo yok, atlandı")
                continue
            cols = {r[1] for r in conn.execute(f"PRAGMA table_info({table})").fetchall()}
            if "tenant_id" in cols:
                print(f"  - {table}: tenant_id zaten var")
                continue
            try:
                conn.execute(f"ALTER TABLE {table} ADD COLUMN tenant_id TEXT DEFAULT 'main'")
                # Mevcut satırları 'main' tenant'ına ata
                conn.execute(f"UPDATE {table} SET tenant_id='main' WHERE tenant_id IS NULL")
                print(f"  + {table}: tenant_id eklendi (default 'main')")
            except Exception as e:
                print(f"  ! {table}: tenant_id eklenemedi: {e}")
        conn.commit()
        print("[migrate_tenant_id] Tamamlandı.")
    finally:
        conn.close()


if __name__ == "__main__":
    migrate()
