"""
scripts/migrate_accounting_schema.py — DB Migration v5.0
========================================================
Mevcut DB'ye eksik kolonları ve tabloları ekler.
Eski gerçek veriyi ASLA silmez.
"""
import os
import sys
import sqlite3

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from config import DB_PATH
from database import init_db

def migrate():
    print(f"[Migration] DB: {DB_PATH}")

    # init_db tüm tabloları CREATE IF NOT EXISTS ile oluşturur
    init_db()

    # trades tablosuna eksik kolonları ekle
    trade_columns = {
        "qty_tp1": "REAL DEFAULT 0",
        "qty_tp2": "REAL DEFAULT 0",
        "qty_runner": "REAL DEFAULT 0",
        "notional_size": "REAL DEFAULT 0",
        "margin_used": "REAL DEFAULT 0",
        "risk_pct": "REAL DEFAULT 1.0",
        "risk_usd": "REAL DEFAULT 0",
        "max_loss_after_fee": "REAL DEFAULT 0",
        "unrealized_pnl": "REAL DEFAULT 0",
        "open_fee": "REAL DEFAULT 0",
        "close_fee": "REAL DEFAULT 0",
        "fee_rate": "REAL DEFAULT 0.0004",
        "duration_seconds": "INTEGER DEFAULT 0",
        "market_regime": "TEXT",
        "is_valid_for_stats": "INTEGER DEFAULT 1",
        "archived_reason": "TEXT",
        "tp3": "REAL",
        "remaining_qty": "REAL",
        "original_qty": "REAL",
    }

    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()

    # Mevcut kolonları al
    cursor.execute("PRAGMA table_info(trades)")
    existing = {row[1] for row in cursor.fetchall()}

    added = 0
    for col, col_type in trade_columns.items():
        if col not in existing:
            try:
                cursor.execute(f"ALTER TABLE trades ADD COLUMN {col} {col_type}")
                print(f"  [OK] trades.{col} eklendi")
                added += 1
            except Exception as e:
                print(f"  [WARN] trades.{col} eklenemedi: {e}")

    conn.commit()
    conn.close()

    if added == 0:
        print("[Migration] Tüm kolonlar zaten mevcut.")
    else:
        print(f"[Migration] {added} kolon eklendi.")

    print("[Migration] Tamamlandi.")


if __name__ == "__main__":
    migrate()
