"""
database.py — AX Merkezi Veritabanı
=====================================
WAL mode, tek writer, tüm tablolar burada.
"""
import sqlite3
import json
import logging
import uuid
from datetime import datetime, timezone, date, timedelta
from config import DB_PATH

logger = logging.getLogger(__name__)

def get_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH, timeout=30, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    conn.execute("PRAGMA foreign_keys=ON")
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
            realized_pnl REAL DEFAULT 0,
            open_time TEXT, close_time TEXT
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
        CREATE TABLE IF NOT EXISTS ai_logs (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            event TEXT, symbol TEXT, decision TEXT, score REAL, confidence REAL, reason TEXT, data TEXT,
            created_at TEXT DEFAULT (datetime('now'))
        );
        CREATE TABLE IF NOT EXISTS best_params (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            data TEXT, params_json TEXT, win_rate REAL DEFAULT 0, profit_factor REAL DEFAULT 0,
            created_at TEXT DEFAULT (datetime('now'))
        );
        CREATE TABLE IF NOT EXISTS scalp_signals (
            id TEXT PRIMARY KEY, symbol TEXT NOT NULL, timestamp REAL, direction TEXT,
            entry_zone REAL, stop_loss REAL, tp1 REAL, tp2 REAL, tp3 REAL,
            setup_quality TEXT, coin_score REAL, trend_score REAL, trigger_score REAL, risk_score REAL,
            confidence REAL, status TEXT DEFAULT 'pending', reason TEXT,
            created_at TEXT DEFAULT (datetime('now'))
        );
        """)

def get_paper_balance():
    with get_conn() as conn:
        row = conn.execute("SELECT balance FROM paper_account WHERE id=1").fetchone()
        return row[0] if row else 250.0

def get_open_trades():
    with get_conn() as conn:
        return conn.execute("SELECT * FROM trades WHERE status='open'").fetchall()

def save_scalp_signal(data):
    with get_conn() as conn:
        conn.execute("""
            INSERT INTO scalp_signals (id, symbol, timestamp, direction, entry_zone, stop_loss, tp1, tp2, tp3, setup_quality, coin_score, trend_score, trigger_score, risk_score, confidence, reason)
            VALUES (:id, :symbol, :timestamp, :direction, :entry_zone, :stop_loss, :tp1, :tp2, :tp3, :setup_quality, :coin_score, :trend_score, :trigger_score, :risk_score, :confidence, :reason)
        """, data)

def save_ai_log(event, symbol, decision, score, confidence, reason, data):
    with get_conn() as conn:
        conn.execute("""
            INSERT INTO ai_logs (event, symbol, decision, score, confidence, reason, data)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        """, (event, symbol, decision, score, confidence, reason, data))

def save_scanned_coin(symbol, score, decision, reason):
    with get_conn() as conn:
        conn.execute("""
            INSERT INTO signal_candidates (symbol, score, decision, created_at)
            VALUES (?, ?, ?, datetime('now'))
        """, (symbol, score, decision))

def save_paper_trade(sig_dict, tracked_from="ghost"):
    """Ghost trading verilerini kaydeder."""
    with get_conn() as conn:
        conn.execute("""
            INSERT INTO signal_candidates (symbol, direction, entry, sl, tp1, score, decision, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, datetime('now'))
        """, (sig_dict['symbol'], sig_dict['direction'], sig_dict['entry_zone'], sig_dict['stop_loss'], sig_dict['tp1'], sig_dict['final_score'] if 'final_score' in sig_dict else 0, tracked_from))

def get_stats():
    return {"total_trades": 0, "win_rate": 0}

def get_current_params():
    return {}

def get_state(key):
    return None
