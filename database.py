import sqlite3
import json
import logging
import os
from datetime import datetime, timezone

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
        CREATE TABLE IF NOT EXISTS daily_summary (
            date TEXT PRIMARY KEY, 
            trade_count INTEGER, 
            win_count INTEGER, 
            loss_count INTEGER, 
            win_rate REAL, 
            gross_pnl REAL, 
            net_pnl REAL, 
            avg_r REAL, 
            max_drawdown REAL, 
            balance_eod REAL
        );
        CREATE TABLE IF NOT EXISTS weekly_summary (
            week_start TEXT PRIMARY KEY, 
            trade_count INTEGER, 
            win_count INTEGER, 
            loss_count INTEGER, 
            win_rate REAL, 
            net_pnl REAL, 
            avg_r REAL, 
            best_day TEXT, 
            worst_day TEXT
        );
        CREATE TABLE IF NOT EXISTS coin_profiles (
            symbol TEXT PRIMARY KEY, 
            win_rate REAL DEFAULT 0, 
            total_trades INTEGER DEFAULT 0, 
            total_pnl REAL DEFAULT 0, 
            danger_score REAL DEFAULT 0, 
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
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
            "win_rate": 0
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
    pass

def save_paper_trade(sig_dict, tracked_from="ghost"):
    with get_conn() as conn:
        conn.execute("""
            INSERT INTO signal_candidates (symbol, direction, entry, sl, tp1, score, decision, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, datetime('now'))
        """, (sig_dict['symbol'], sig_dict['direction'], sig_dict['entry_zone'], sig_dict['stop_loss'], sig_dict['tp1'], sig_dict.get('final_score', 0), tracked_from))

def save_daily_summary(data):
    with get_conn() as conn:
        conn.execute("""
            INSERT OR REPLACE INTO daily_summary (date, trade_count, win_count, loss_count, win_rate, gross_pnl, net_pnl, avg_r, max_drawdown, balance_eod)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (data['date'], data['trade_count'], data['win_count'], data['loss_count'], data['win_rate'], data['gross_pnl'], data['net_pnl'], data['avg_r'], data['max_drawdown'], data['balance_eod']))

def save_weekly_summary(data):
    with get_conn() as conn:
        conn.execute("""
            INSERT OR REPLACE INTO weekly_summary (week_start, trade_count, win_count, loss_count, win_rate, net_pnl, avg_r, best_day, worst_day)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (data['week_start'], data['trade_count'], data['win_count'], data['loss_count'], data['win_rate'], data['net_pnl'], data['avg_r'], data['best_day'], data['worst_day']))

def save_market_snapshot(data):
    pass

def save_scanned_coin(data):
    pass
