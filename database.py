"""
database.py — AX Merkezi Veritabanı v4.10 (ULTIMATE ELITE)
=========================================================
Aşama 10: MFE / MAE / R-Multiple Analizleri ve İstatistikler.
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
            close_reason TEXT,
            mfe REAL DEFAULT 0,
            mae REAL DEFAULT 0,
            r_multiple REAL DEFAULT 0,
            setup_quality TEXT,
            final_score REAL
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
        
        CREATE TABLE IF NOT EXISTS ai_logs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            event TEXT,
            symbol TEXT,
            decision TEXT,
            score REAL,
            confidence REAL,
            reason TEXT,
            data TEXT,
            timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
        """)

def add_ledger_entry(trade_id, symbol, event_type, amount, note=""):
    with get_conn() as conn:
        row = conn.execute("SELECT balance FROM paper_account WHERE id=1").fetchone()
        balance_before = row[0]
        balance_after = balance_before + amount
        conn.execute("UPDATE paper_account SET balance = ? WHERE id=1", (balance_after,))
        conn.execute("""
            INSERT INTO balance_ledger (trade_id, symbol, event_type, amount, balance_before, balance_after, note)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        """, (trade_id, symbol, event_type, amount, balance_before, balance_after, note))
        return balance_after

def update_trade_stats(trade_id, mfe=None, mae=None):
    with get_conn() as conn:
        if mfe is not None:
            conn.execute("UPDATE trades SET mfe = MAX(mfe, ?) WHERE id = ?", (mfe, trade_id))
        if mae is not None:
            conn.execute("UPDATE trades SET mae = MIN(mae, ?) WHERE id = ?", (mae, trade_id))

def close_trade(trade_id, final_pnl, final_fee, reason, r_multiple=0):
    with get_conn() as conn:
        conn.execute("""
            UPDATE trades SET 
            status = 'closed',
            net_pnl = realized_pnl + ?,
            total_fee = total_fee + ?,
            remaining_qty = 0,
            close_time = datetime('now'),
            close_reason = ?,
            r_multiple = ?
            WHERE id = ?
        """, (final_pnl, final_fee, reason, r_multiple, trade_id))

def get_open_trades():
    with get_conn() as conn:
        rows = conn.execute("SELECT * FROM trades WHERE status != 'closed'").fetchall()
        return [dict(r) for r in rows]

def save_ai_log(event, symbol, decision, score, confidence, reason, data):
    with get_conn() as conn:
        conn.execute("""
            INSERT INTO ai_logs (event, symbol, decision, score, confidence, reason, data)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        """, (event, symbol, decision, score, confidence, reason, data))
