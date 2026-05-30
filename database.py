"""
database.py — AX SQLite Data Layer v5.0 (Production)
=====================================================
Tablolar: trades, signal_candidates, balance_ledger, bot_status.
Partial close, trailing SL, accumulated PnL desteği eklendi.
Migration veri kaybı olmadan yapılır. DROP/DELETE kullanılmaz.
"""

from __future__ import annotations

import json
import logging
import sqlite3
from datetime import datetime, timezone
from typing import Any, Optional, Generator, ContextManager
from contextlib import contextmanager

import config
from core.data_layer import SignalData, TradeData

logger = logging.getLogger("ax.database")


# ── Bağlantı ────────────────────────────────────────────────────────

def get_connection() -> sqlite3.Connection:
    """WAL modunda SQLite bağlantısı döner."""
    conn = sqlite3.connect(config.DB_PATH, timeout=30, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    conn.execute("PRAGMA synchronous=NORMAL")
    conn.execute("PRAGMA cache_size=-64000")  # 64MB cache (negatif = KB)
    conn.execute("PRAGMA temp_store=MEMORY")
    return conn


@contextmanager
def get_conn() -> Generator[sqlite3.Connection, None, None]:
    """get_connection alias — v5.1 compat (closes connection after exiting block)."""
    conn = sqlite3.connect(config.DB_PATH, timeout=30, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    conn.execute("PRAGMA cache_size=-64000")  # 64MB cache
    try:
        with conn:
            yield conn
    finally:
        conn.close()


def open_db(db_path: str | None = None, timeout: int = 15) -> sqlite3.Connection:
    """
    Herhangi bir yol için WAL modunda bağlantı açar.
    db_path verilmezse config.DB_PATH kullanılır.
    AIDecisionEngine ve GhostMemoryManager gibi farklı db_path kullanan modüller için.
    """
    path = db_path or config.DB_PATH
    conn = sqlite3.connect(path, timeout=timeout, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    conn.execute("PRAGMA cache_size=-64000")
    return conn


# ── Tablo DDL'leri ───────────────────────────────────────────────────

_TRADES_DDL = """
CREATE TABLE IF NOT EXISTS trades (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    symbol              TEXT NOT NULL,
    direction           TEXT NOT NULL DEFAULT 'LONG',
    status              TEXT DEFAULT 'OPEN',
    entry               REAL DEFAULT 0,
    sl                  REAL DEFAULT 0,
    tp1                 REAL DEFAULT 0,
    tp2                 REAL DEFAULT 0,
    tp3                 REAL DEFAULT 0,
    original_qty        REAL DEFAULT 0,
    remaining_qty       REAL DEFAULT 0,
    qty_tp1             REAL DEFAULT 0,
    qty_tp2             REAL DEFAULT 0,
    qty_runner          REAL DEFAULT 0,
    qty                 REAL DEFAULT 0,
    leverage            INTEGER DEFAULT 10,
    notional_size       REAL DEFAULT 0,
    margin_used         REAL DEFAULT 0,
    risk_pct            REAL DEFAULT 1.0,
    risk_usd            REAL DEFAULT 0,
    max_loss_after_fee  REAL DEFAULT 0,
    current_price       REAL DEFAULT 0,
    close_price         REAL DEFAULT 0,
    mark_price          REAL DEFAULT 0,
    last_update         TEXT,
    source              TEXT DEFAULT 'bot',
    realized_pnl        REAL DEFAULT 0,
    unrealized_pnl      REAL DEFAULT 0,
    net_pnl             REAL DEFAULT 0,
    total_fee           REAL DEFAULT 0,
    open_fee            REAL DEFAULT 0,
    close_fee           REAL DEFAULT 0,
    fee_rate            REAL DEFAULT 0.0004,
    tp1_hit             INTEGER DEFAULT 0,
    tp2_hit             INTEGER DEFAULT 0,
    open_time           TEXT,
    close_time          TEXT,
    open_time_str       TEXT,
    duration_seconds    INTEGER DEFAULT 0,
    hold_minutes        REAL DEFAULT 0,
    close_reason        TEXT DEFAULT '',
    stop_reason         TEXT,
    target_reason       TEXT,
    r_multiple          REAL DEFAULT 0,
    current_R           REAL DEFAULT 0,
    mfe                 REAL DEFAULT 0,
    mae                 REAL DEFAULT 0,
    setup_quality       TEXT,
    final_score         REAL DEFAULT 0,
    market_regime       TEXT,
    is_valid_for_stats  INTEGER DEFAULT 1,
    ax_mode             TEXT,
    environment         TEXT,
    session             TEXT,
    metadata            TEXT DEFAULT '{}',
    trail_stop          REAL DEFAULT 0
)
"""

_SIGNAL_CANDIDATES_DDL = """
CREATE TABLE IF NOT EXISTS signal_candidates (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    symbol          TEXT,
    side            TEXT,
    entry_price     REAL DEFAULT 0,
    stop_loss       REAL DEFAULT 0,
    tp1             REAL DEFAULT 0,
    tp2             REAL DEFAULT 0,
    tp3             REAL DEFAULT 0,
    score           REAL DEFAULT 0,
    leverage        INTEGER DEFAULT 1,
    risk_pct        REAL DEFAULT 0,
    decision        TEXT DEFAULT '',
    reason          TEXT DEFAULT '',
    source          TEXT DEFAULT '',
    created_at      TEXT,
    status          TEXT DEFAULT 'NEW',
    ghost_pnl       REAL DEFAULT 0,
    metadata        TEXT DEFAULT '{}',
    uuid            TEXT,
    direction       TEXT DEFAULT '',
    entry           REAL DEFAULT 0,
    sl              REAL DEFAULT 0,
    setup_quality   TEXT DEFAULT '',
    final_score     REAL DEFAULT 0,
    market_regime   TEXT DEFAULT '',
    risk_status     TEXT DEFAULT '',
    margin_loss_pct REAL DEFAULT 0,
    spread          REAL DEFAULT 0,
    volume          REAL DEFAULT 0,
    volatility      REAL DEFAULT 0,
    veto_reason     TEXT DEFAULT '',
    linked_trade_id INTEGER,
    trend_score     REAL DEFAULT 0,
    trigger_score   REAL DEFAULT 0,
    risk_score      REAL DEFAULT 0,
    ai_score        REAL DEFAULT 0,
    rr              REAL DEFAULT 0,
    position_size   REAL DEFAULT 0,
    notional        REAL DEFAULT 0,
    leverage_suggestion INTEGER DEFAULT 10,
    risk_amount     REAL DEFAULT 0,
    max_loss        REAL DEFAULT 0,
    atr             REAL DEFAULT 0,
    stop_distance_percent REAL DEFAULT 0,
    net_rr          REAL DEFAULT 0,
    estimated_fee   REAL DEFAULT 0,
    estimated_slippage REAL DEFAULT 0
)
"""

_BALANCE_LEDGER_DDL = """
CREATE TABLE IF NOT EXISTS balance_ledger (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    balance         REAL NOT NULL,
    realized_pnl    REAL DEFAULT 0,
    unrealized_pnl  REAL DEFAULT 0,
    note            TEXT DEFAULT '',
    created_at      TEXT NOT NULL
)
"""

_BOT_STATUS_DDL = """
CREATE TABLE IF NOT EXISTS bot_status (
    key         TEXT PRIMARY KEY,
    value       TEXT DEFAULT '',
    updated_at  TEXT NOT NULL
)
"""

_PARTIAL_CLOSES_DDL = """
CREATE TABLE IF NOT EXISTS partial_closes (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    trade_id        INTEGER NOT NULL,
    close_qty       REAL NOT NULL,
    close_pct       REAL NOT NULL,
    close_price     REAL NOT NULL,
    partial_pnl     REAL DEFAULT 0,
    reason          TEXT DEFAULT '',
    closed_at       TEXT NOT NULL,
    FOREIGN KEY (trade_id) REFERENCES trades(id)
)
"""

_PAPER_RESULTS_DDL = """
CREATE TABLE IF NOT EXISTS paper_results (
    id                      INTEGER PRIMARY KEY AUTOINCREMENT,
    signal_id               TEXT,
    candidate_id            TEXT,
    symbol                  TEXT NOT NULL,
    direction               TEXT NOT NULL,
    preview_entry           REAL DEFAULT 0,
    preview_sl              REAL DEFAULT 0,
    preview_tp1             REAL DEFAULT 0,
    preview_tp2             REAL DEFAULT 0,
    preview_tp3             REAL DEFAULT 0,
    tracked_from            TEXT DEFAULT 'candidate',
    horizon_minutes         REAL DEFAULT 240,
    reject_reason_snap      TEXT DEFAULT '',
    final_score_snap        REAL DEFAULT 0,
    leverage_hint           INTEGER DEFAULT 10,
    hit_tp                  INTEGER DEFAULT 0,
    hit_stop_first          INTEGER DEFAULT 0,
    time_to_move_minutes    REAL DEFAULT 0,
    max_favorable_excursion REAL DEFAULT 0,
    max_adverse_excursion   REAL DEFAULT 0,
    setup_worked            INTEGER DEFAULT 0,
    would_have_won          INTEGER DEFAULT 0,
    first_touch             TEXT DEFAULT '',
    skip_decision_correct   INTEGER DEFAULT 0,
    status                  TEXT DEFAULT 'pending',
    finalized_at            TEXT,
    created_at              TEXT DEFAULT (datetime('now'))
)
"""

_SIGNAL_EVENTS_DDL = """
CREATE TABLE IF NOT EXISTS signal_events (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    signal_id     TEXT,
    stage         TEXT,
    symbol        TEXT,
    reject_reason TEXT,
    data          TEXT,
    created_at    TEXT DEFAULT (datetime('now'))
)
"""

_PAPER_ACCOUNT_DDL = """
CREATE TABLE IF NOT EXISTS paper_account (
    id              INTEGER PRIMARY KEY,
    balance         REAL NOT NULL DEFAULT 2000.0,
    initial_balance REAL NOT NULL DEFAULT 2000.0,
    updated_at      TEXT DEFAULT (datetime('now'))
)
"""

_COIN_CONFIGS_DDL = """
CREATE TABLE IF NOT EXISTS coin_configs (
    coin            TEXT PRIMARY KEY,
    config_json     TEXT DEFAULT '{}',
    updated_at      TEXT,
    version         INTEGER DEFAULT 1
)
"""

# ── Ghost Learning 2.0 tabloları ─────────────────────────────────────

_GHOST_SIGNALS_DDL = """
CREATE TABLE IF NOT EXISTS ghost_signals (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    candidate_id    INTEGER,
    symbol          TEXT DEFAULT '',
    timeframe       TEXT DEFAULT '5m',
    direction       TEXT DEFAULT '',
    entry_price     REAL DEFAULT 0,
    stop_loss       REAL DEFAULT 0,
    tp1             REAL DEFAULT 0,
    tp2             REAL DEFAULT 0,
    tp3             REAL DEFAULT 0,
    atr             REAL DEFAULT 0,
    final_score     REAL DEFAULT 0,
    reject_reason   TEXT DEFAULT '',
    trigger_type    TEXT DEFAULT 'UNKNOWN',
    market_regime   TEXT DEFAULT 'NEUTRAL',
    coin            TEXT DEFAULT '',
    side            TEXT DEFAULT '',
    take_profit     REAL DEFAULT 0,
    confidence      REAL DEFAULT 0,
    simulated       INTEGER DEFAULT 0,
    created_at      TEXT DEFAULT (datetime('now'))
)
"""

_GHOST_RESULTS_DDL = """
CREATE TABLE IF NOT EXISTS ghost_results (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    ghost_id        INTEGER NOT NULL,
    virtual_outcome TEXT DEFAULT 'OPEN',
    virtual_pnl_r   REAL DEFAULT 0,
    virtual_mfe     REAL DEFAULT 0,
    virtual_mae     REAL DEFAULT 0,
    bars_held       INTEGER DEFAULT 0,
    exit_price      REAL DEFAULT 0,
    pattern_type    TEXT DEFAULT '',
    simulated_at    TEXT DEFAULT (datetime('now')),
    FOREIGN KEY (ghost_id) REFERENCES ghost_signals(id)
)
"""

_GHOST_THRESHOLD_SUGGESTIONS_DDL = """
CREATE TABLE IF NOT EXISTS ghost_threshold_suggestions (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    coin            TEXT NOT NULL,
    trigger_type    TEXT NOT NULL,
    action          TEXT NOT NULL,
    current_val     REAL NOT NULL,
    suggested_val   REAL NOT NULL,
    expected_trades REAL DEFAULT 0,
    confidence      TEXT DEFAULT 'MEDIUM',
    applied         INTEGER DEFAULT 0,
    created_at      TEXT DEFAULT (datetime('now'))
)
"""

_GHOST_SUGGESTIONS_DDL = """
CREATE TABLE IF NOT EXISTS ghost_suggestions (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    symbol              TEXT NOT NULL,
    trigger_type        TEXT NOT NULL,
    current_threshold   REAL DEFAULT 0,
    suggested_threshold REAL DEFAULT 0,
    virtual_wr          REAL DEFAULT 0,
    avg_virtual_r       REAL DEFAULT 0,
    sample_count        INTEGER DEFAULT 0,
    confidence          TEXT DEFAULT 'LOW',
    applied             INTEGER DEFAULT 0,
    created_at          TEXT DEFAULT (datetime('now'))
)
"""

# ── Migration kolonları ──────────────────────────────────────────────

_EXPECTED_COLUMNS: dict[str, list[tuple[str, str]]] = {
    "trades": [
        ("direction",    "TEXT DEFAULT 'LONG'"),
        ("entry",        "REAL DEFAULT 0"),
        ("sl",           "REAL DEFAULT 0"),
        ("qty",          "REAL DEFAULT 0"),
        ("original_qty", "REAL DEFAULT 0"),
        ("remaining_qty","REAL DEFAULT 0"),
        ("notional_size","REAL DEFAULT 0"),
        ("open_time",    "TEXT"),
        ("close_time",   "TEXT"),
        ("close_price",  "REAL DEFAULT 0"),
        ("net_pnl",      "REAL DEFAULT 0"),
        ("total_fee",    "REAL DEFAULT 0"),
        ("open_fee",     "REAL DEFAULT 0"),
        ("close_fee",    "REAL DEFAULT 0"),
        ("fee_rate",     "REAL DEFAULT 0.0004"),
        ("tp2",          "REAL DEFAULT 0"),
        ("tp3",          "REAL DEFAULT 0"),
        ("tp1_hit",      "INTEGER DEFAULT 0"),
        ("tp2_hit",      "INTEGER DEFAULT 0"),
        ("leverage",     "INTEGER DEFAULT 10"),
        ("margin_used",  "REAL DEFAULT 0"),
        ("risk_usd",     "REAL DEFAULT 0"),
        ("risk_pct",     "REAL DEFAULT 1.0"),
        ("current_price","REAL DEFAULT 0"),
        ("unrealized_pnl","REAL DEFAULT 0"),
        ("realized_pnl", "REAL DEFAULT 0"),
        ("close_reason", "TEXT DEFAULT ''"),
        ("ax_mode",      "TEXT"),
        ("metadata",     "TEXT DEFAULT '{}'"),
        ("trail_stop",        "REAL DEFAULT 0"),
        ("breakeven_set",     "INTEGER DEFAULT 0"),  # BUG FIX: eksik kolon
        ("trailing_active",   "INTEGER DEFAULT 0"),  # BUG FIX: eksik kolon
    ],
    "signal_candidates": [
        ("tp2", "REAL DEFAULT 0"),
        ("tp3", "REAL DEFAULT 0"),
        ("leverage", "INTEGER DEFAULT 1"),
        ("risk_pct", "REAL DEFAULT 0"),
        ("decision", "TEXT DEFAULT ''"),
        ("reason", "TEXT DEFAULT ''"),
        ("source", "TEXT DEFAULT ''"),
        ("status", "TEXT DEFAULT 'NEW'"),
        ("ghost_pnl", "REAL DEFAULT 0"),
        ("metadata", "TEXT DEFAULT '{}'"),
        # v6.0 — yeni sütunlar (migration için)
        ("uuid",            "TEXT"),
        ("direction",       "TEXT DEFAULT ''"),
        ("entry",           "REAL DEFAULT 0"),
        ("sl",              "REAL DEFAULT 0"),
        ("setup_quality",   "TEXT DEFAULT ''"),
        ("final_score",     "REAL DEFAULT 0"),
        ("market_regime",   "TEXT DEFAULT ''"),
        ("risk_status",     "TEXT DEFAULT ''"),
        ("margin_loss_pct", "REAL DEFAULT 0"),
        ("spread",          "REAL DEFAULT 0"),
        ("volume",          "REAL DEFAULT 0"),
        ("volatility",      "REAL DEFAULT 0"),
        ("veto_reason",     "TEXT DEFAULT ''"),
        ("linked_trade_id", "INTEGER"),
        ("trend_score",     "REAL DEFAULT 0"),
        ("trigger_score",   "REAL DEFAULT 0"),
        ("risk_score",      "REAL DEFAULT 0"),
        ("ai_score",        "REAL DEFAULT 0"),
        ("rr",              "REAL DEFAULT 0"),
        ("position_size",   "REAL DEFAULT 0"),
        ("notional",        "REAL DEFAULT 0"),
        ("leverage_suggestion", "INTEGER DEFAULT 10"),
        ("risk_amount",     "REAL DEFAULT 0"),
        ("max_loss",        "REAL DEFAULT 0"),
        ("atr",             "REAL DEFAULT 0"),
        ("stop_distance_percent", "REAL DEFAULT 0"),
        ("net_rr",          "REAL DEFAULT 0"),
        ("estimated_fee",   "REAL DEFAULT 0"),
        ("estimated_slippage", "REAL DEFAULT 0"),
    ],
    "ghost_signals": [
        ("candidate_id",  "INTEGER"),
        ("symbol",        "TEXT DEFAULT ''"),
        ("direction",     "TEXT DEFAULT ''"),
        ("tp1",           "REAL DEFAULT 0"),
        ("tp2",           "REAL DEFAULT 0"),
        ("tp3",           "REAL DEFAULT 0"),
        ("atr",           "REAL DEFAULT 0"),
        ("final_score",   "REAL DEFAULT 0"),
        ("market_regime", "TEXT DEFAULT 'NEUTRAL'"),
    ],
    "ghost_results": [
        ("exit_price", "REAL DEFAULT 0"),
    ],
    "coin_profiles": [
        ("last_updated", "TEXT DEFAULT (datetime('now'))"),  # P0 BUG FIX #2
    ],
    "telegram_messages": [
        ("sig_id", "TEXT"),  # BUG FIX: save_telegram_message sig_id kullanıyor
    ],
    "ai_logs": [
        ("created_at",      "TEXT"),
        ("trades_analyzed", "INTEGER DEFAULT 0"),
        ("win_rate",        "REAL DEFAULT 0"),
        ("avg_rr",          "REAL DEFAULT 0"),
        ("insight",         "TEXT DEFAULT ''"),
        ("changes",         "TEXT DEFAULT '[]'"),
        ("event",           "TEXT"),
        ("symbol",          "TEXT"),
        ("decision",        "TEXT"),
        ("score",           "REAL DEFAULT 0"),
        ("confidence",      "REAL DEFAULT 0"),
        ("reason",          "TEXT DEFAULT ''"),
        ("data",            "TEXT DEFAULT ''"),
    ],
}


def _verify_schema() -> None:
    """Kritik sütunların varlığını doğrular, eksikse uyarır."""
    required = {
        'trades': ['direction', 'open_time', 'close_time', 'entry', 'sl',
                   'qty', 'net_pnl', 'close_price', 'tp1', 'tp2', 'tp3'],
    }
    try:
        with get_conn() as conn:
            for table, cols in required.items():
                existing = {r[1] for r in conn.execute(f"PRAGMA table_info({table})")}
                missing = set(cols) - existing
                if missing:
                    logger.error("SCHEMA EKSIK — %s tablosunda: %s", table, missing)
                else:
                    logger.info("Schema OK: %s", table)
    except Exception as exc:
        logger.warning("Schema doğrulaması başarısız: %s", exc)


def init_db() -> None:
    """Tabloları oluşturur (var olanları silmez)."""
    conn = get_connection()
    try:
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA synchronous=NORMAL")
        conn.execute("PRAGMA cache_size=-64000")
        conn.commit()
        conn.execute(_TRADES_DDL)
        conn.execute(_SIGNAL_CANDIDATES_DDL)
        conn.execute(_BALANCE_LEDGER_DDL)
        conn.execute(_BOT_STATUS_DDL)
        conn.execute(_PARTIAL_CLOSES_DDL)
        conn.execute(_PAPER_RESULTS_DDL)
        conn.execute(_SIGNAL_EVENTS_DDL)
        conn.execute(_PAPER_ACCOUNT_DDL)
        conn.execute(_COIN_CONFIGS_DDL)
        conn.execute(_GHOST_SIGNALS_DDL)
        conn.execute(_GHOST_RESULTS_DDL)
        conn.execute(_GHOST_THRESHOLD_SUGGESTIONS_DDL)
        conn.execute(_GHOST_SUGGESTIONS_DDL)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS ai_learning (
                id            INTEGER PRIMARY KEY AUTOINCREMENT,
                symbol        TEXT,
                trade_result  TEXT,
                pnl           REAL DEFAULT 0,
                setup_quality TEXT,
                created_at    TEXT DEFAULT (datetime('now'))
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS adaptive_stats (
                id          TEXT PRIMARY KEY,
                scope       TEXT,
                key         TEXT,
                sample_size INTEGER DEFAULT 0,
                win_rate    REAL DEFAULT 0,
                expectancy  REAL DEFAULT 0,
                avg_r       REAL DEFAULT 0,
                threshold_data      INTEGER DEFAULT 0,
                threshold_watchlist INTEGER DEFAULT 0,
                threshold_telegram  INTEGER DEFAULT 0,
                threshold_trade     INTEGER DEFAULT 0,
                action_taken TEXT DEFAULT '',
                notes        TEXT DEFAULT '',
                created_at   TEXT DEFAULT (datetime('now'))
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS coin_profiles (
                symbol       TEXT PRIMARY KEY,
                win_rate     REAL DEFAULT 0.5,
                avg_r        REAL DEFAULT 0,
                profit_factor REAL DEFAULT 0,
                tp1_hit_rate REAL DEFAULT 0,
                tp2_hit_rate REAL DEFAULT 0,
                runner_contribution REAL DEFAULT 0,
                avg_duration REAL DEFAULT 0,
                fakeout_rate REAL DEFAULT 0,
                fee_drag     REAL DEFAULT 0,
                best_hour    INTEGER,
                best_session TEXT,
                long_bias    REAL DEFAULT 0.5,
                short_bias   REAL DEFAULT 0.5,
                regime_performance TEXT,
                danger_score REAL DEFAULT 0,
                sample_size  INTEGER DEFAULT 0,
                total_trades INTEGER DEFAULT 0,
                updated_at   TEXT DEFAULT (datetime('now')),
                last_updated TEXT DEFAULT (datetime('now'))
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS params (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                version         INTEGER DEFAULT 1,
                sl_atr_mult     REAL DEFAULT 1.2,
                tp_atr_mult     REAL DEFAULT 2.0,
                rsi5_min        REAL DEFAULT 35,
                rsi5_max        REAL DEFAULT 75,
                rsi1_min        REAL DEFAULT 35,
                rsi1_max        REAL DEFAULT 72,
                vol_ratio_min   REAL DEFAULT 1.2,
                min_volume_m    REAL DEFAULT 10.0,
                min_change_pct  REAL DEFAULT 2.0,
                risk_pct        REAL DEFAULT 1.5,
                updated_at      TEXT DEFAULT (datetime('now')),
                ai_reason       TEXT DEFAULT ''
            )
        """)
        conn.execute("INSERT OR IGNORE INTO params (id) VALUES (1)")
        conn.execute("""
            CREATE TABLE IF NOT EXISTS ai_logs (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                created_at      TEXT,
                trades_analyzed INTEGER DEFAULT 0,
                win_rate        REAL DEFAULT 0,
                avg_rr          REAL DEFAULT 0,
                insight         TEXT DEFAULT '',
                changes         TEXT DEFAULT '[]',
                event           TEXT,
                symbol          TEXT,
                decision        TEXT,
                score           REAL DEFAULT 0,
                confidence      REAL DEFAULT 0,
                reason          TEXT DEFAULT '',
                data            TEXT DEFAULT ''
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS trade_events (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                trade_id    INTEGER,
                event_type  TEXT,
                data        TEXT DEFAULT '',
                created_at  TEXT DEFAULT (datetime('now'))
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS system_state (
                key        TEXT PRIMARY KEY,
                value      TEXT DEFAULT '',
                updated_at TEXT DEFAULT (datetime('now'))
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS weekly_summary (
                week_start  TEXT PRIMARY KEY,
                trade_count INTEGER DEFAULT 0,
                win_count   INTEGER DEFAULT 0,
                loss_count  INTEGER DEFAULT 0,
                win_rate    REAL DEFAULT 0,
                net_pnl     REAL DEFAULT 0,
                avg_r       REAL DEFAULT 0,
                best_day    TEXT,
                worst_day   TEXT
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS coin_library (
                symbol       TEXT PRIMARY KEY,
                min_qty      REAL DEFAULT 0,
                step_size    REAL DEFAULT 0,
                tick_size    REAL DEFAULT 0,
                min_notional REAL DEFAULT 5.0,
                status       TEXT DEFAULT 'TRADING',
                last_updated TEXT
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS coin_cooldown (
                symbol        TEXT PRIMARY KEY,
                until         TEXT,
                reason        TEXT DEFAULT '',
                consec_losses INTEGER DEFAULT 0
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS telegram_messages (
                id         INTEGER PRIMARY KEY AUTOINCREMENT,
                sig_id     TEXT,
                symbol     TEXT,
                dedupe_key TEXT UNIQUE,
                text       TEXT,
                status     TEXT DEFAULT 'queued',
                created_at TEXT DEFAULT (datetime('now')),
                sent_at    TEXT
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS scanned_coins (
                id         INTEGER PRIMARY KEY AUTOINCREMENT,
                symbol     TEXT,
                score      REAL DEFAULT 0,
                status     TEXT DEFAULT 'scanned',
                reason     TEXT DEFAULT '',
                volume     REAL DEFAULT 0,
                price      REAL DEFAULT 0,
                price_change REAL DEFAULT 0,
                scanned_at TEXT DEFAULT (datetime('now'))
            )
        """)
        conn.execute("DROP TABLE IF EXISTS best_params")
        conn.execute("""
            CREATE TABLE IF NOT EXISTS best_params (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                data            TEXT,
                params_json     TEXT,
                win_rate        REAL,
                profit_factor   REAL,
                created_at      TEXT DEFAULT (datetime('now'))
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS pattern_memory (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                pattern_hash TEXT,
                win_rate REAL,
                occurrences INTEGER,
                last_seen TEXT
            )
        """)
        from config import INITIAL_PAPER_BALANCE
        conn.execute(
            "INSERT OR IGNORE INTO paper_account (id, balance) VALUES (1, ?)",
            (INITIAL_PAPER_BALANCE,)
        )
        conn.commit()
        # İndeksler (performans)
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_trades_status ON trades(status)"
        )
        
        # Migrations
        try:
            conn.execute("ALTER TABLE ghost_signals ADD COLUMN timeframe TEXT DEFAULT '5m'")
        except Exception:
            pass # Kolon zaten varsa hata verir, yoksay
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_trades_symbol ON trades(symbol)"
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_signals_symbol ON signal_candidates(symbol)"
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_signals_created ON signal_candidates(created_at)"
        )
        init_ghost_tables()   # Ghost Learning 2.0
        conn.commit()
        logger.info("DB tabloları hazır: %s", config.DB_PATH)
    finally:
        conn.close()
    _verify_schema()
    migrate_db()


def migrate_db() -> list[str]:
    """
    Eksik kolonları tespit edip ALTER TABLE ile ekler.
    Var olan veriyi silmez, DROP/DELETE kullanmaz.
    """
    added: list[str] = []
    conn = get_connection()
    try:
        # partial_closes tablosu yoksa oluştur
        conn.execute(_PARTIAL_CLOSES_DDL)

        for table, columns in _EXPECTED_COLUMNS.items():
            existing = _get_existing_columns(conn, table)
            for col_name, col_def in columns:
                if col_name not in existing:
                    sql = f"ALTER TABLE {table} ADD COLUMN {col_name} {col_def}"
                    try:
                        conn.execute(sql)
                        added.append(f"{table}.{col_name}")
                        logger.info("Kolon eklendi: %s.%s", table, col_name)
                    except sqlite3.OperationalError as exc:
                        logger.warning("Kolon eklenemedi %s.%s: %s", table, col_name, exc)
        conn.commit()
    finally:
        conn.close()
    if added:
        logger.info("Migration tamamlandı: %s", added)
    return added


def _get_existing_columns(conn: sqlite3.Connection, table: str) -> set[str]:
    """Tablodaki mevcut kolon isimlerini set olarak döner."""
    cursor = conn.execute(f"PRAGMA table_info({table})")
    return {row["name"] for row in cursor.fetchall()}


def ensure_column(table: str, column: str, column_type: str) -> bool:
    """Belirli bir tabloda kolonun var olduğunu garanti eder."""
    conn = get_connection()
    try:
        existing = _get_existing_columns(conn, table)
        if column in existing:
            return False
        sql = f"ALTER TABLE {table} ADD COLUMN {column} {column_type}"
        try:
            conn.execute(sql)
            conn.commit()
            logger.info("Kolon eklendi: %s.%s", table, column)
            return True
        except sqlite3.OperationalError as exc:
            logger.warning("Kolon eklenemedi %s.%s: %s", table, column, exc)
            return False
    finally:
        conn.close()


# ── Signal candidates ──────────────────────────────────────────────

def save_signal_candidate(
    signal: SignalData,
    decision: str,
    reason: str = "",
    status: str = "NEW",
) -> Optional[int]:
    """Sinyal adayını DB'ye kaydeder."""
    conn = get_connection()
    try:
        cur = conn.execute(
            """
            INSERT INTO signal_candidates
                (symbol, side, entry_price, stop_loss, tp1, tp2, tp3,
                 score, leverage, risk_pct, decision, reason, source,
                 created_at, status, metadata)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            """,
            (
                signal.symbol, signal.side, signal.entry_price,
                signal.stop_loss,
                signal.tp1 or 0, signal.tp2 or 0, signal.tp3 or 0,
                signal.score, signal.leverage, signal.risk_pct,
                decision, reason, signal.source,
                signal.created_at, status,
                json.dumps(signal.metadata) if signal.metadata else "{}",
            ),
        )
        conn.commit()
        return cur.lastrowid
    except Exception as exc:
        logger.error("Signal candidate kaydedilemedi: %s", exc)
        return None
    finally:
        conn.close()


def update_signal_ghost_pnl(signal_id: int, pnl: float, status: str) -> None:
    """Ghost tracking PnL güncelleme."""
    conn = get_connection()
    try:
        conn.execute(
            "UPDATE signal_candidates SET ghost_pnl = ?, status = ? WHERE id = ?",
            (pnl, status, signal_id),
        )
        conn.commit()
    except Exception as exc:
        logger.error("Ghost PnL güncellenemedi [%s]: %s", signal_id, exc)
    finally:
        conn.close()


# ── Trade CRUD ─────────────────────────────────────────────────────

def create_trade(trade: TradeData, metadata: str = "{}") -> Optional[int]:
    """Yeni trade kaydı oluşturur, id döner."""
    conn = get_connection()
    now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
    try:
        cur = conn.execute(
            """
            INSERT INTO trades
                (symbol, direction, entry, sl, tp1, tp2, tp3,
                 qty, qty_tp1, qty_tp2, qty_runner, leverage, notional_size, margin_used, risk_usd,
                 risk_pct, status, open_time, current_price,
                 unrealized_pnl, realized_pnl, net_pnl,
                 remaining_qty, original_qty, close_price, close_reason,
                 total_fee, fee_rate, ax_mode, metadata)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            """,
            (
                trade.symbol,
                getattr(trade, 'direction', None) or getattr(trade, 'side', 'LONG'),
                getattr(trade, 'entry', None) or getattr(trade, 'entry_price', 0),
                getattr(trade, 'sl', None) or getattr(trade, 'stop_loss', 0),
                trade.tp1 or 0,
                trade.tp2 or 0,
                trade.tp3 or 0,
                getattr(trade, 'qty', None) or getattr(trade, 'quantity', 0),
                getattr(trade, 'qty_tp1', 0),
                getattr(trade, 'qty_tp2', 0),
                getattr(trade, 'qty_runner', 0),
                trade.leverage or 10,
                getattr(trade, 'notional_size', None) or getattr(trade, 'notional', 0),
                trade.margin_used or 0,
                trade.risk_usd or 0,
                trade.risk_pct or 1.0,
                trade.status or 'OPEN',
                getattr(trade, 'open_time', None) or getattr(trade, 'opened_at', None) or now,
                getattr(trade, 'current_price', 0) or 0,
                trade.unrealized_pnl or 0,
                trade.realized_pnl or 0,
                trade.realized_pnl or 0,
                getattr(trade, 'qty', None) or getattr(trade, 'quantity', 0),
                getattr(trade, 'qty', None) or getattr(trade, 'quantity', 0),
                getattr(trade, 'close_price', None) or getattr(trade, 'exit_price', 0) or 0,
                trade.close_reason or '',
                getattr(trade, 'total_fee', 0) or 0,
                getattr(trade, 'fee_rate', 0.0004) or 0.0004,
                getattr(trade, 'ax_mode', None),
                metadata or "{}",
            ),
        )
        conn.commit()
        trade_id = cur.lastrowid
        try:
            from core import redis_state
            redis_state.invalidate_open_trades()
        except Exception:
            pass
        return trade_id
    except Exception as exc:
        logger.error("Trade oluşturulamadı: %s", exc)
        return None
    finally:
        conn.close()


def update_trade_price(
    trade_id: int,
    current_price: float,
    unrealized_pnl: float,
) -> None:
    """Açık trade'in güncel fiyatını ve unrealized PnL'ini günceller.
    BUG FIX: LOWER(status) kullan — tp1_hit ve runner da güncellenir."""
    conn = get_connection()
    try:
        conn.execute(
            """
            UPDATE trades
            SET current_price = ?, unrealized_pnl = ?
            WHERE id = ? AND LOWER(status) IN ('open','tp1_hit','runner')
            """,
            (current_price, unrealized_pnl, trade_id),
        )
        conn.commit()
    except Exception as exc:
        logger.error("Trade fiyat güncellenemedi [%s]: %s", trade_id, exc)
    finally:
        conn.close()


def update_trade_sl(trade_id: int, new_sl: float) -> None:
    """Trailing SL güncelleme."""
    conn = get_connection()
    try:
        conn.execute(
            "UPDATE trades SET sl = ? WHERE id = ? AND LOWER(status) IN ('open','tp1_hit','runner')",
            (new_sl, trade_id),
        )
        conn.commit()
    except Exception as exc:
        logger.error("Trade SL güncellenemedi [%s]: %s", trade_id, exc)
    finally:
        conn.close()


def update_trade_metadata(trade_id: int, metadata_json: str) -> None:
    """Trade metadata'sını günceller (exit state için)."""
    conn = get_connection()
    try:
        conn.execute(
            "UPDATE trades SET metadata = ? WHERE id = ?",
            (metadata_json, trade_id),
        )
        conn.commit()
    except Exception as exc:
        logger.error("Trade metadata güncellenemedi [%s]: %s", trade_id, exc)
    finally:
        conn.close()


def record_partial_close(
    trade_id: int,
    close_qty: float,
    close_pct: float,
    close_price: float,
    partial_pnl: float,
    reason: str = "",
    new_sl: Optional[float] = None,
) -> None:
    """Partial close'u kaydeder ve trade'in accumulated_pnl + remaining_qty_pct'ini günceller."""
    now = datetime.now(timezone.utc).isoformat()
    conn = get_connection()
    try:
        # partial_closes tablosuna kayıt
        conn.execute(
            """
            INSERT INTO partial_closes
                (trade_id, close_qty, close_pct, close_price, partial_pnl, reason, closed_at)
            VALUES (?,?,?,?,?,?,?)
            """,
            (trade_id, close_qty, close_pct, close_price, partial_pnl, reason, now),
        )

        # trades tablosu güncelle
        # BUG FIX: LOWER(status) — tp1_hit modundaki trade'ler de güncellenir
        update_sql = """
            UPDATE trades
            SET realized_pnl = COALESCE(realized_pnl, 0) + ?,
                remaining_qty = CASE
                    WHEN COALESCE(remaining_qty, qty, 0) - ? < 0 THEN 0
                    ELSE COALESCE(remaining_qty, qty, 0) - ?
                END
        """
        params = [partial_pnl, close_qty, close_qty]

        if new_sl is not None and new_sl > 0:
            update_sql += ", sl = ?"
            params.append(new_sl)

        update_sql += " WHERE id = ? AND LOWER(status) IN ('open','tp1_hit','runner')"
        params.append(trade_id)

        conn.execute(update_sql, params)
        conn.commit()

        logger.info(
            "Partial close kaydedildi: #%s  qty=%.4f  pct=%.1f%%  pnl=%.4f  reason=%s",
            trade_id, close_qty, close_pct, partial_pnl, reason,
        )
        try:
            from core import redis_state
            redis_state.invalidate_open_trades()
        except Exception:
            pass
    except Exception as exc:
        logger.error("Partial close kaydedilemedi [%s]: %s", trade_id, exc)
    finally:
        conn.close()


def close_trade(
    trade_id: int,
    exit_price: float,
    realized_pnl: float,
    close_reason: str = "",
) -> None:
    """Trade'i kapatır ve realized PnL yazar."""
    now = datetime.now(timezone.utc).isoformat()
    conn = get_connection()
    try:
        conn.execute(
            """
            UPDATE trades
            SET status = 'closed',
                close_price = ?,
                realized_pnl = ?,
                net_pnl = ?,
                unrealized_pnl = 0,
                close_reason = ?,
                close_time = ?
            WHERE id = ?
            """,
            (exit_price, realized_pnl, realized_pnl, close_reason, now, trade_id),
        )
        conn.commit()
        # NOT: Bakiye güncellemesi çağıran tarafından (execution_engine._finalize veya
        # ExecutionEngine.close_trade) yapılır — double-counting'i önlemek için burada güncellenmez.
        logger.info("[DB] Trade #%s kapandı: pnl=%+.3f$", trade_id, realized_pnl)
        try:
            from core import redis_state
            redis_state.invalidate_open_trades()
        except Exception:
            pass
    except Exception as exc:
        logger.error("Trade kapatılamadı [%s]: %s", trade_id, exc)
    finally:
        conn.close()


# ── Trade sorgular ─────────────────────────────────────────────────

def get_open_trades() -> list[dict]:
    """Açık trade'leri döner. Redis cache (5s TTL) → SQLite fallback."""
    try:
        from core import redis_state
        cached = redis_state.get("open_trades_cache")
        if cached is not None:
            return cached
    except Exception:
        pass

    conn = get_connection()
    try:
        rows = conn.execute(
            "SELECT * FROM trades WHERE LOWER(status) IN ('open','tp1_hit','runner') ORDER BY open_time DESC"
        ).fetchall()
        result = [dict(r) for r in rows]
        try:
            from core import redis_state
            redis_state.set("open_trades_cache", result, ttl=5)
        except Exception:
            pass
        return result
    except Exception as exc:
        logger.error("Open trades alınamadı: %s", exc)
        return []
    finally:
        conn.close()


def get_recent_trades(limit: int = 100) -> list[dict]:
    """Son trade'leri döner — sadece kapanmış olanları ve dashboard uyumlu alias'larla."""
    conn = get_connection()
    try:
        rows = conn.execute(
            "SELECT * FROM trades WHERE LOWER(status) = 'closed' ORDER BY id DESC LIMIT ?", (limit,)
        ).fetchall()
        result = []
        for r in rows:
            t = dict(r)
            # Dashboard uyumluluğu için alan aliasları
            t["exit_price"]  = t.get("close_price") or t.get("exit_price") or t.get("current_price", 0)
            t["opened_at"]   = t.get("open_time") or t.get("opened_at", "")
            t["closed_at"]   = t.get("close_time") or t.get("closed_at", "")
            t["entry_price"] = t.get("entry") or t.get("entry_price", 0)
            t["stop_loss"]   = t.get("sl") or t.get("stop_loss", 0)
            t["side"]        = t.get("direction") or t.get("side", "?")
            result.append(t)
        return result
    except Exception as exc:
        logger.error("Recent trades alınamadı: %s", exc)
        return []
    finally:
        conn.close()


def get_recent_signals(limit: int = 100) -> list[dict]:
    """Son sinyal adaylarını döner."""
    conn = get_connection()
    try:
        rows = conn.execute(
            "SELECT * FROM signal_candidates ORDER BY id DESC LIMIT ?", (limit,)
        ).fetchall()
        return [dict(r) for r in rows]
    except Exception as exc:
        logger.error("Recent signals alınamadı: %s", exc)
        return []
    finally:
        conn.close()


def get_trade_by_id(trade_id: int) -> Optional[dict]:
    """ID ile trade getirir."""
    conn = get_connection()
    try:
        row = conn.execute("SELECT * FROM trades WHERE id = ?", (trade_id,)).fetchone()
        return dict(row) if row else None
    except Exception as exc:
        logger.error("Trade getirilemedi [%s]: %s", trade_id, exc)
        return None
    finally:
        conn.close()


def get_partial_closes(trade_id: int) -> list[dict]:
    """Bir trade'in tüm partial close'larını döner."""
    conn = get_connection()
    try:
        rows = conn.execute(
            "SELECT * FROM partial_closes WHERE trade_id = ? ORDER BY id",
            (trade_id,),
        ).fetchall()
        return [dict(r) for r in rows]
    except Exception as exc:
        logger.error("Partial closes alınamadı [%s]: %s", trade_id, exc)
        return []
    finally:
        conn.close()


# ── Dashboard stats ────────────────────────────────────────────────

import time as _time
_stats_cache = {"data": {}, "time": 0}

def get_dashboard_stats() -> dict:
    """Dashboard için özet istatistikler."""
    global _stats_cache
    now = _time.time()
    if now - _stats_cache["time"] < 0.2 and _stats_cache["data"]:
        return _stats_cache["data"]

    conn = get_connection()
    try:
        total = conn.execute("SELECT COUNT(*) FROM trades").fetchone()[0]
        open_count = conn.execute(
            "SELECT COUNT(*) FROM trades WHERE LOWER(status) IN ('open', 'tp1_hit', 'runner')"
        ).fetchone()[0]
        closed_count = conn.execute(
            "SELECT COUNT(*) FROM trades WHERE LOWER(status)='closed'"
        ).fetchone()[0]

        # BUG FIX: net_pnl kullan (realized_pnl TP1/TP2 partial toplamlarını da içerir)
        rpnl_row = conn.execute(
            "SELECT COALESCE(SUM(net_pnl), 0) FROM trades WHERE LOWER(status)='closed'"
        ).fetchone()
        realized_pnl = float(rpnl_row[0]) if rpnl_row else 0.0

        upnl_row = conn.execute(
            "SELECT COALESCE(SUM(unrealized_pnl), 0) FROM trades WHERE LOWER(status) IN ('open', 'tp1_hit', 'runner')"
        ).fetchone()
        unrealized_pnl = float(upnl_row[0]) if upnl_row else 0.0

        # Accumulated partial PnL (açık trade'lerdeki)
        accum_row = conn.execute(
            "SELECT COALESCE(SUM(realized_pnl), 0) FROM trades WHERE LOWER(status) IN ('open', 'tp1_hit', 'runner')"
        ).fetchone()
        accumulated_pnl = float(accum_row[0]) if accum_row else 0.0

        win_count = conn.execute(
            "SELECT COUNT(*) FROM trades WHERE LOWER(status)='closed' AND net_pnl > 0"
        ).fetchone()[0]
        loss_count = closed_count - win_count
        winrate = round(
            (win_count / closed_count * 100), 1
        ) if closed_count > 0 else 0.0

        # Bugünkü PnL
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        today_row = conn.execute(
            "SELECT COALESCE(SUM(net_pnl), 0) FROM trades "
            "WHERE LOWER(status)='closed' AND DATE(close_time) = ?",
            (today,),
        ).fetchone()
        today_pnl = float(today_row[0]) if today_row else 0.0

        # Bakiye: paper_account tek kaynak of truth
        try:
            _bal_row = conn.execute("SELECT balance FROM paper_account WHERE id=1").fetchone()
            balance = float(_bal_row[0]) if _bal_row else getattr(config, 'INITIAL_PAPER_BALANCE', 2000.0)
        except Exception:
            balance = getattr(config, 'INITIAL_PAPER_BALANCE', 2000.0)

        # Ghost tracking özeti
        ghost_tp = conn.execute(
            "SELECT COUNT(*) FROM signal_candidates WHERE status='TP_HIT'"
        ).fetchone()[0]
        ghost_sl = conn.execute(
            "SELECT COUNT(*) FROM signal_candidates WHERE status='SL_HIT'"
        ).fetchone()[0]

        result = {
            "total_trades": total,
            "open_trades": open_count,
            "closed_trades": closed_count,
            "realized_pnl": round(realized_pnl, 4),
            "unrealized_pnl": round(unrealized_pnl, 4),
            "accumulated_pnl": round(accumulated_pnl, 4),
            "total_pnl": round(realized_pnl + unrealized_pnl + accumulated_pnl, 4),
            "today_pnl": round(today_pnl, 4),
            "winrate": winrate,
            "win_rate": winrate,          # BUG FIX: alias — frontend win_rate bekliyor
            "win_trades": win_count,      # BUG FIX: frontend için eksikti
            "loss_trades": loss_count,    # BUG FIX: frontend için eksikti
            "balance": round(balance, 4),
            "initial_balance": getattr(config, 'INITIAL_PAPER_BALANCE', 2000.0),
            "ghost_tp_hits": ghost_tp,
            "ghost_sl_hits": ghost_sl,
            "ghost_winrate": round(
                ghost_tp / (ghost_tp + ghost_sl) * 100, 1
            ) if (ghost_tp + ghost_sl) > 0 else 0.0,
        }
        _stats_cache["data"] = result
        _stats_cache["time"] = now
        return result
    except Exception as exc:
        logger.error("Dashboard stats alınamadı: %s", exc)
        return {
            "total_trades": 0, "open_trades": 0, "closed_trades": 0,
            "realized_pnl": 0, "unrealized_pnl": 0, "accumulated_pnl": 0,
            "total_pnl": 0, "today_pnl": 0, "winrate": 0,
            "balance": getattr(config, "INITIAL_PAPER_BALANCE", 2000.0),
            "ghost_tp_hits": 0, "ghost_sl_hits": 0, "ghost_winrate": 0,
        }
    finally:
        conn.close()


# ── Bot status ─────────────────────────────────────────────────────

def update_bot_status(key: str, value: str) -> None:
    """Bot durum anahtarını günceller. Redis primary, SQLite sync (heartbeat hariç)."""
    try:
        from core import redis_state
        redis_state.set(f"bot_status:{key}", value, ttl=600)
    except Exception:
        pass

    # heartbeat her 10s gelir — SQLite'a her dakika yaz (lock baskısını azalt)
    if key == "heartbeat":
        try:
            from core import redis_state as _rs
            _last = _rs.get("bot_status_heartbeat_last_db_write", default=0)
            import time as _time
            now_ts = _time.time()
            if now_ts - float(_last) < 60:
                return
            _rs.set("bot_status_heartbeat_last_db_write", now_ts)
        except Exception:
            pass

    now = datetime.now(timezone.utc).isoformat()
    conn = get_connection()
    try:
        conn.execute(
            """
            INSERT INTO bot_status (key, value, updated_at)
            VALUES (?, ?, ?)
            ON CONFLICT(key) DO UPDATE SET value=excluded.value, updated_at=excluded.updated_at
            """,
            (key, value, now),
        )
        conn.commit()
    except Exception as exc:
        logger.error("Bot status güncellenemedi [%s]: %s", key, exc)
    finally:
        conn.close()


def get_bot_status(key: Optional[str] = None) -> dict:
    """Bot durum bilgisini döner. Redis first → SQLite fallback."""
    if key is not None:
        try:
            from core import redis_state
            cached = redis_state.get(f"bot_status:{key}")
            if cached is not None:
                from datetime import datetime, timezone as tz
                return {"value": str(cached), "updated_at": datetime.now(tz.utc).isoformat()}
        except Exception:
            pass
        conn = get_connection()
        try:
            row = conn.execute(
                "SELECT value, updated_at FROM bot_status WHERE key = ?",
                (key,),
            ).fetchone()
            if row:
                return {"value": row["value"], "updated_at": row["updated_at"]}
            return {}
        except Exception as exc:
            logger.error("Bot status alınamadı: %s", exc)
            return {}
        finally:
            conn.close()

    conn = get_connection()
    try:
        rows = conn.execute(
            "SELECT key, value, updated_at FROM bot_status"
        ).fetchall()
        return {
            r["key"]: {"value": r["value"], "updated_at": r["updated_at"]}
            for r in rows
        }
    except Exception as exc:
        logger.error("Bot status alınamadı: %s", exc)
        return {}
    finally:
        conn.close()


# ── Balance ledger ─────────────────────────────────────────────────

def set_balance(balance: float, note: str = "") -> Optional[int]:
    """Bakiye kaydı ekler."""
    now = datetime.now(timezone.utc).isoformat()
    conn = get_connection()
    try:
        cur = conn.execute(
            """
            INSERT INTO balance_ledger (balance, note, created_at)
            VALUES (?, ?, ?)
            """,
            (balance, note, now),
        )
        conn.commit()
        return cur.lastrowid
    except Exception as exc:
        logger.error("Balance kaydedilemedi: %s", exc)
        return None
    finally:
        conn.close()


def get_latest_balance(default: float = 500.0) -> float:
    """Son bakiye kaydını döner. Kayıt yoksa default döner."""
    conn = get_connection()
    try:
        row = conn.execute(
            "SELECT balance FROM balance_ledger ORDER BY id DESC LIMIT 1"
        ).fetchone()
        if row:
            return float(row["balance"])
        return default
    except Exception as exc:
        logger.error("Balance okunamadı: %s", exc)
        return default
    finally:
        conn.close()


# ── v5.1 Ek fonksiyonlar ─────────────────────────────────────────────

def _migrate():
    migrations = [
        ("trades", "qty",                "REAL"),
        ("trades", "hold_minutes",       "REAL"),
        ("trades", "open_time_str",      "TEXT"),
        ("trades", "ax_mode",            "TEXT"),
        ("trades", "environment",        "TEXT"),
        ("trades", "session",            "TEXT"),
        ("trades", "close_price",        "REAL DEFAULT 0"),
        ("trades", "mark_price",         "REAL DEFAULT 0"),
        ("trades", "last_update",        "TEXT"),
        ("trades", "source",             "TEXT DEFAULT 'bot'"),
        ("trades", "current_price",      "REAL DEFAULT 0"),
        ("trades", "unrealized_pnl",     "REAL DEFAULT 0"),
        ("trades", "open_fee",           "REAL DEFAULT 0"),
        ("trades", "close_fee",          "REAL DEFAULT 0"),
        ("trades", "fee_rate",           "REAL DEFAULT 0.0004"),
        ("trades", "notional_size",      "REAL DEFAULT 0"),
        ("trades", "margin_used",        "REAL DEFAULT 0"),
        ("trades", "risk_pct",           "REAL DEFAULT 1.0"),
        ("trades", "risk_usd",           "REAL DEFAULT 0"),
        ("trades", "max_loss_after_fee", "REAL DEFAULT 0"),
        ("trades", "duration_seconds",   "INTEGER DEFAULT 0"),
        ("trades", "market_regime",      "TEXT"),
        ("trades", "is_valid_for_stats", "INTEGER DEFAULT 1"),
        ("trades", "archived_reason",    "TEXT"),
        ("trades", "tp3",                "REAL"),
        ("trades", "remaining_qty",      "REAL"),
        ("trades", "original_qty",       "REAL"),
        ("trades", "qty_tp1",            "REAL DEFAULT 0"),
        ("trades", "qty_tp2",            "REAL DEFAULT 0"),
        ("trades", "qty_runner",         "REAL DEFAULT 0"),
        ("trades", "mfe",                "REAL DEFAULT 0"),
        ("trades", "mae",                "REAL DEFAULT 0"),
        ("trades", "entry_zone",         "REAL DEFAULT 0"),
        ("trades", "invalidation_level", "REAL DEFAULT 0"),
        ("trades", "stop_reason",        "TEXT"),
        ("trades", "target_reason",      "TEXT"),
        ("trades", "trigger_score",      "REAL DEFAULT 0"),
        ("trades", "current_R",          "REAL DEFAULT 0"),
        ("trades", "distance_to_sl",     "REAL DEFAULT 0"),
        ("trades", "distance_to_tp1",    "REAL DEFAULT 0"),
        ("trades", "distance_to_tp2",    "REAL DEFAULT 0"),
        ("trades", "distance_to_tp3",    "REAL DEFAULT 0"),
        ("daily_summary", "sent",        "INTEGER DEFAULT 0"),
        ("daily_summary", "best_coin",   "TEXT"),
        ("daily_summary", "worst_coin",  "TEXT"),
        ("coin_cooldown", "consec_losses", "INTEGER DEFAULT 0"),
        ("coin_profiles", "short_bias",  "REAL DEFAULT 0.5"),
        ("coin_profiles", "tp1_hit_rate","REAL DEFAULT 0"),
        ("coin_profiles", "tp2_hit_rate","REAL DEFAULT 0"),
        ("coin_profiles", "runner_contribution", "REAL DEFAULT 0"),
        ("coin_profiles", "avg_duration","REAL DEFAULT 0"),
        ("coin_profiles", "fakeout_rate","REAL DEFAULT 0"),
        ("coin_profiles", "fee_drag",    "REAL DEFAULT 0"),
        ("coin_profiles", "best_hour",   "INTEGER"),
        ("coin_profiles", "best_session","TEXT"),
        ("coin_profiles", "long_bias",   "REAL DEFAULT 0.5"),
        ("coin_profiles", "regime_performance", "TEXT"),
        ("coin_profiles", "danger_score","REAL DEFAULT 0"),
        ("coin_profiles", "sample_size", "INTEGER DEFAULT 0"),
        ("coin_profiles", "total_trades", "INTEGER DEFAULT 0"),
        ("coin_profiles", "updated_at",   "TEXT DEFAULT (datetime('now'))"),
        ("trades", "total_fee",    "REAL DEFAULT 0"),
        ("trades", "setup_quality","TEXT"),
        ("trades", "final_score",  "REAL"),
        ("trades", "leverage",     "INTEGER DEFAULT 10"),
        ("trades", "realized_pnl", "REAL DEFAULT 0"),
        ("trades", "tp1_hit",      "INTEGER DEFAULT 0"),
        ("trades", "tp2_hit",      "INTEGER DEFAULT 0"),
        ("trades", "r_multiple",   "REAL DEFAULT 0"),
        ("trades", "close_reason", "TEXT"),
        ("trades", "close_time",   "TEXT"),
        ("trades", "open_time",    "TEXT"),
        ("signal_candidates", "risk_status",     "TEXT"),
        ("signal_candidates", "margin_loss_pct", "REAL DEFAULT 0"),
        ("signal_candidates", "spread",          "REAL DEFAULT 0"),
        ("signal_candidates", "volume",          "REAL DEFAULT 0"),
        ("signal_candidates", "volatility",      "REAL DEFAULT 0"),
        # BUG FIX: Eksik kolonlar — migration ile mevcut DB'ye eklenir
        ("trades", "net_pnl",         "REAL DEFAULT 0"),
        ("trades", "trail_stop",      "REAL DEFAULT 0"),
        ("trades", "breakeven_set",   "INTEGER DEFAULT 0"),
        ("trades", "trailing_active", "INTEGER DEFAULT 0"),
        ("telegram_messages", "sig_id", "TEXT"),
        ("telegram_messages", "text", "TEXT"),
    ]
    with get_conn() as conn:
        for table, col, col_type in migrations:
            try:
                existing = {
                    row[1] for row in conn.execute(f"PRAGMA table_info({table})")
                }
                if col not in existing:
                    conn.execute(f"ALTER TABLE {table} ADD COLUMN {col} {col_type}")
            except Exception:
                pass


_TRADE_COLUMNS = None


def _get_trade_columns(conn):
    global _TRADE_COLUMNS
    if _TRADE_COLUMNS is None:
        cursor = conn.execute("PRAGMA table_info(trades)")
        _TRADE_COLUMNS = {row[1] for row in cursor.fetchall()}
    return _TRADE_COLUMNS


def save_trade(trade: dict) -> int:
    with get_conn() as conn:
        cols = _get_trade_columns(conn)
        filtered = {k: v for k, v in trade.items() if k in cols and k != "id"}
        if not filtered:
            raise ValueError("save_trade: No valid columns to insert")
        col_names = ", ".join(filtered.keys())
        placeholders = ", ".join(["?"] * len(filtered))
        cursor = conn.execute(
            f"INSERT INTO trades ({col_names}) VALUES ({placeholders})",
            list(filtered.values())
        )
        trade_id = cursor.lastrowid
        logger.info(f"[DB] Trade #{trade_id} kaydedildi: {trade.get('symbol')}")
        return trade_id


def update_trade(trade_id: int, updates: dict):
    with get_conn() as conn:
        cols = _get_trade_columns(conn)
        filtered = {k: v for k, v in updates.items() if k in cols and k != "id"}
        if not filtered:
            return
        set_clause = ", ".join([f"{k} = ?" for k in filtered.keys()])
        conn.execute(
            f"UPDATE trades SET {set_clause} WHERE id = ?",
            list(filtered.values()) + [trade_id]
        )


def get_closed_trades(limit: int = 200, valid_only: bool = True) -> list:
    with get_conn() as conn:
        if valid_only:
            rows = conn.execute(
                """SELECT * FROM trades
                   WHERE status = 'closed' AND is_valid_for_stats = 1
                   ORDER BY id DESC LIMIT ?""",
                (limit,)
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT * FROM trades WHERE status = 'closed' ORDER BY id DESC LIMIT ?",
                (limit,)
            ).fetchall()
        return [dict(r) for r in rows]


def get_stats() -> dict:
    with get_conn() as conn:
        row = conn.execute("""
            SELECT
                COUNT(*) as total_trades,
                SUM(CASE WHEN net_pnl > 0 THEN 1 ELSE 0 END) as wins,
                SUM(CASE WHEN net_pnl <= 0 THEN 1 ELSE 0 END) as losses,
                SUM(net_pnl) as total_pnl,
                SUM(total_fee) as total_fees,
                AVG(r_multiple) as avg_r,
                SUM(CASE WHEN net_pnl > 0 THEN net_pnl ELSE 0 END) as gross_profit,
                SUM(CASE WHEN net_pnl < 0 THEN ABS(net_pnl) ELSE 0 END) as gross_loss
            FROM trades
            WHERE status = 'closed' AND is_valid_for_stats = 1
        """).fetchone()

        total = row["total_trades"] or 0
        wins = row["wins"] or 0
        gross_profit = row["gross_profit"] or 0
        gross_loss = row["gross_loss"] or 0

        return {
            "total_trades": total,
            "wins": wins,
            "losses": row["losses"] or 0,
            "win_rate": round(wins / total, 4) if total > 0 else 0,
            "total_pnl": round(row["total_pnl"] or 0, 4),
            "total_fees": round(row["total_fees"] or 0, 4),
            "avg_r": round(row["avg_r"] or 0, 3),
            "profit_factor": round(gross_profit / gross_loss, 2) if gross_loss > 0 else 0,
        }


def get_paper_balance() -> float:
    try:
        from config import INITIAL_PAPER_BALANCE as _default
    except Exception:
        _default = 2000.0
    try:
        with get_conn() as conn:
            row = conn.execute("SELECT balance FROM paper_account WHERE id=1").fetchone()
            if row:
                return float(row[0])
            conn.execute(
                "INSERT OR IGNORE INTO paper_account (id, balance) VALUES (1, ?)",
                (_default,)
            )
            conn.commit()
            return _default
    except Exception:
        return _default


def update_paper_balance(amount: float) -> float:
    with get_conn() as conn:
        row = conn.execute("SELECT balance FROM paper_account WHERE id=1").fetchone()
        current = float(row[0]) if row else 500.0
        new_balance = current + amount
        conn.execute("UPDATE paper_account SET balance = ? WHERE id=1", (new_balance,))
        return new_balance


def add_ledger_entry(trade_id, symbol, event_type, amount, note=""):
    with get_conn() as conn:
        row = conn.execute("SELECT balance FROM paper_account WHERE id=1").fetchone()
        balance_before = float(row[0]) if row else 500.0
        balance_after = balance_before + amount
        conn.execute("UPDATE paper_account SET balance = ? WHERE id=1", (balance_after,))
        conn.execute("""
            INSERT INTO balance_ledger
                (trade_id, symbol, event_type, amount, balance_before, balance_after, note)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        """, (trade_id, symbol, event_type, amount,
              balance_before, balance_after, note))
        return balance_after


def save_partial_close(trade_id, symbol, close_type, close_qty,
                       close_price, net_pnl, fee):
    with get_conn() as conn:
        conn.execute("""
            INSERT INTO partial_closes
                (trade_id, symbol, close_type, close_qty, close_price, net_pnl, fee)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        """, (trade_id, symbol, close_type, close_qty, close_price, net_pnl, fee))


def archive_invalid_trade(trade_id, reason="manual_archive"):
    with get_conn() as conn:
        conn.execute("""
            UPDATE trades SET is_valid_for_stats = 0, archived_reason = ?
            WHERE id = ?
        """, (reason, trade_id))


def save_scalp_signal(data: dict, decision: str = "ALLOW"):
    """
    decision parametresi dışarıdan alınır.
    Hardcoded 'ALLOW' kaldırıldı — ghost learning veri bütünlüğü için kritik.
    """
    save_signal_candidate_dict({
        "uuid":            data.get("id"),
        "symbol":          data.get("symbol"),
        "direction":       data.get("direction"),
        "entry":           data.get("entry_zone", data.get("entry")),
        "sl":              data.get("stop_loss", data.get("sl")),
        "tp1":             data.get("tp1"),
        "tp2":             data.get("tp2"),
        "tp3":             data.get("tp3"),
        "setup_quality":   data.get("setup_quality"),
        "final_score":     data.get("final_score"),
        "decision":        decision,
        "reason":          data.get("reason", ""),
        "market_regime":   data.get("market_regime"),
        "risk_status":     data.get("risk_status"),
        "margin_loss_pct": data.get("margin_loss_pct", 0),
        "spread":          data.get("spread", 0),
        "volume":          data.get("volume", 0),
        "volatility":      data.get("volatility", 0),
    })


def save_signal_candidate_dict(data: dict):
    """Dict tabanlı sinyal adayını signal_candidates tablosuna kaydeder."""
    with get_conn() as conn:
        conn.execute("""
            INSERT INTO signal_candidates
                (uuid, symbol, direction, entry, sl, tp1, tp2, tp3,
                 setup_quality, final_score, decision, reason, market_regime,
                 risk_status, margin_loss_pct, spread, volume, volatility)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            data.get("uuid"), data.get("symbol"), data.get("direction"),
            data.get("entry"), data.get("sl"), data.get("tp1"),
            data.get("tp2"), data.get("tp3"),
            data.get("setup_quality"), data.get("final_score"),
            data.get("decision"), data.get("reason"), data.get("market_regime"),
            data.get("risk_status"),
            data.get("margin_loss_pct", 0),
            data.get("spread", 0),
            data.get("volume", 0),
            data.get("volatility", 0),
        ))


def get_active_scalp_signals(limit: int = 100) -> list:
    """Son 24 saatte kaydedilen sinyal adaylarını döner."""
    try:
        with get_conn() as conn:
            rows = conn.execute("""
                SELECT * FROM signal_candidates
                WHERE created_at >= datetime('now', '-24 hours')
                ORDER BY id DESC LIMIT ?
            """, (limit,)).fetchall()
            return [dict(r) for r in rows]
    except Exception as e:
        logger.warning(f"get_active_scalp_signals hatası: {e}")
        return []


def save_paper_trade(data: dict, tracked_from: str = "candidate"):
    with get_conn() as conn:
        conn.execute("""
            INSERT INTO paper_results
                (signal_id, candidate_id,
                 symbol, direction, preview_entry, preview_sl, preview_tp1,
                 preview_tp2, preview_tp3, tracked_from, would_have_won, status)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'pending')
        """, (
            data.get("signal_id"),
            data.get("candidate_id"),
            data.get("symbol"),
            data.get("direction"),
            data.get("preview_entry", data.get("entry_zone", data.get("entry"))),
            data.get("preview_sl", data.get("stop_loss", data.get("sl"))),
            data.get("preview_tp1", data.get("tp1")),
            data.get("tp2", data.get("preview_tp2")),
            data.get("tp3", data.get("preview_tp3")),
            data.get("tracked_from", tracked_from),
            data.get("would_have_won", 0),
        ))


def save_paper_result(data: dict):
    save_paper_trade(data, tracked_from=data.get("tracked_from", "candidate"))


def get_pending_paper_results(limit=35) -> list:
    with get_conn() as conn:
        rows = conn.execute("""
            SELECT * FROM paper_results
            WHERE status = 'pending'
            ORDER BY created_at ASC
            LIMIT ?
        """, (limit,)).fetchall()
        return [dict(r) for r in rows]


def update_paper_result(result_id: int, updates: dict):
    with get_conn() as conn:
        valid_cols = {
            "hit_tp", "hit_stop_first", "time_to_move_minutes",
            "max_favorable_excursion", "max_adverse_excursion",
            "setup_worked", "would_have_won", "first_touch",
            "skip_decision_correct", "status", "finalized_at",
        }
        filtered = {k: v for k, v in updates.items() if k in valid_cols}
        if not filtered:
            return
        set_clause = ", ".join([f"{k} = ?" for k in filtered.keys()])
        conn.execute(
            f"UPDATE paper_results SET {set_clause} WHERE id = ?",
            list(filtered.values()) + [result_id]
        )


# ── Ghost Learning 2.0 CRUD ──────────────────────────────────────────

def save_ghost_signal(data: dict) -> int:
    """ghost_signals tablosuna yeni kayıt ekler, id döner."""
    with get_conn() as conn:
        cur = conn.execute(
            """INSERT INTO ghost_signals
               (coin, symbol, side, direction, timeframe, entry_price, stop_loss, take_profit, tp1, tp2, tp3, atr, final_score, market_regime,
                confidence, reject_reason, trigger_type, simulated)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0)""",
            (
                data.get("coin") or data.get("symbol", ""),
                data.get("symbol", ""),
                data.get("side") or data.get("direction", ""),
                data.get("direction") or data.get("side", ""),
                data.get("timeframe", "5m"),
                float(data.get("entry_price") or data.get("entry", 0)),
                float(data.get("stop_loss") or data.get("sl", 0)),
                float(data.get("take_profit") or data.get("tp1", 0)),
                float(data.get("tp1", 0)),
                float(data.get("tp2", 0)),
                float(data.get("tp3", 0)),
                float(data.get("atr", 0)),
                float(data.get("final_score", 0)),
                data.get("market_regime", "NEUTRAL"),
                float(data.get("confidence", 0)),
                data.get("reject_reason", ""),
                data.get("trigger_type", "unknown"),
            )
        )
        return cur.lastrowid or 0


def get_unsimulated_ghosts(limit: int = 100) -> list:
    """Henüz simüle edilmemiş ghost_signals kayıtlarını döner."""
    with get_conn() as conn:
        rows = conn.execute(
            """SELECT * FROM ghost_signals
               WHERE simulated = 0
               ORDER BY created_at ASC
               LIMIT ?""",
            (limit,)
        ).fetchall()
        return [dict(r) for r in rows]


def save_ghost_result(ghost_id: int, data: dict) -> None:
    """ghost_results tablosuna simülasyon sonucu kaydeder."""
    with get_conn() as conn:
        conn.execute(
            """INSERT INTO ghost_results
               (ghost_id, virtual_outcome, virtual_pnl_r, virtual_mfe,
                virtual_mae, bars_held, pattern_type)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (
                ghost_id,
                data.get("virtual_outcome", "OPEN"),
                float(data.get("virtual_pnl_r", 0)),
                float(data.get("virtual_mfe", 0)),
                float(data.get("virtual_mae", 0)),
                int(data.get("bars_held", 0)),
                data.get("pattern_type", ""),
            )
        )
        conn.execute(
            "UPDATE ghost_signals SET simulated = 1 WHERE id = ?",
            (ghost_id,)
        )


def get_ghost_pattern_stats(min_count: int = 5, days: int = 30) -> list:
    """
    Pattern analizi: trigger_type × coin bazında ghost WR ve avg R döner.
    min_count: Minimum ghost sayısı (anlamlı analiz için).
    """
    with get_conn() as conn:
        rows = conn.execute(
            """SELECT
                   g.trigger_type,
                   g.coin,
                   COUNT(*) as ghost_count,
                   SUM(CASE WHEN r.virtual_outcome='WIN' THEN 1.0 ELSE 0 END)
                       * 100.0 / COUNT(*) AS virtual_wr,
                   AVG(r.virtual_pnl_r) AS avg_virtual_r
               FROM ghost_signals g
               JOIN ghost_results r ON g.id = r.ghost_id
               WHERE g.created_at > datetime('now', ?)
               GROUP BY g.trigger_type, g.coin
               HAVING ghost_count >= ?
               ORDER BY avg_virtual_r DESC""",
            (f"-{days} days", min_count)
        ).fetchall()
        return [dict(r) for r in rows]


def save_ghost_suggestion(data: dict) -> None:
    """ghost_threshold_suggestions tablosuna öneri kaydeder."""
    with get_conn() as conn:
        conn.execute(
            """INSERT INTO ghost_threshold_suggestions
               (coin, trigger_type, action, current_val, suggested_val,
                expected_trades, confidence)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (
                data.get("coin", ""),
                data.get("trigger_type", ""),
                data.get("action", "LOWER_THRESHOLD"),
                float(data.get("current_val", 0)),
                float(data.get("suggested_val", 0)),
                float(data.get("expected_trades", 0)),
                data.get("confidence", "MEDIUM"),
            )
        )


def update_coin_profile(symbol: str, updates: dict):
    with get_conn() as conn:
        existing = conn.execute(
            "SELECT symbol FROM coin_profiles WHERE symbol = ?", (symbol,)
        ).fetchone()
        now = datetime.now(timezone.utc).isoformat()
        if existing:
            valid_cols = {
                "win_rate", "avg_r", "profit_factor", "tp1_hit_rate",
                "tp2_hit_rate", "runner_contribution", "avg_duration",
                "fakeout_rate", "fee_drag", "best_hour", "best_session",
                "long_bias", "short_bias", "regime_performance",
                "danger_score", "sample_size",
            }
            filtered = {k: v for k, v in updates.items() if k in valid_cols}
            # P0 BUG FIX #2: Her iki zaman kolonünu de güncelle
            filtered["updated_at"]   = now
            filtered["last_updated"] = now
            set_clause = ", ".join([f"{k} = ?" for k in filtered.keys()])
            conn.execute(
                f"UPDATE coin_profiles SET {set_clause} WHERE symbol = ?",
                list(filtered.values()) + [symbol]
            )
        else:
            conn.execute(
                "INSERT INTO coin_profiles (symbol, updated_at, last_updated) VALUES (?, ?, ?)",
                (symbol, now, now)
            )
            if updates:
                update_coin_profile(symbol, updates)


def upsert_pattern_memory(
    pattern_hash: str,
    outcome: int,        # 1=WIN, 0=LOSS
    r_multiple: float = 0.0,
    features: dict | None = None,
) -> None:
    """
    P0 BUG FIX #3: pattern_memory tablosunu günceller.
    ML modeli bu tabloyu okuyarak eğitilir.
    - Hash zaten varsa: occurrences artırılır, win_rate güncellenir.
    - Yoksa: yeni kayıt oluşturulur.
    features: {"adx", "rsi5", "rsi1", "ml_score", "rv", "side", "quality", "symbol"}
    """
    try:
        now = datetime.now(timezone.utc).isoformat()
        feat_json = json.dumps(features or {})
        with get_conn() as conn:
            existing = conn.execute(
                "SELECT id, win_rate, occurrences FROM pattern_memory WHERE pattern_hash = ?",
                (pattern_hash,)
            ).fetchone()
            if existing:
                old_occ = int(existing["occurrences"] or 1)
                old_wr  = float(existing["win_rate"] or 0.0)
                new_occ = old_occ + 1
                # Running average win_rate
                new_wr  = round((old_wr * old_occ + outcome) / new_occ, 4)
                conn.execute(
                    """UPDATE pattern_memory
                       SET win_rate=?, occurrences=?, last_seen=?
                       WHERE pattern_hash=?""",
                    (new_wr, new_occ, now, pattern_hash)
                )
            else:
                conn.execute(
                    """INSERT INTO pattern_memory
                       (pattern_hash, win_rate, occurrences, last_seen)
                       VALUES (?, ?, ?, ?)""",
                    (pattern_hash, float(outcome), 1, now)
                )
    except Exception as exc:
        logger.warning("upsert_pattern_memory hatası [%s]: %s", pattern_hash, exc)


def update_trade_stats(trade_id, mfe=None, mae=None):
    with get_conn() as conn:
        if mfe is not None:
            conn.execute(
                "UPDATE trades SET mfe = MAX(mfe, ?) WHERE id = ?", (mfe, trade_id)
            )
        if mae is not None:
            conn.execute(
                "UPDATE trades SET mae = MIN(mae, ?) WHERE id = ?", (mae, trade_id)
            )


def save_ai_log(event, symbol, decision, score, confidence, reason, data):
    with get_conn() as conn:
        conn.execute("""
            INSERT INTO ai_logs (event, symbol, decision, score, confidence, reason, data)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        """, (event, symbol, decision, score, confidence, reason, data))


def save_postmortem(trade_id, data: dict):
    save_trade_event(trade_id, "POSTMORTEM", json.dumps(data))


def save_trade_event(trade_id, event_type, data=""):
    with get_conn() as conn:
        conn.execute("""
            INSERT INTO trade_events (trade_id, event_type, data)
            VALUES (?, ?, ?)
        """, (trade_id, event_type, data))


def get_trade_events(trade_id: int) -> list:
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT * FROM trade_events WHERE trade_id = ? ORDER BY id",
            (trade_id,)
        ).fetchall()
        return [dict(r) for r in rows]


def get_state(key: str) -> str:
    try:
        with get_conn() as conn:
            row = conn.execute(
                "SELECT value FROM system_state WHERE key = ?", (key,)
            ).fetchone()
            return row[0] if row else None
    except Exception:
        return None


def set_state(key: str, value: str):
    try:
        with get_conn() as conn:
            conn.execute("""
                INSERT INTO system_state (key, value, updated_at)
                VALUES (?, ?, datetime('now'))
                ON CONFLICT(key) DO UPDATE SET value = ?, updated_at = datetime('now')
            """, (key, value, value))
    except Exception as e:
        logger.warning(f"[DB] set_state hatası: {e}")


def update_system_state(key: str, value: str):
    with get_conn() as conn:
        conn.execute("""
            INSERT INTO system_state (key, value, updated_at)
            VALUES (?, ?, datetime('now'))
            ON CONFLICT(key) DO UPDATE SET value=?, updated_at=datetime('now')
        """, (key, value, value))


def get_system_state(key: str, default="-") -> str:
    try:
        with get_conn() as conn:
            row = conn.execute(
                "SELECT value FROM system_state WHERE key=?", (key,)
            ).fetchone()
            if row:
                return str(row[0])
    except Exception:
        pass
    return default


def get_market_regime() -> str:
    """Piyasa rejimini döner. Redis first → SQLite fallback. Default: NEUTRAL"""
    try:
        from core import redis_state
        cached = redis_state.get("market_regime")
        if cached:
            return str(cached)
    except Exception:
        pass
    return get_system_state("market_regime", default="NEUTRAL")


def set_market_regime(regime: str) -> None:
    """Piyasa rejimini Redis + SQLite'a yazar."""
    try:
        from core import redis_state
        redis_state.set("market_regime", regime)
    except Exception:
        pass
    try:
        update_system_state("market_regime", regime)
    except Exception as exc:
        logger.warning("[DB] set_market_regime SQLite: %s", exc)


def save_daily_summary(data: dict):
    with get_conn() as conn:
        conn.execute("""
            INSERT INTO daily_summary
                (date, trade_count, win_count, loss_count, win_rate,
                 gross_pnl, net_pnl, avg_r, max_drawdown, balance_eod)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(date) DO UPDATE SET
                trade_count=?, win_count=?, loss_count=?, win_rate=?,
                gross_pnl=?, net_pnl=?, avg_r=?, max_drawdown=?, balance_eod=?
        """, (
            data["date"], data["trade_count"], data["win_count"],
            data["loss_count"], data["win_rate"], data["gross_pnl"],
            data["net_pnl"], data["avg_r"], data["max_drawdown"], data["balance_eod"],
            data["trade_count"], data["win_count"], data["loss_count"],
            data["win_rate"], data["gross_pnl"], data["net_pnl"],
            data["avg_r"], data["max_drawdown"], data["balance_eod"],
        ))


def save_weekly_summary(data: dict):
    with get_conn() as conn:
        conn.execute("""
            INSERT INTO weekly_summary
                (week_start, trade_count, win_count, loss_count, win_rate,
                 net_pnl, avg_r, best_day, worst_day)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(week_start) DO UPDATE SET
                trade_count=?, win_count=?, loss_count=?, win_rate=?,
                net_pnl=?, avg_r=?, best_day=?, worst_day=?
        """, (
            data["week_start"], data["trade_count"], data["win_count"],
            data["loss_count"], data["win_rate"], data["net_pnl"],
            data["avg_r"], data.get("best_day"), data.get("worst_day"),
            data["trade_count"], data["win_count"], data["loss_count"],
            data["win_rate"], data["net_pnl"], data["avg_r"],
            data.get("best_day"), data.get("worst_day"),
        ))


def save_coin_library(symbol, filters: dict):
    with get_conn() as conn:
        now = datetime.now(timezone.utc).isoformat()
        conn.execute("""
            INSERT INTO coin_library
                (symbol, min_qty, step_size, tick_size, min_notional, status, last_updated)
            VALUES (?, ?, ?, ?, ?, 'TRADING', ?)
            ON CONFLICT(symbol) DO UPDATE SET
                min_qty=?, step_size=?, tick_size=?, min_notional=?,
                status='TRADING', last_updated=?
        """, (
            symbol,
            filters.get("min_qty"), filters.get("step_size"),
            filters.get("tick_size"), filters.get("min_notional", 5.0), now,
            filters.get("min_qty"), filters.get("step_size"),
            filters.get("tick_size"), filters.get("min_notional", 5.0), now,
        ))


def disable_coin(symbol, reason="delisted"):
    with get_conn() as conn:
        conn.execute(
            "UPDATE coin_library SET status = ? WHERE symbol = ?",
            (reason, symbol)
        )


def reset_paper_data(force_delete=False):
    if force_delete:
        with get_conn() as conn:
            conn.execute("DELETE FROM trades")
            conn.execute("DELETE FROM partial_closes")
            conn.execute("DELETE FROM balance_ledger")
            conn.execute("UPDATE paper_account SET balance = initial_balance WHERE id=1")
        logger.warning("[DB] FORCE DELETE: Tüm trade verileri silindi!")
    else:
        with get_conn() as conn:
            conn.execute("""
                UPDATE trades SET is_valid_for_stats = 0,
                    archived_reason = 'paper_reset'
                WHERE is_valid_for_stats = 1
            """)
            conn.execute("UPDATE paper_account SET balance = initial_balance WHERE id=1")
        logger.info("[DB] Paper reset: trades is_valid_for_stats=0 yapıldı.")


# ─────────────────────────────────────────────────────────────────────────────
# TELEGRAM MESAJ TABLOSU
# ─────────────────────────────────────────────────────────────────────────────

def save_telegram_message(sig_id, symbol: str, dedupe_key: str,
                          text: str, status: str = "queued") -> bool:
    """Telegram mesajını kuyruğa yaz. Duplicate dedupe_key'i reddeder."""
    try:
        with get_conn() as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS telegram_messages (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    sig_id TEXT,
                    symbol TEXT,
                    dedupe_key TEXT UNIQUE,
                    text TEXT,
                    status TEXT DEFAULT 'queued',
                    created_at TEXT DEFAULT (datetime('now'))
                )
            """)
            existing = conn.execute(
                "SELECT id FROM telegram_messages WHERE dedupe_key = ?", (dedupe_key,)
            ).fetchone()
            if existing:
                return False
            conn.execute(
                "INSERT INTO telegram_messages (sig_id, symbol, dedupe_key, text, status) "
                "VALUES (?, ?, ?, ?, ?)",
                (str(sig_id), symbol, dedupe_key, text[:4096], status)
            )
            return True
    except Exception as e:
        logger.warning(f"[DB] save_telegram_message hatası: {e}")
        return False


def mark_telegram_message_sent(dedupe_key: str):
    try:
        with get_conn() as conn:
            conn.execute(
                "UPDATE telegram_messages SET status = 'sent' WHERE dedupe_key = ?",
                (dedupe_key,)
            )
    except Exception as e:
        logger.warning(f"[DB] mark_telegram_message_sent hatası: {e}")


# ─────────────────────────────────────────────────────────────────────────────
# MARKET SCANNER TABLOSU
# ─────────────────────────────────────────────────────────────────────────────

def save_market_snapshot(data: dict):
    try:
        with get_conn() as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS market_snapshots (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    symbol TEXT,
                    volume REAL,
                    price REAL,
                    price_change REAL,
                    status TEXT,
                    score REAL,
                    timestamp TEXT DEFAULT (datetime('now'))
                )
            """)
            conn.execute(
                "INSERT INTO market_snapshots (symbol, volume, price, price_change, status, score) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (data.get("symbol"), data.get("volume"), data.get("price"),
                 data.get("price_change"), data.get("status"), data.get("score"))
            )
    except Exception as e:
        logger.debug(f"[DB] save_market_snapshot: {e}")


def save_scanned_coin(data: dict):
    try:
        with get_conn() as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS scanned_coins (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    symbol TEXT,
                    status TEXT,
                    reason TEXT,
                    score REAL,
                    volume REAL,
                    price REAL,
                    price_change REAL,
                    timestamp TEXT DEFAULT (datetime('now'))
                )
            """)
            conn.execute(
                "INSERT INTO scanned_coins (symbol, status, reason, score, volume, price, price_change) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                (data.get("symbol"), data.get("status"), data.get("reason"),
                 data.get("score"), data.get("volume"), data.get("price"),
                 data.get("price_change"))
            )
    except Exception as e:
        logger.debug(f"[DB] save_scanned_coin: {e}")


# ─────────────────────────────────────────────────────────────────────────────
# ALIAS VE COMPAT FONKSİYONLAR
# ─────────────────────────────────────────────────────────────────────────────

def upsert_coin_profile(symbol: str, updates: dict):
    """update_coin_profile alias — geriye dönük uyumluluk."""
    update_coin_profile(symbol, updates)


def get_coin_profile(symbol: str) -> dict:
    """Coin profil verisini döndürür. Kayıt yoksa boş dict."""
    with get_conn() as conn:
        row = conn.execute(
            "SELECT * FROM coin_profiles WHERE symbol = ?", (symbol,)
        ).fetchone()
        return dict(row) if row else {}


def is_coin_in_cooldown(symbol: str) -> bool:
    """Coin cooldown'da mı? Redis TTL-bazlı kontrol → SQLite fallback."""
    try:
        from core import redis_state
        if redis_state.exists(f"cooldown:{symbol}"):
            return True
    except Exception:
        pass
    try:
        now_str = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
        with get_conn() as conn:
            row = conn.execute(
                "SELECT until FROM coin_cooldown WHERE symbol = ? AND until > ?",
                (symbol, now_str)
            ).fetchone()
            return row is not None
    except Exception:
        return False


def set_coin_cooldown_redis(symbol: str, minutes: int) -> None:
    """Coin cooldown'unu Redis'e yazar (TTL ile otomatik sona erer)."""
    try:
        from core import redis_state
        redis_state.set(f"cooldown:{symbol}", 1, ttl=int(minutes * 60))
    except Exception:
        pass


# ─────────────────────────────────────────────────────────────────────────────
# SCALP BOT COMPAT FONKSİYONLARI
# ─────────────────────────────────────────────────────────────────────────────

def init_paper_account(reset: bool = False):
    """Paper account yoksa başlangıç bakiyesiyle oluşturur. reset=True ise bakiyeyi sıfırlar."""
    try:
        with get_conn() as conn:
            conn.execute(_PAPER_ACCOUNT_DDL)
            existing = conn.execute(
                "SELECT id FROM paper_account WHERE id=1"
            ).fetchone()
            if not existing:
                init_bal = getattr(config, 'INITIAL_PAPER_BALANCE', 2000.0)
                conn.execute(
                    "INSERT OR IGNORE INTO paper_account (id, balance, initial_balance) VALUES (1, ?, ?)",
                    (init_bal, init_bal)
                )
                logger.info(f"[DB] paper_account oluşturuldu: ${init_bal}")
            elif reset:
                init_bal = getattr(config, 'INITIAL_PAPER_BALANCE', 2000.0)
                conn.execute(
                    "UPDATE paper_account SET balance=?, initial_balance=? WHERE id=1",
                    (init_bal, init_bal)
                )
                logger.info(f"[DB] paper_account sıfırlandı: ${init_bal}")
            else:
                logger.info("[DB] paper_account hazır.")
    except Exception as e:
        logger.warning(f"init_paper_account: {e}")


def archive_old_scalp_signals(hours: int = 24):
    """Eski sinyal adaylarını arşivle (signal_candidates soft-delete)."""
    try:
        cutoff = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
        with get_conn() as conn:
            conn.execute(
                "DELETE FROM signal_candidates WHERE created_at < datetime(?, ?)",
                (cutoff, f"-{hours} hours")
            )
    except Exception as e:
        logger.warning(f"archive_old_scalp_signals: {e}")


def save_candidate_signal(data: dict) -> int:
    """Sinyal adayını signal_candidates tablosuna kaydeder, id döndürür."""
    try:
        now = datetime.now(timezone.utc).isoformat()
        with get_conn() as conn:
            cursor = conn.execute("""
                INSERT INTO signal_candidates
                  (uuid, symbol, direction, entry, sl, tp1, tp2, tp3,
                   setup_quality, final_score, trend_score, trigger_score,
                   risk_score, ai_score, rr, position_size, notional,
                   leverage_suggestion, risk_amount, max_loss, atr,
                   stop_distance_percent, net_rr, estimated_fee, estimated_slippage,
                   decision, market_regime, reason, created_at)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            """, (
                data.get("signal_id"),                              # uuid = signal UUID
                data.get("symbol"),
                data.get("direction"),
                data.get("entry"),
                data.get("sl", data.get("stop", 0)),               # FIX: "stop" key alias
                data.get("tp1"), data.get("tp2"), data.get("tp3"),
                data.get("quality", data.get("setup_quality")),
                data.get("final_score", data.get("score", 0)),
                data.get("trend_score", 0),
                data.get("trigger_score", 0),
                data.get("risk_score", 0),
                data.get("ai_score", data.get("final_score", 0)),
                data.get("rr", 0),
                data.get("position_size", 0),
                data.get("notional", 0),
                data.get("leverage_suggestion", data.get("leverage", 10)),
                data.get("risk_amount", data.get("max_loss", 0)),
                data.get("max_loss", 0),
                data.get("atr", 0),
                data.get("stop_distance_percent", 0),
                data.get("net_rr", data.get("rr", 0)),
                data.get("estimated_fee", 0),
                data.get("estimated_slippage", 0),
                data.get("decision", "PENDING"),
                data.get("market_regime"),
                data.get("reason", ""),
                now,
            ))
            return cursor.lastrowid
    except Exception as e:
        logger.warning(f"save_candidate_signal: {e}")
        return 0


def save_signal_event(signal_id, event_type: str, **kwargs):
    """Sinyal yaşam döngüsü olayını signal_events tablosuna kaydeder."""
    try:
        now = datetime.now(timezone.utc).isoformat()
        symbol = kwargs.get("symbol", "")
        reject_reason = kwargs.get("reject_reason", kwargs.get("reason", ""))
        with get_conn() as conn:
            conn.execute(
                "INSERT INTO signal_events (signal_id, stage, symbol, reject_reason, created_at) VALUES (?,?,?,?,?)",
                (str(signal_id), event_type, symbol, reject_reason, now)
            )
    except Exception as e:
        logger.debug(f"save_signal_event: {e}")


# ─────────────────────────────────────────────────────────────────────────────
# GHOST LEARNING 2.0 — DB yardımcıları
# ─────────────────────────────────────────────────────────────────────────────

def init_ghost_tables() -> None:
    """Ghost Learning 2.0 tablolarını ve indekslerini oluşturur."""
    ddls = [
        _GHOST_SIGNALS_DDL,
        _GHOST_RESULTS_DDL,
        _GHOST_SUGGESTIONS_DDL,
        "CREATE INDEX IF NOT EXISTS idx_ghost_signals_simulated ON ghost_signals(simulated)",
        "CREATE INDEX IF NOT EXISTS idx_ghost_signals_symbol ON ghost_signals(symbol)",
        "CREATE INDEX IF NOT EXISTS idx_ghost_results_ghost_id ON ghost_results(ghost_id)",
    ]
    with get_conn() as conn:
        for ddl in ddls:
            conn.execute(ddl)
    logger.info("[DB] Ghost Learning 2.0 tabloları hazır.")


def get_ghost_stats() -> dict:
    """Dashboard için ghost learning özet istatistikleri."""
    try:
        with get_conn() as conn:
            total = conn.execute(
                "SELECT COUNT(*) FROM ghost_signals"
            ).fetchone()[0]
            pending = conn.execute(
                "SELECT COUNT(*) FROM ghost_signals WHERE simulated=0"
            ).fetchone()[0]
            wins = conn.execute(
                "SELECT COUNT(*) FROM ghost_results WHERE virtual_outcome='WIN'"
            ).fetchone()[0]
            losses = conn.execute(
                "SELECT COUNT(*) FROM ghost_results WHERE virtual_outcome='LOSS'"
            ).fetchone()[0]
            avg_r_row = conn.execute(
                "SELECT AVG(virtual_pnl_r) FROM ghost_results WHERE virtual_outcome IN ('WIN','LOSS')"
            ).fetchone()
            avg_r = float(avg_r_row[0]) if avg_r_row and avg_r_row[0] else 0.0
            vwr = wins * 100 / (wins + losses) if (wins + losses) > 0 else 0.0
            return {
                "ghost_total": total,
                "ghost_pending": pending,
                "ghost_wins": wins,
                "ghost_losses": losses,
                "ghost_virtual_wr": round(vwr, 1),
                "ghost_avg_r": round(avg_r, 3),
            }
    except Exception as exc:
        logger.warning("[DB] get_ghost_stats: %s", exc)
        return {
            "ghost_total": 0, "ghost_pending": 0,
            "ghost_wins": 0, "ghost_losses": 0,
            "ghost_virtual_wr": 0, "ghost_avg_r": 0,
        }


def update_candidate_status(candidate_id: int, **kwargs):
    """signal_candidates kaydının durumunu günceller."""
    try:
        # lifecycle_stage/execution_status → decision/veto_reason eşlemesi
        col_map = {
            "decision": "decision",
            "reject_reason": "veto_reason",
            "ai_veto_reason": "veto_reason",
            "linked_trade_id": "linked_trade_id",
            "lifecycle_stage": None,   # kolon yok, atla
            "execution_status": None,  # kolon yok, atla
        }
        with get_conn() as conn:
            valid_cols = {r[1] for r in conn.execute(
                "PRAGMA table_info(signal_candidates)"
            ).fetchall()}
        updates = {}
        for k, v in kwargs.items():
            mapped = col_map.get(k, k)
            if mapped and mapped in valid_cols:
                updates[mapped] = v
        if not updates:
            return
        set_clause = ", ".join(f"{k}=?" for k in updates)
        with get_conn() as conn:
            conn.execute(
                f"UPDATE signal_candidates SET {set_clause} WHERE id=?",
                list(updates.values()) + [candidate_id]
            )
    except Exception as e:
        logger.debug(f"update_candidate_status: {e}")


def get_daily_signal_count() -> dict:
    """Bugün üretilen sinyallerin kalite bazlı dağılımını döndürür."""
    try:
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        with get_conn() as conn:
            rows = conn.execute("""
                SELECT decision, COUNT(*) as cnt
                FROM signal_candidates
                WHERE DATE(created_at) = ?
                GROUP BY decision
            """, (today,)).fetchall()
        result = {}
        total = 0
        for r in rows:
            result[r[0] or "UNKNOWN"] = r[1]
            total += r[1]
        result["total"] = total
        return result
    except Exception as e:
        logger.warning(f"get_daily_signal_count: {e}")
        return {"total": 0}


# ─────────────────────────────────────────────────────────────────────────────
# COIN CONFIGS — per-coin nightly optimizer parametreleri
# ─────────────────────────────────────────────────────────────────────────────

def get_coin_config(coin: str) -> dict:
    """Coin'in optimize edilmiş parametrelerini döndürür. Yoksa boş dict."""
    try:
        with get_conn() as conn:
            row = conn.execute(
                "SELECT config_json FROM coin_configs WHERE coin = ?", (coin,)
            ).fetchone()
            if row:
                return json.loads(row[0]) if row[0] else {}
    except Exception as e:
        logger.warning(f"get_coin_config({coin}): {e}")
    return {}


def save_coin_config(coin: str, config: dict) -> None:
    """Per-coin config'i upsert eder, version'ı artırır."""
    try:
        now_str = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
        config_json = json.dumps(config, ensure_ascii=False)
        with get_conn() as conn:
            existing = conn.execute(
                "SELECT version FROM coin_configs WHERE coin = ?", (coin,)
            ).fetchone()
            if existing:
                new_version = (existing[0] or 1) + 1
                conn.execute(
                    "UPDATE coin_configs SET config_json=?, updated_at=?, version=? WHERE coin=?",
                    (config_json, now_str, new_version, coin)
                )
            else:
                conn.execute(
                    "INSERT INTO coin_configs (coin, config_json, updated_at, version) VALUES (?,?,?,1)",
                    (coin, config_json, now_str)
                )
            conn.commit()
    except Exception as e:
        logger.warning(f"save_coin_config({coin}): {e}")


def get_all_coin_configs() -> dict:
    """Tüm coin config'lerini {coin: config_dict} olarak döndürür."""
    try:
        with get_conn() as conn:
            rows = conn.execute(
                "SELECT coin, config_json FROM coin_configs"
            ).fetchall()
            return {r[0]: json.loads(r[1]) if r[1] else {} for r in rows}
    except Exception as e:
        logger.warning(f"get_all_coin_configs: {e}")
        return {}


def get_pending_ghost_suggestions(min_confidence: str = "MEDIUM") -> list:
    """Uygulanmamış ghost threshold önerilerini döner."""
    confidence_order = {"LOW": 0, "MEDIUM": 1, "HIGH": 2}
    min_level = confidence_order.get(min_confidence, 1)
    try:
        with get_conn() as conn:
            rows = conn.execute("""
                SELECT id, symbol, trigger_type,
                       current_threshold, suggested_threshold,
                       virtual_wr, avg_virtual_r, sample_count, confidence
                FROM ghost_suggestions
                WHERE applied = 0
                ORDER BY avg_virtual_r DESC
            """).fetchall()
            result = []
            for r in rows:
                if confidence_order.get(r[8], 0) >= min_level:
                    result.append({
                        "id": r[0], "symbol": r[1], "trigger_type": r[2],
                        "current_threshold": r[3], "suggested_threshold": r[4],
                        "virtual_wr": r[5], "avg_virtual_r": r[6],
                        "sample_count": r[7], "confidence": r[8],
                    })
            return result
    except Exception as exc:
        logger.warning("[DB] get_pending_ghost_suggestions: %s", exc)
        return []


def mark_ghost_suggestion_applied(suggestion_id: int) -> None:
    """Ghost öneriyi uygulandı olarak işaretler."""
    try:
        with get_conn() as conn:
            conn.execute(
                "UPDATE ghost_suggestions SET applied = 1 WHERE id = ?",
                (suggestion_id,)
            )
    except Exception as exc:
        logger.warning("[DB] mark_ghost_suggestion_applied(%s): %s", suggestion_id, exc)
