#!/usr/bin/env python3
"""
scripts/migrate_accounting_schema.py
=====================================
Sunucudaki mevcut trading.db'ye accounting modülü için gerekli
kolonları güvenli şekilde ekler. Tekrar çalıştırılabilir (idempotent).

Kullanım:
    python3 scripts/migrate_accounting_schema.py
    python3 scripts/migrate_accounting_schema.py --db /path/to/trading.db
"""
import sys
import os
import sqlite3
import argparse
from datetime import datetime

# Proje kökünü path'e ekle
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

try:
    from config import DB_PATH
except ImportError:
    DB_PATH = "trading.db"

# ─── Eklenecek Kolonlar ───────────────────────────────────────────────────────
ACCOUNTING_COLUMNS = [
    # Tablo, Kolon, Tip+Default
    ("trades", "margin_used",      "REAL DEFAULT 0"),
    ("trades", "open_fee",         "REAL DEFAULT 0"),
    ("trades", "fee_rate",         "REAL DEFAULT 0.0004"),
    ("trades", "total_fee",        "REAL DEFAULT 0"),
    ("trades", "remaining_qty",    "REAL DEFAULT 0"),
    ("trades", "sl_dist",          "REAL DEFAULT 0"),
    ("trades", "max_loss_usd",     "REAL DEFAULT 0"),
    ("trades", "r_multiple",       "REAL DEFAULT 0"),
    ("trades", "exit_price",       "REAL DEFAULT 0"),
    ("trades", "notional_size",    "REAL DEFAULT 0"),
    ("trades", "risk_usd",         "REAL DEFAULT 0"),
    ("trades", "realized_pnl",     "REAL DEFAULT 0"),
    ("trades", "unrealized_pnl",   "REAL DEFAULT 0"),
    ("trades", "trade_stage",      "TEXT DEFAULT 'open'"),
    ("trades", "active_target",    "TEXT DEFAULT 'tp1'"),
    ("trades", "qty_tp1",          "REAL DEFAULT 0"),
    ("trades", "qty_tp2",          "REAL DEFAULT 0"),
    ("trades", "qty_runner",       "REAL DEFAULT 0"),
    ("trades", "tp1_hit",          "INTEGER DEFAULT 0"),
    ("trades", "tp2_hit",          "INTEGER DEFAULT 0"),
    ("trades", "trail_stop",       "REAL"),
    ("trades", "breakeven_sl",     "REAL"),
    ("trades", "result",           "TEXT DEFAULT ''"),
    ("trades", "hold_minutes",     "REAL DEFAULT 0"),
    ("trades", "close_price",      "REAL DEFAULT 0"),
    ("trades", "updated_at",       "TEXT DEFAULT (datetime('now'))"),
]

def get_existing_columns(conn: sqlite3.Connection, table: str) -> set:
    rows = conn.execute(f"PRAGMA table_info({table})").fetchall()
    return {row[1] for row in rows}

def run_migration(db_path: str):
    print(f"\n[Migration] DB: {db_path}")
    print(f"[Migration] Başlangıç: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")

    if not os.path.exists(db_path):
        print(f"[HATA] DB bulunamadı: {db_path}")
        sys.exit(1)

    conn = sqlite3.connect(db_path)
    added = 0
    skipped = 0

    for table, column, col_def in ACCOUNTING_COLUMNS:
        # Tablo var mı?
        tbl_exists = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name=?", (table,)
        ).fetchone()
        if not tbl_exists:
            print(f"  [SKIP]  Tablo yok: {table}")
            skipped += 1
            continue

        existing = get_existing_columns(conn, table)
        if column in existing:
            print(f"  [OK]    {table}.{column} zaten var")
            skipped += 1
        else:
            try:
                conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {col_def}")
                conn.commit()
                print(f"  [ADDED] {table}.{column} {col_def}")
                added += 1
            except sqlite3.OperationalError as e:
                print(f"  [HATA]  {table}.{column}: {e}")
                skipped += 1

    # remaining_qty'yi mevcut qty değerinden doldur (0 olanlar için)
    try:
        conn.execute("""
            UPDATE trades
            SET remaining_qty = qty
            WHERE remaining_qty = 0 AND qty > 0 AND status NOT IN
                ('closed','closed_win','closed_loss','sl','trail','tp3','timeout')
        """)
        conn.commit()
        print(f"\n  [FIX] Açık trade'lerin remaining_qty değerleri qty'den dolduruldu")
    except Exception as e:
        print(f"  [WARN] remaining_qty fix: {e}")

    conn.close()
    print(f"\n[Migration] Tamamlandı — Eklenen: {added} | Atlanan: {skipped}")

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Accounting schema migration")
    parser.add_argument("--db", default=None, help="DB dosya yolu (varsayılan: config.DB_PATH)")
    args = parser.parse_args()
    db_path = args.db or DB_PATH
    run_migration(db_path)
