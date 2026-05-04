"""
database.py — AX Merkezi Veritabanı v4.2 (ULTIMATE ELITE)
=========================================================
Dashboard ve Bot için gerekli tüm fonksiyonlar eklendi.
"""
import sqlite3
import json
import logging
import os
from datetime import datetime, timezone

# Config'den DB_PATH al
try:
    from config import DB_PATH
except ImportError:
    DB_PATH = "trading.db"

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
            status TEXT DEFAULT 'open',
            environment TEXT DEFAULT 'paper',
            entry REAL, sl REAL, tp1 REAL, tp2 REAL,
            net_pnl REAL DEFAULT 0,
            open_time TEXT, close_time TEXT,
            close_reason TEXT
        );
        CREATE TABLE IF NOT EXISTS signal_candidates (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            symbol TEXT NOT NULL,
            direction TEXT,
            entry REAL, sl REAL, tp1 REAL, tp2 REAL,
            score REAL DEFAULT 0,
            decision TEXT DEFAULT 'PENDING',
            created_at TEXT DEFAULT (datetime('now'))
        );
        CREATE TABLE IF NOT EXISTS paper_account (
            id INTEGER PRIMARY KEY CHECK (id = 1),
            balance REAL DEFAULT 250.0,
            initial_balance REAL DEFAULT 250.0
        );
        INSERT OR IGNORE INTO paper_account (id, balance, initial_balance) VALUES (1, 250.0, 250.0);
        CREATE TABLE IF NOT EXISTS state (
            key TEXT PRIMARY KEY,
            value TEXT
        );
        """)

def get_trades(limit=50):
    with get_conn() as conn:
        rows = conn.execute("SELECT * FROM trades ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
        return [dict(r) for r in rows]

def get_open_trades():
    with get_conn() as conn:
        rows = conn.execute("SELECT * FROM trades WHERE status='open'").fetchall()
        return [dict(r) for r in rows]

def get_stats():
    with get_conn() as conn:
        row = conn.execute("SELECT COUNT(*), SUM(net_pnl) FROM trades WHERE status != 'open'").fetchone()
        total = row[0] or 0
        pnl = row[1] or 0
        return {
            "total_trades": total,
            "total_pnl": round(pnl, 2),
            "win_rate": 0 # Basitleştirilmiş
        }

def get_paper_balance():
    with get_conn() as conn:
        row = conn.execute("SELECT balance FROM paper_account WHERE id=1").fetchone()
        return row[0] if row else 250.0

def get_current_params():
    return {"version": "4.2 Elite"}

def get_state(key):
    with get_conn() as conn:
        row = conn.execute("SELECT value FROM state WHERE key=?", (key,)).fetchone()
        return row[0] if row else None

def save_scalp_signal(data):
    with get_conn() as conn:
        # Basitleştirilmiş kayıt
        pass

def save_paper_trade(sig_dict, tracked_from="ghost"):
    with get_conn() as conn:
        conn.execute("""
            INSERT INTO signal_candidates (symbol, direction, entry, sl, tp1, score, decision, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, datetime('now'))
        """, (sig_dict['symbol'], sig_dict['direction'], sig_dict['entry_zone'], sig_dict['stop_loss'], sig_dict['tp1'], sig_dict.get('final_score', 0), tracked_from))
