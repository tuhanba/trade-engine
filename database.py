"""
database.py — AX Merkezi Veritabanı v4.4 (ULTIMATE ELITE)
=========================================================
Aşama 4: TP Lifecycle ve Balance Ledger Entegrasyonu.
"""
import sqlite3
import logging
from datetime import datetime
from config import DB_PATH

logger = logging.getLogger(__name__)

def get_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH, timeout=30, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    return conn

def init_db():
    with get_conn() as conn:
        conn.executescript("""
        CREATE TABLE IF NOT EXISTS trades (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            symbol TEXT NOT NULL,
            direction TEXT NOT NULL,
            status TEXT DEFAULT 'open', -- open, tp1_hit, tp2_hit, closed
            entry REAL, sl REAL, tp1 REAL, tp2 REAL, tp3 REAL,
            original_qty REAL, remaining_qty REAL,
            leverage INTEGER,
            realized_pnl REAL DEFAULT 0,
            net_pnl REAL DEFAULT 0,
            total_fee REAL DEFAULT 0,
            tp1_hit INTEGER DEFAULT 0,
            tp2_hit INTEGER DEFAULT 0,
            open_time TEXT, close_time TEXT,
            close_reason TEXT
        );
        CREATE TABLE IF NOT EXISTS balance_ledger (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            trade_id INTEGER,
            symbol TEXT,
            event_type TEXT, -- TP1, TP2, FINAL, STOP, FEE
            amount REAL,
            balance_before REAL,
            balance_after REAL,
            timestamp TEXT DEFAULT (datetime('now')),
            note TEXT
        );
        CREATE TABLE IF NOT EXISTS paper_account (
            id INTEGER PRIMARY KEY CHECK (id = 1),
            balance REAL DEFAULT 250.0,
            initial_balance REAL DEFAULT 250.0
        );
        INSERT OR IGNORE INTO paper_account (id, balance, initial_balance) VALUES (1, 250.0, 250.0);
        """)

def add_ledger_entry(trade_id, symbol, event_type, amount, note=""):
    with get_conn() as conn:
        # Mevcut bakiyeyi al
        row = conn.execute("SELECT balance FROM paper_account WHERE id=1").fetchone()
        balance_before = row[0]
        balance_after = balance_before + amount
        
        # Bakiyeyi güncelle
        conn.execute("UPDATE paper_account SET balance = ? WHERE id=1", (balance_after,))
        
        # Ledger kaydı at
        conn.execute("""
            INSERT INTO balance_ledger (trade_id, symbol, event_type, amount, balance_before, balance_after, note)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        """, (trade_id, symbol, event_type, amount, balance_before, balance_after, note))
        return balance_after

def update_trade_tp(trade_id, tp_level, pnl, fee, remaining_qty):
    with get_conn() as conn:
        if tp_level == 1:
            conn.execute("""
                UPDATE trades SET 
                tp1_hit = 1, 
                realized_pnl = realized_pnl + ?, 
                total_fee = total_fee + ?,
                remaining_qty = ?,
                status = 'tp1_hit'
                WHERE id = ?
            """, (pnl, fee, remaining_qty, trade_id))
        elif tp_level == 2:
            conn.execute("""
                UPDATE trades SET 
                tp2_hit = 1, 
                realized_pnl = realized_pnl + ?, 
                total_fee = total_fee + ?,
                remaining_qty = ?,
                status = 'tp2_hit'
                WHERE id = ?
            """, (pnl, fee, remaining_qty, trade_id))

def close_trade(trade_id, final_pnl, final_fee, reason):
    with get_conn() as conn:
        conn.execute("""
            UPDATE trades SET 
            status = 'closed',
            net_pnl = realized_pnl + ?,
            total_fee = total_fee + ?,
            remaining_qty = 0,
            close_time = datetime('now'),
            close_reason = ?
            WHERE id = ?
        """, (final_pnl, final_fee, reason, trade_id))

# Diğer yardımcı fonksiyonlar (Aşama 6 için)
def get_open_trades():
    with get_conn() as conn:
        rows = conn.execute("SELECT * FROM trades WHERE status != 'closed'").fetchall()
        return [dict(r) for r in rows]
