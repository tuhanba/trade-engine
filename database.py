"""
database.py — SQLite bağlantısı ve şema başlatma.

Kullanılan tablolar (10):
  trades, paper_account, signal_candidates, trade_postmortem,
  coin_profile, coin_market_memory, coin_cooldown,
  daily_summary, weekly_summary, system_state
"""

import sqlite3
from datetime import datetime, timezone
from config import DB_PATH


def get_conn():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    conn = get_conn()
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    conn.commit()

    conn.executescript("""
    CREATE TABLE IF NOT EXISTS trades (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        symbol TEXT,
        direction TEXT,
        entry REAL,
        exit_price REAL,
        sl REAL,
        tp1 REAL,
        tp2 REAL,
        trail_stop REAL,
        runner_target REAL,
        qty REAL,
        qty_tp1 REAL DEFAULT 0,
        qty_tp2 REAL DEFAULT 0,
        qty_runner REAL DEFAULT 0,
        realized_pnl REAL DEFAULT 0,
        unrealized_pnl REAL DEFAULT 0,
        net_pnl REAL DEFAULT 0,
        r_multiple REAL,
        risk_usdt REAL,
        status TEXT DEFAULT 'OPEN',
        environment TEXT DEFAULT 'paper',
        ax_mode TEXT DEFAULT 'execute',
        setup_type TEXT,
        ai_reason TEXT,
        linked_candidate_id INTEGER,
        open_time TEXT,
        close_time TEXT,
        duration_min REAL,
        params_version INTEGER DEFAULT 1,
        result TEXT
    );

    CREATE TABLE IF NOT EXISTS paper_account (
        id INTEGER PRIMARY KEY DEFAULT 1,
        paper_balance REAL DEFAULT 250.0,
        updated_at TEXT
    );

    CREATE TABLE IF NOT EXISTS signal_candidates (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        symbol TEXT NOT NULL,
        direction TEXT NOT NULL,
        entry REAL,
        sl REAL,
        tp1 REAL,
        tp2 REAL,
        runner_target REAL,
        rr REAL,
        expected_mfe_r REAL,
        score INTEGER DEFAULT 0,
        confidence REAL DEFAULT 0.0,
        decision TEXT DEFAULT 'PENDING',
        veto_reason TEXT,
        session TEXT,
        ax_mode TEXT,
        execution_mode TEXT,
        linked_trade_id INTEGER,
        created_at TEXT DEFAULT (datetime('now'))
    );

    CREATE TABLE IF NOT EXISTS trade_postmortem (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        trade_id INTEGER UNIQUE,
        symbol TEXT,
        direction TEXT,
        mfe REAL,
        mae REAL,
        efficiency REAL,
        missed_gain REAL,
        hold_minutes REAL,
        best_possible_tp REAL,
        exit_quality REAL,
        created_at TEXT DEFAULT (datetime('now'))
    );

    CREATE TABLE IF NOT EXISTS coin_profile (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        symbol TEXT UNIQUE NOT NULL,
        trade_count INTEGER DEFAULT 0,
        win_rate REAL DEFAULT 0.0,
        avg_r REAL DEFAULT 0.0,
        profit_factor REAL DEFAULT 0.0,
        avg_mfe REAL DEFAULT 0.0,
        avg_mae REAL DEFAULT 0.0,
        best_session TEXT,
        worst_session TEXT,
        preferred_direction TEXT,
        long_count INTEGER DEFAULT 0,
        short_count INTEGER DEFAULT 0,
        best_rr_zone TEXT,
        danger_score REAL DEFAULT 2.5,
        cooldown_status INTEGER DEFAULT 0,
        volatility_profile TEXT DEFAULT 'normal',
        spread_quality TEXT DEFAULT 'good',
        fakeout_rate REAL DEFAULT 0.0,
        total_pnl REAL DEFAULT 0.0,
        updated_at TEXT DEFAULT (datetime('now'))
    );

    CREATE TABLE IF NOT EXISTS coin_market_memory (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        symbol TEXT NOT NULL,
        session TEXT,
        market_regime TEXT,
        direction TEXT,
        result TEXT,
        r_multiple REAL,
        mfe REAL,
        mae REAL,
        created_at TEXT DEFAULT (datetime('now'))
    );

    CREATE TABLE IF NOT EXISTS coin_cooldown (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        symbol TEXT UNIQUE NOT NULL,
        reason TEXT,
        cooldown_until TEXT NOT NULL,
        created_at TEXT DEFAULT (datetime('now'))
    );

    CREATE TABLE IF NOT EXISTS daily_summary (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        date TEXT UNIQUE NOT NULL,
        total_trades INTEGER DEFAULT 0,
        wins INTEGER DEFAULT 0,
        losses INTEGER DEFAULT 0,
        win_rate REAL DEFAULT 0.0,
        total_pnl REAL DEFAULT 0.0,
        avg_r REAL DEFAULT 0.0,
        profit_factor REAL DEFAULT 0.0,
        created_at TEXT DEFAULT (datetime('now'))
    );

    CREATE TABLE IF NOT EXISTS weekly_summary (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        week_start TEXT UNIQUE NOT NULL,
        week_end TEXT,
        total_trades INTEGER DEFAULT 0,
        wins INTEGER DEFAULT 0,
        losses INTEGER DEFAULT 0,
        win_rate REAL DEFAULT 0.0,
        total_pnl REAL DEFAULT 0.0,
        avg_r REAL DEFAULT 0.0,
        profit_factor REAL DEFAULT 0.0,
        best_coin TEXT,
        worst_coin TEXT,
        created_at TEXT DEFAULT (datetime('now'))
    );

    CREATE TABLE IF NOT EXISTS system_state (
        id INTEGER PRIMARY KEY DEFAULT 1,
        ax_mode TEXT DEFAULT 'execute',
        execution_mode TEXT DEFAULT 'paper',
        bot_status TEXT DEFAULT 'running',
        circuit_breaker_active INTEGER DEFAULT 0,
        circuit_breaker_until TEXT,
        consecutive_losses INTEGER DEFAULT 0,
        last_heartbeat TEXT,
        updated_at TEXT DEFAULT (datetime('now'))
    );

    INSERT OR IGNORE INTO system_state (id) VALUES (1);
    INSERT OR IGNORE INTO paper_account (id, paper_balance, updated_at)
        VALUES (1, 250.0, datetime('now'));
    """)

    conn.commit()

    # Kolon migration — mevcut DB'ye eksik kolonları ekle
    _migrations = [
        ("coin_profile", "long_count",         "INTEGER DEFAULT 0"),
        ("coin_profile", "short_count",         "INTEGER DEFAULT 0"),
        ("coin_profile", "total_pnl",           "REAL DEFAULT 0.0"),
        ("coin_profile", "volatility_profile",  "TEXT DEFAULT 'normal'"),
        ("coin_profile", "spread_quality",      "TEXT DEFAULT 'good'"),
        ("trades",       "setup_type",          "TEXT"),
        ("trades",       "ai_reason",           "TEXT"),
        ("trades",       "linked_candidate_id", "INTEGER"),
    ]
    for table, col, coldef in _migrations:
        try:
            conn.execute(f"ALTER TABLE {table} ADD COLUMN {col} {coldef}")
            conn.commit()
        except Exception:
            pass  # kolon zaten varsa SQLite hata verir, yoksay

    conn.close()
