#!/usr/bin/env python3
"""
DB Migration: Eksik sütunları ekler, mevcut veriye dokunmaz.
  - trades: result, net_pnl
  - pattern_memory: bb_width_chg, momentum_3c, prev_result
"""
import sqlite3
import os

DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "trading.db")

def migrate():
    print(f"[migrate] DB yolu: {DB_PATH}")
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()

    # ── trades tablosu ──────────────────────────────────────────────────────
    c.execute("PRAGMA table_info(trades)")
    trades_cols = [row[1] for row in c.fetchall()]
    print(f"[migrate] trades sütunları: {trades_cols}")

    added = []

    if "result" not in trades_cols:
        c.execute("ALTER TABLE trades ADD COLUMN result TEXT")
        added.append("trades.result")
        print("[migrate] ✓ trades.result eklendi")
    else:
        print("[migrate] trades.result zaten var")

    if "net_pnl" not in trades_cols:
        c.execute("ALTER TABLE trades ADD COLUMN net_pnl REAL DEFAULT 0.0")
        added.append("trades.net_pnl")
        print("[migrate] ✓ trades.net_pnl eklendi")
    else:
        print("[migrate] trades.net_pnl zaten var")

    # ── pattern_memory tablosu ──────────────────────────────────────────────
    c.execute("PRAGMA table_info(pattern_memory)")
    pm_cols = [row[1] for row in c.fetchall()]
    print(f"[migrate] pattern_memory sütunları: {pm_cols}")

    if "bb_width_chg" not in pm_cols:
        c.execute("ALTER TABLE pattern_memory ADD COLUMN bb_width_chg REAL DEFAULT 0")
        added.append("pattern_memory.bb_width_chg")
        print("[migrate] ✓ pattern_memory.bb_width_chg eklendi")
    else:
        print("[migrate] pattern_memory.bb_width_chg zaten var")

    if "momentum_3c" not in pm_cols:
        c.execute("ALTER TABLE pattern_memory ADD COLUMN momentum_3c REAL DEFAULT 0")
        added.append("pattern_memory.momentum_3c")
        print("[migrate] ✓ pattern_memory.momentum_3c eklendi")
    else:
        print("[migrate] pattern_memory.momentum_3c zaten var")

    if "prev_result" not in pm_cols:
        c.execute("ALTER TABLE pattern_memory ADD COLUMN prev_result TEXT DEFAULT 'NONE'")
        added.append("pattern_memory.prev_result")
        print("[migrate] ✓ pattern_memory.prev_result eklendi")
    else:
        print("[migrate] pattern_memory.prev_result zaten var")

    conn.commit()
    conn.close()

    if added:
        print(f"[migrate] TAMAMLANDI — Eklenen sütunlar: {added}")
    else:
        print("[migrate] TAMAMLANDI — Eklenecek sütun yoktu, her şey zaten güncel")

if __name__ == "__main__":
    migrate()
