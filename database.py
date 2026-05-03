"""
database.py — AX Merkezi Veritabanı
=====================================
WAL mode, tek writer, tüm tablolar burada.

Tablolar:
  trades               — Açık/kapanan trade'ler
  signal_candidates    — Her sinyal buraya kaydedilir
  paper_account        — Paper trading bakiyesi
  params               — Bot parametreleri
  ai_logs              — AX kararları ve gerekçeleri
  trade_postmortem     — Trade sonrası MFE/MAE analizi
  coin_profile         — Coin öğrenme profili
  coin_market_memory   — Coin piyasa hafızası
  coin_cooldown        — Coin bazlı bekleme
  daily_summary        — Günlük özet
  weekly_summary       — Haftalık özet
  dashboard_snapshots  — Dashboard geçmişi
  best_params          — En iyi parametre seti
  system_state         — Sistem durumu (CB, pause vb.)
  pattern_memory       — Genel pattern hafızası
"""

import sqlite3
import json
import logging
import uuid
from datetime import datetime, timezone, date, timedelta
from config import DB_PATH

logger = logging.getLogger(__name__)


# ─────────────────────────────────────────────────────────────────────────────
# BAĞLANTI
# ─────────────────────────────────────────────────────────────────────────────

def get_conn() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH, timeout=10)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA synchronous=NORMAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


# ─────────────────────────────────────────────────────────────────────────────
# INIT — TÜM TABLOLAR
# ─────────────────────────────────────────────────────────────────────────────

def init_db():
    with get_conn() as conn:
        conn.executescript("""

        -- ── trades ──────────────────────────────────────────────────────────
        CREATE TABLE IF NOT EXISTS trades (
            id               INTEGER PRIMARY KEY AUTOINCREMENT,
            symbol           TEXT NOT NULL,
            direction        TEXT NOT NULL,
            status           TEXT DEFAULT 'open',
            environment      TEXT DEFAULT 'paper',
            ax_mode          TEXT DEFAULT 'execute',

            entry            REAL,
            sl               REAL,
            tp1              REAL,
            tp2              REAL,
            trail_stop       REAL,

            qty              REAL,
            qty_tp1          REAL,
            qty_tp2          REAL,
            qty_runner       REAL,

            realized_pnl     REAL DEFAULT 0,
            net_pnl          REAL DEFAULT 0,
            r_multiple       REAL DEFAULT 0,

            tp1_hit          INTEGER DEFAULT 0,
            tp2_hit          INTEGER DEFAULT 0,

            linked_candidate_id INTEGER,
            linked_candidate_uuid TEXT,
            open_time        TEXT,
            close_time       TEXT,
            close_price      REAL,
            close_reason     TEXT,
            hold_minutes     REAL DEFAULT 0
        );

        -- ── signal_candidates ────────────────────────────────────────────────
        CREATE TABLE IF NOT EXISTS signal_candidates (
            id               INTEGER PRIMARY KEY AUTOINCREMENT,
            symbol           TEXT NOT NULL,
            direction        TEXT,
            entry            REAL,
            sl               REAL,
            tp1              REAL,
            tp2              REAL,
            runner_target    REAL,
            rr               REAL,
            expected_mfe_r   REAL,
            score            REAL DEFAULT 0,
            confidence       REAL DEFAULT 0,
            decision         TEXT DEFAULT 'PENDING',
            veto_reason      TEXT,
            session          TEXT,
            market_regime    TEXT,
            ax_mode          TEXT DEFAULT 'execute',
            execution_mode   TEXT DEFAULT 'paper',
            linked_trade_id  INTEGER,
            created_at       TEXT DEFAULT (datetime('now'))
        );

        -- ── paper_account ────────────────────────────────────────────────────
        CREATE TABLE IF NOT EXISTS paper_account (
            id              INTEGER PRIMARY KEY CHECK (id = 1),
            balance         REAL DEFAULT 250.0,
            initial_balance REAL DEFAULT 250.0,
            updated_at      TEXT DEFAULT (datetime('now'))
        );

        -- ── params ──────────────────────────────────────────────────────────
        CREATE TABLE IF NOT EXISTS params (
            id         INTEGER PRIMARY KEY AUTOINCREMENT,
            data       TEXT NOT NULL,
            reason     TEXT,
            created_at TEXT DEFAULT (datetime('now'))
        );

        -- ── ai_logs ─────────────────────────────────────────────────────────
        CREATE TABLE IF NOT EXISTS ai_logs (
            id           INTEGER PRIMARY KEY AUTOINCREMENT,
            event        TEXT,
            symbol       TEXT,
            decision     TEXT,
            score        REAL,
            confidence   REAL,
            reason       TEXT,
            data         TEXT,
            created_at   TEXT DEFAULT (datetime('now'))
        );

        -- ── trade_postmortem ─────────────────────────────────────────────────
        CREATE TABLE IF NOT EXISTS trade_postmortem (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            trade_id        INTEGER UNIQUE,
            symbol          TEXT,
            direction       TEXT,
            mfe_r           REAL,
            mae_r           REAL,
            efficiency      REAL,
            missed_gain     REAL,
            sl_tightness    REAL,
            hold_minutes    REAL,
            exit_quality    REAL,
            setup_quality   REAL,
            notes           TEXT,
            created_at      TEXT DEFAULT (datetime('now'))
        );

        -- ── coin_profile ─────────────────────────────────────────────────────
        CREATE TABLE IF NOT EXISTS coin_profile (
            symbol              TEXT PRIMARY KEY,
            trade_count         INTEGER DEFAULT 0,
            win_count           INTEGER DEFAULT 0,
            loss_count          INTEGER DEFAULT 0,
            win_rate            REAL DEFAULT 0,
            avg_r               REAL DEFAULT 0,
            profit_factor       REAL DEFAULT 0,
            avg_mfe             REAL DEFAULT 0,
            avg_mae             REAL DEFAULT 0,
            best_session        TEXT,
            preferred_direction TEXT,
            danger_score        REAL DEFAULT 0,
            fakeout_rate        REAL DEFAULT 0,
            volatility_profile  TEXT DEFAULT 'normal',
            updated_at          TEXT DEFAULT (datetime('now'))
        );

        -- ── coin_market_memory ───────────────────────────────────────────────
        CREATE TABLE IF NOT EXISTS coin_market_memory (
            id         INTEGER PRIMARY KEY AUTOINCREMENT,
            symbol     TEXT NOT NULL,
            session    TEXT,
            regime     TEXT,
            direction  TEXT,
            result     TEXT,
            r_multiple REAL,
            created_at TEXT DEFAULT (datetime('now'))
        );

        -- ── coin_cooldown ────────────────────────────────────────────────────
        CREATE TABLE IF NOT EXISTS coin_cooldown (
            symbol      TEXT PRIMARY KEY,
            reason      TEXT,
            until       TEXT NOT NULL,
            created_at  TEXT DEFAULT (datetime('now'))
        );

        -- ── daily_summary ────────────────────────────────────────────────────
        CREATE TABLE IF NOT EXISTS daily_summary (
            id           INTEGER PRIMARY KEY AUTOINCREMENT,
            date         TEXT UNIQUE,
            trade_count  INTEGER DEFAULT 0,
            win_count    INTEGER DEFAULT 0,
            loss_count   INTEGER DEFAULT 0,
            win_rate     REAL DEFAULT 0,
            gross_pnl    REAL DEFAULT 0,
            net_pnl      REAL DEFAULT 0,
            avg_r        REAL DEFAULT 0,
            max_drawdown REAL DEFAULT 0,
            balance_eod  REAL DEFAULT 0,
            created_at   TEXT DEFAULT (datetime('now'))
        );

        -- ── weekly_summary ───────────────────────────────────────────────────
        CREATE TABLE IF NOT EXISTS weekly_summary (
            id           INTEGER PRIMARY KEY AUTOINCREMENT,
            week_start   TEXT UNIQUE,
            trade_count  INTEGER DEFAULT 0,
            win_count    INTEGER DEFAULT 0,
            loss_count   INTEGER DEFAULT 0,
            win_rate     REAL DEFAULT 0,
            net_pnl      REAL DEFAULT 0,
            avg_r        REAL DEFAULT 0,
            best_day     TEXT,
            worst_day    TEXT,
            created_at   TEXT DEFAULT (datetime('now'))
        );

        -- ── dashboard_snapshots ──────────────────────────────────────────────
        CREATE TABLE IF NOT EXISTS dashboard_snapshots (
            id         INTEGER PRIMARY KEY AUTOINCREMENT,
            note       TEXT,
            balance    REAL,
            data       TEXT,
            created_at TEXT DEFAULT (datetime('now'))
        );

        -- ── best_params ──────────────────────────────────────────────────────
        CREATE TABLE IF NOT EXISTS best_params (
            id            INTEGER PRIMARY KEY AUTOINCREMENT,
            data          TEXT,
            params_json   TEXT,
            win_rate      REAL DEFAULT 0,
            avg_r         REAL DEFAULT 0,
            pnl           REAL DEFAULT 0,
            trade_count   INTEGER DEFAULT 0,
            profit_factor REAL DEFAULT 0,
            created_at    TEXT DEFAULT (datetime('now'))
        );

        -- ── system_state ─────────────────────────────────────────────────────
        CREATE TABLE IF NOT EXISTS system_state (
            key        TEXT PRIMARY KEY,
            value      TEXT,
            updated_at TEXT DEFAULT (datetime('now'))
        );

        -- ── scalp_signals (Yeni Tek Schema) ──────────────────────────────────
        CREATE TABLE IF NOT EXISTS scalp_signals (
            id                 TEXT PRIMARY KEY,
            symbol             TEXT NOT NULL,
            timestamp          REAL,
            source             TEXT DEFAULT 'system',
            timeframe          TEXT DEFAULT '1m',
            direction          TEXT,
            coin_score         REAL DEFAULT 0,
            trend_score        REAL DEFAULT 0,
            trigger_score      REAL DEFAULT 0,
            risk_score         REAL DEFAULT 0,
            final_score        REAL DEFAULT 0,
            setup_quality      TEXT DEFAULT 'D',
            entry_zone         REAL DEFAULT 0,
            stop_loss          REAL DEFAULT 0,
            tp1                REAL DEFAULT 0,
            tp2                REAL DEFAULT 0,
            tp3                REAL DEFAULT 0,
            rr                 REAL DEFAULT 0,
            risk_percent       REAL DEFAULT 0,
            position_size      REAL DEFAULT 0,
            notional_size      REAL DEFAULT 0,
            leverage_suggestion INTEGER DEFAULT 1,
            max_loss           REAL DEFAULT 0,
            invalidation_level REAL DEFAULT 0,
            confidence         REAL DEFAULT 0,
            status             TEXT DEFAULT 'pending',
            reason             TEXT DEFAULT '',
            telegram_status    TEXT DEFAULT 'pending',
            dashboard_status   TEXT DEFAULT 'pending',
            error              TEXT DEFAULT '',
            created_at         TEXT DEFAULT (datetime('now')),
            archived_at        TEXT
        );

        -- ── pattern_memory ───────────────────────────────────────────────────
        CREATE TABLE IF NOT EXISTS pattern_memory (
            id           INTEGER PRIMARY KEY AUTOINCREMENT,
            symbol       TEXT,
            direction    TEXT,
            session      TEXT,
            result       TEXT,
            net_pnl      REAL,
            hold_minutes REAL,
            partial_exit INTEGER DEFAULT 0,
            created_at   TEXT DEFAULT (datetime('now'))
        );

        -- ── market_snapshots ────────────────────────────────────────────────
        CREATE TABLE IF NOT EXISTS market_snapshots (
            id                    TEXT PRIMARY KEY,
            symbol                TEXT NOT NULL,
            timestamp             REAL,
            price                 REAL,
            volume_24h            REAL,
            price_change_24h      REAL,
            atr_percent           REAL,
            spread_percent        REAL,
            funding_rate          REAL,
            open_interest_change  REAL,
            tradeability_score    REAL,
            scanner_status        TEXT,
            scanner_reason        TEXT,
            created_at            TEXT DEFAULT (datetime('now'))
        );

        -- ── scanned_coins ────────────────────────────────────────────────────
        CREATE TABLE IF NOT EXISTS scanned_coins (
            id                     TEXT PRIMARY KEY,
            symbol                 TEXT NOT NULL,
            timestamp              REAL,
            scanner_status         TEXT,
            scanner_reason         TEXT,
            volume                 REAL,
            volatility             REAL,
            spread                 REAL,
            price_change           REAL,
            funding                REAL,
            open_interest          REAL,
            trend_cleanliness      REAL,
            tradeability_score     REAL,
            volume_score           REAL,
            volatility_score       REAL,
            spread_score           REAL,
            orderbook_depth_score  REAL,
            open_interest_score    REAL,
            funding_score          REAL,
            trend_cleanliness_score REAL,
            pump_dump_penalty      REAL,
            correlation_penalty    REAL,
            created_at             TEXT DEFAULT (datetime('now'))
        );

        -- ── candidate_signals ────────────────────────────────────────────────
        CREATE TABLE IF NOT EXISTS candidate_signals (
            id                    TEXT PRIMARY KEY,
            signal_id             TEXT,
            symbol                TEXT NOT NULL,
            direction             TEXT,
            trend_score           REAL DEFAULT 0,
            trigger_score         REAL DEFAULT 0,
            risk_score            REAL DEFAULT 0,
            ai_score              REAL DEFAULT 0,
            final_score           REAL DEFAULT 0,
            quality               TEXT DEFAULT 'D',
            reason                TEXT DEFAULT '',
            reject_reason         TEXT DEFAULT '',
            ai_veto_reason        TEXT DEFAULT '',
            risk_reject_reason    TEXT DEFAULT '',
            lifecycle_stage       TEXT DEFAULT 'SCANNED',
            entry                 REAL,
            stop                  REAL,
            tp1                   REAL,
            tp2                   REAL,
            tp3                   REAL,
            rr                    REAL,
            atr                   REAL,
            stop_distance_percent REAL,
            estimated_fee         REAL,
            estimated_slippage    REAL,
            net_rr                REAL,
            position_size         REAL,
            notional              REAL,
            leverage_suggestion   INTEGER,
            risk_amount           REAL,
            max_loss              REAL,
            execution_status      TEXT DEFAULT 'candidate',
            created_at            TEXT DEFAULT (datetime('now'))
        );

        -- ── signal_events ────────────────────────────────────────────────────
        CREATE TABLE IF NOT EXISTS signal_events (
            id            TEXT PRIMARY KEY,
            signal_id     TEXT NOT NULL,
            symbol        TEXT,
            stage         TEXT NOT NULL,
            reason        TEXT DEFAULT '',
            reject_reason TEXT DEFAULT '',
            payload       TEXT,
            created_at    TEXT DEFAULT (datetime('now'))
        );

        -- ── trade_events ─────────────────────────────────────────────────────
        CREATE TABLE IF NOT EXISTS trade_events (
            id            TEXT PRIMARY KEY,
            trade_id      INTEGER,
            signal_id     TEXT,
            symbol        TEXT NOT NULL,
            event_type    TEXT NOT NULL,
            event_payload TEXT,
            created_at    TEXT DEFAULT (datetime('now'))
        );

        -- ── paper_results ────────────────────────────────────────────────────
        CREATE TABLE IF NOT EXISTS paper_results (
            id                      TEXT PRIMARY KEY,
            signal_id               TEXT,
            candidate_id            TEXT,
            symbol                  TEXT NOT NULL,
            direction               TEXT,
            tracked_from            TEXT DEFAULT 'candidate',
            hit_tp                  INTEGER DEFAULT 0,
            hit_stop_first          INTEGER DEFAULT 0,
            time_to_move_minutes    REAL DEFAULT 0,
            max_favorable_excursion REAL DEFAULT 0,
            max_adverse_excursion   REAL DEFAULT 0,
            setup_worked            INTEGER DEFAULT 0,
            would_have_won          INTEGER DEFAULT 0,
            status                  TEXT DEFAULT 'pending',
            preview_entry           REAL,
            preview_sl              REAL,
            preview_tp1             REAL,
            preview_tp2             REAL,
            preview_tp3             REAL,
            leverage_hint           INTEGER DEFAULT 10,
            finalized_at            TEXT,
            first_touch             TEXT DEFAULT '',
            horizon_minutes         REAL DEFAULT 480,
            skip_decision_correct   INTEGER DEFAULT 1,
            final_score_snap        REAL DEFAULT 0,
            reject_reason_snap      TEXT,
            created_at              TEXT DEFAULT (datetime('now'))
        );

        -- ── adaptive_stats ───────────────────────────────────────────────────
        CREATE TABLE IF NOT EXISTS adaptive_stats (
            id                    TEXT PRIMARY KEY,
            scope                 TEXT NOT NULL,
            key                   TEXT NOT NULL,
            sample_size           INTEGER DEFAULT 0,
            win_rate              REAL DEFAULT 0,
            expectancy            REAL DEFAULT 0,
            avg_r                 REAL DEFAULT 0,
            threshold_data        REAL DEFAULT 0,
            threshold_watchlist   REAL DEFAULT 0,
            threshold_telegram    REAL DEFAULT 0,
            threshold_trade       REAL DEFAULT 0,
            action_taken          TEXT DEFAULT '',
            notes                 TEXT DEFAULT '',
            created_at            TEXT DEFAULT (datetime('now'))
        );

        -- ── telegram_messages ────────────────────────────────────────────────
        CREATE TABLE IF NOT EXISTS telegram_messages (
            id            TEXT PRIMARY KEY,
            signal_id     TEXT,
            symbol        TEXT NOT NULL,
            dedupe_key    TEXT UNIQUE,
            status        TEXT DEFAULT 'queued',
            message_body  TEXT,
            created_at    TEXT DEFAULT (datetime('now'))
        );

        -- ── backtest_runs ────────────────────────────────────────────────────
        CREATE TABLE IF NOT EXISTS backtest_runs (
            id            TEXT PRIMARY KEY,
            run_name      TEXT,
            config        TEXT,
            started_at    TEXT,
            finished_at   TEXT,
            summary       TEXT,
            created_at    TEXT DEFAULT (datetime('now'))
        );

        """)

    # ── Eski şemadan yeni kolonlara migration ─────────────────────────────────
    _migrate(get_conn())
    logger.info("DB init tamamlandı.")


def _migrate(conn):
    """Eksik kolonları ekle — her çalıştırmada idempotent."""
    migrations = [
        # trades tablosu — yeni kolonlar
        ("trades", "qty_tp1",            "REAL"),
        ("trades", "qty_tp2",            "REAL"),
        ("trades", "qty_runner",         "REAL"),
        ("trades", "trail_stop",         "REAL"),
        ("trades", "realized_pnl",       "REAL DEFAULT 0"),
        ("trades", "r_multiple",         "REAL DEFAULT 0"),
        ("trades", "tp1_hit",            "INTEGER DEFAULT 0"),
        ("trades", "tp2_hit",            "INTEGER DEFAULT 0"),
        ("trades", "linked_candidate_id","INTEGER"),
        ("trades", "linked_candidate_uuid", "TEXT"),
        ("trades", "hold_minutes",       "REAL DEFAULT 0"),
        ("trades", "ax_mode",            "TEXT DEFAULT 'execute'"),
        ("trades", "environment",        "TEXT DEFAULT 'paper'"),
        ("trades", "close_reason",       "TEXT"),
        # coin_params tablosu — yeni kolonlar
        ("coin_params", "volatility_profile", "TEXT DEFAULT 'normal'"),
        ("coin_params", "sl_atr_mult",        "REAL DEFAULT 1.3"),
        ("coin_params", "tp_atr_mult",        "REAL DEFAULT 2.0"),
        ("coin_params", "risk_pct",           "REAL DEFAULT 1.0"),
        ("coin_params", "max_leverage",       "INTEGER DEFAULT 15"),
        ("coin_params", "min_adx",            "REAL DEFAULT 20"),
        ("coin_params", "min_bb_width",       "REAL DEFAULT 1.3"),
        ("coin_params", "min_volume_m",       "REAL DEFAULT 15.0"),
        ("coin_params", "enabled",            "INTEGER DEFAULT 1"),
        ("coin_params", "updated_at",         "TEXT DEFAULT (datetime('now'))"),
        # paper_account — eski şemada 'paper_balance', yeni şemada 'balance'
        ("paper_account", "balance",         "REAL DEFAULT 250.0"),
        ("paper_account", "initial_balance", "REAL DEFAULT 250.0"),
        ("paper_results", "status", "TEXT DEFAULT 'pending'"),
        ("paper_results", "preview_entry", "REAL"),
        ("paper_results", "preview_sl", "REAL"),
        ("paper_results", "preview_tp1", "REAL"),
        ("paper_results", "preview_tp2", "REAL"),
        ("paper_results", "preview_tp3", "REAL"),
        ("paper_results", "leverage_hint", "INTEGER DEFAULT 10"),
        ("paper_results", "finalized_at", "TEXT"),
        ("paper_results", "first_touch", "TEXT DEFAULT ''"),
        ("paper_results", "horizon_minutes", "REAL DEFAULT 480"),
        ("paper_results", "skip_decision_correct", "INTEGER DEFAULT 1"),
        ("paper_results", "final_score_snap", "REAL DEFAULT 0"),
        ("paper_results", "reject_reason_snap", "TEXT"),
    ]
    for table, col, col_type in migrations:
        try:
            conn.execute(f"ALTER TABLE {table} ADD COLUMN {col} {col_type}")
        except Exception:
            pass  # Kolon zaten var

    # paper_account: eski paper_balance → yeni balance kolonuna kopyala
    try:
        conn.execute(
            "UPDATE paper_account SET balance = paper_balance "
            "WHERE balance IS NULL OR balance = 250.0"
        )
    except Exception:
        pass


# ─────────────────────────────────────────────────────────────────────────────
# PAPER ACCOUNT
# ─────────────────────────────────────────────────────────────────────────────

def init_paper_account(initial: float = 250.0):
    with get_conn() as conn:
        conn.execute(
            "INSERT OR IGNORE INTO paper_account (id, balance, initial_balance) VALUES (1, ?, ?)",
            (initial, initial)
        )

def get_paper_balance() -> float:
    with get_conn() as conn:
        row = conn.execute("SELECT balance FROM paper_account WHERE id=1").fetchone()
        return float(row["balance"]) if row else 250.0

def update_paper_balance(pnl: float):
    with get_conn() as conn:
        conn.execute(
            "UPDATE paper_account SET balance=balance+?, updated_at=datetime('now') WHERE id=1",
            (pnl,)
        )


# ─────────────────────────────────────────────────────────────────────────────
# SIGNAL CANDIDATES
# ─────────────────────────────────────────────────────────────────────────────

def save_signal_candidate(sig: dict) -> int:
    sql = """
        INSERT INTO signal_candidates
            (symbol, direction, entry, sl, tp1, tp2, runner_target,
             rr, expected_mfe_r, score, confidence, decision, veto_reason,
             session, market_regime, ax_mode, execution_mode)
        VALUES
            (:symbol, :direction, :entry, :sl, :tp1, :tp2, :runner_target,
             :rr, :expected_mfe_r, :score, :confidence, :decision, :veto_reason,
             :session, :market_regime, :ax_mode, :execution_mode)
    """
    defaults = {
        "direction": None, "entry": None, "sl": None,
        "tp1": None, "tp2": None, "runner_target": None,
        "rr": 0, "expected_mfe_r": 0, "score": 0, "confidence": 0,
        "decision": "PENDING", "veto_reason": None,
        "session": None, "market_regime": None,
        "ax_mode": "execute", "execution_mode": "paper",
    }
    with get_conn() as conn:
        cur = conn.execute(sql, {**defaults, **sig})
        return cur.lastrowid

def update_signal_decision(candidate_id: int, decision: str, score: float,
                            confidence: float, veto_reason: str = None,
                            linked_trade_id: int = None):
    with get_conn() as conn:
        conn.execute(
            """UPDATE signal_candidates
               SET decision=?, score=?, confidence=?, veto_reason=?, linked_trade_id=?
               WHERE id=?""",
            (decision, score, confidence, veto_reason, linked_trade_id, candidate_id)
        )


# ─────────────────────────────────────────────────────────────────────────────
# TRADES
# ─────────────────────────────────────────────────────────────────────────────

def save_trade(t: dict) -> int:
    sql = """
        INSERT INTO trades
            (symbol, direction, status, environment, ax_mode,
             entry, sl, tp1, tp2, trail_stop,
             qty, qty_tp1, qty_tp2, qty_runner,
             linked_candidate_id, linked_candidate_uuid, open_time)
        VALUES
            (:symbol, :direction, :status, :environment, :ax_mode,
             :entry, :sl, :tp1, :tp2, :trail_stop,
             :qty, :qty_tp1, :qty_tp2, :qty_runner,
             :linked_candidate_id, :linked_candidate_uuid, :open_time)
    """
    defaults = {
        "status": "open", "environment": "paper", "ax_mode": "execute",
        "trail_stop": None, "qty_tp1": None, "qty_tp2": None, "qty_runner": None,
        "linked_candidate_id": None,
        "linked_candidate_uuid": None,
        "open_time": datetime.now(timezone.utc).isoformat(),
    }
    with get_conn() as conn:
        cur = conn.execute(sql, {**defaults, **t})
        return cur.lastrowid

def update_trade(trade_id: int, fields: dict):
    if not fields:
        return
    sets = ", ".join(f"{k}=?" for k in fields)
    vals = list(fields.values()) + [trade_id]
    with get_conn() as conn:
        conn.execute(f"UPDATE trades SET {sets} WHERE id=?", vals)

def close_trade(trade_id: int, close_price: float, net_pnl: float,
                reason: str, hold_minutes: float = 0):
    now = datetime.now(timezone.utc).isoformat()
    # Status'u reason ve pnl'e göre belirle
    _r = (reason or "").upper()
    if _r in ("SL", "SL/BE"):
        _status = "sl"
    elif _r == "TIMEOUT":
        _status = "timeout"
    elif _r == "TRAIL":
        _status = "trail"
    elif net_pnl > 0:
        _status = "closed_win"
    else:
        _status = "closed_loss"
    with get_conn() as conn:
        conn.execute(
            """UPDATE trades SET
                status=?, close_price=?, net_pnl=?, close_reason=?,
                hold_minutes=?, close_time=?
               WHERE id=?""",
            (_status, close_price, net_pnl, reason, hold_minutes, now, trade_id)
        )

def get_trade(trade_id: int) -> dict | None:
    with get_conn() as conn:
        row = conn.execute("SELECT * FROM trades WHERE id=?", (trade_id,)).fetchone()
        return dict(row) if row else None

def get_open_trades() -> list:
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT * FROM trades WHERE status IN ('open','tp1_hit','runner') ORDER BY open_time"
        ).fetchall()
        return [dict(r) for r in rows]

def get_trades(limit: int = 100, status: str = None, symbol: str = None) -> list:
    sql = "SELECT * FROM trades WHERE 1=1"
    params = []
    if status:
        sql += " AND status=?"
        params.append(status)
    if symbol:
        sql += " AND symbol=?"
        params.append(symbol)
    sql += " ORDER BY id DESC LIMIT ?"
    params.append(limit)
    with get_conn() as conn:
        return [dict(r) for r in conn.execute(sql, params).fetchall()]


# ─────────────────────────────────────────────────────────────────────────────
# POSTMORTEM
# ─────────────────────────────────────────────────────────────────────────────

def save_postmortem(data: dict):
    sql = """
        INSERT OR REPLACE INTO trade_postmortem
            (trade_id, symbol, direction, mfe_r, mae_r, efficiency,
             missed_gain, sl_tightness, hold_minutes, exit_quality, setup_quality, notes)
        VALUES
            (:trade_id, :symbol, :direction, :mfe_r, :mae_r, :efficiency,
             :missed_gain, :sl_tightness, :hold_minutes, :exit_quality, :setup_quality, :notes)
    """
    defaults = {k: None for k in ["symbol","direction","mfe_r","mae_r","efficiency",
                                   "missed_gain","sl_tightness","hold_minutes",
                                   "exit_quality","setup_quality","notes"]}
    with get_conn() as conn:
        conn.execute(sql, {**defaults, **data})


# ─────────────────────────────────────────────────────────────────────────────
# COIN PROFILE
# ─────────────────────────────────────────────────────────────────────────────

def get_coin_profile(symbol: str) -> dict:
    with get_conn() as conn:
        row = conn.execute(
            "SELECT * FROM coin_profile WHERE symbol=?", (symbol,)
        ).fetchone()
        return dict(row) if row else {}

def upsert_coin_profile(symbol: str, fields: dict):
    with get_conn() as conn:
        existing = conn.execute(
            "SELECT symbol FROM coin_profile WHERE symbol=?", (symbol,)
        ).fetchone()
        if existing:
            sets = ", ".join(f"{k}=?" for k in fields) + ", updated_at=datetime('now')"
            conn.execute(
                f"UPDATE coin_profile SET {sets} WHERE symbol=?",
                list(fields.values()) + [symbol]
            )
        else:
            fields["symbol"] = symbol
            cols = ", ".join(fields.keys())
            vals = ", ".join("?" * len(fields))
            conn.execute(
                f"INSERT INTO coin_profile ({cols}) VALUES ({vals})",
                list(fields.values())
            )

def save_coin_market_memory(symbol: str, session: str, regime: str,
                             direction: str, result: str, r_multiple: float):
    with get_conn() as conn:
        conn.execute(
            """INSERT INTO coin_market_memory
               (symbol, session, regime, direction, result, r_multiple)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (symbol, session, regime, direction, result, r_multiple)
        )


# ─────────────────────────────────────────────────────────────────────────────
# COIN COOLDOWN
# ─────────────────────────────────────────────────────────────────────────────

def set_coin_cooldown(symbol: str, minutes: int, reason: str = ""):
    until = (datetime.now(timezone.utc) + timedelta(minutes=minutes)).isoformat()
    with get_conn() as conn:
        conn.execute(
            "INSERT OR REPLACE INTO coin_cooldown (symbol, reason, until) VALUES (?, ?, ?)",
            (symbol, reason, until)
        )

def is_coin_in_cooldown(symbol: str) -> bool:
    now = datetime.now(timezone.utc).isoformat()
    with get_conn() as conn:
        row = conn.execute(
            "SELECT until FROM coin_cooldown WHERE symbol=? AND until > ?",
            (symbol, now)
        ).fetchone()
        return row is not None

def clear_expired_cooldowns():
    now = datetime.now(timezone.utc).isoformat()
    with get_conn() as conn:
        conn.execute("DELETE FROM coin_cooldown WHERE until <= ?", (now,))


# ─────────────────────────────────────────────────────────────────────────────
# DAILY / WEEKLY SUMMARY
# ─────────────────────────────────────────────────────────────────────────────

def save_daily_summary(data: dict):
    sql = """
        INSERT OR REPLACE INTO daily_summary
            (date, trade_count, win_count, loss_count, win_rate,
             gross_pnl, net_pnl, avg_r, max_drawdown, balance_eod)
        VALUES
            (:date, :trade_count, :win_count, :loss_count, :win_rate,
             :gross_pnl, :net_pnl, :avg_r, :max_drawdown, :balance_eod)
    """
    defaults = {k: 0 for k in ["trade_count","win_count","loss_count","win_rate",
                                "gross_pnl","net_pnl","avg_r","max_drawdown","balance_eod"]}
    defaults["date"] = date.today().isoformat()
    with get_conn() as conn:
        conn.execute(sql, {**defaults, **data})

def get_daily_summaries(days: int = 30) -> list:
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT * FROM daily_summary ORDER BY date DESC LIMIT ?", (days,)
        ).fetchall()
        return [dict(r) for r in rows]

def save_weekly_summary(data: dict):
    sql = """
        INSERT OR REPLACE INTO weekly_summary
            (week_start, trade_count, win_count, loss_count, win_rate,
             net_pnl, avg_r, best_day, worst_day)
        VALUES
            (:week_start, :trade_count, :win_count, :loss_count, :win_rate,
             :net_pnl, :avg_r, :best_day, :worst_day)
    """
    defaults = {k: 0 for k in ["trade_count","win_count","loss_count","win_rate","net_pnl","avg_r"]}
    defaults.update({"best_day": None, "worst_day": None})
    with get_conn() as conn:
        conn.execute(sql, {**defaults, **data})


# ─────────────────────────────────────────────────────────────────────────────
# STATS
# ─────────────────────────────────────────────────────────────────────────────

def get_stats(hours: int = 720) -> dict:
    """Son N saatin istatistikleri (varsayilan: 720 saat = 30 gun)."""
    cutoff = (datetime.now(timezone.utc) - timedelta(hours=hours)).isoformat()
    with get_conn() as conn:
        rows = conn.execute(
            """SELECT * FROM trades
               WHERE status IN ('closed','closed_win','closed_loss','sl','tp1_hit','runner','trail','timeout')
               AND close_time >= ? AND close_time IS NOT NULL""",
            (cutoff,)
        ).fetchall()
        open_rows = conn.execute(
            "SELECT COUNT(*) FROM trades WHERE close_time IS NULL"
        ).fetchone()
    trades = [dict(r) for r in rows]
    open_count = open_rows[0] if open_rows else 0
    _empty = {
        "total": 0, "wins": 0, "losses": 0, "win_rate": 0,
        "total_pnl": 0, "avg_pnl": 0, "avg_win": 0, "avg_loss": 0,
        "avg_r": 0, "avg_rr": 0, "profit_factor": 0, "max_drawdown": 0,
        "best_trade": 0, "worst_trade": 0, "avg_dur": 0,
        "open_count": open_count, "last_ai": {},
    }
    if not trades:
        return _empty
    wins   = [t for t in trades if (t.get("net_pnl") or 0) > 0]
    losses = [t for t in trades if (t.get("net_pnl") or 0) <= 0]
    pnls   = [t.get("net_pnl") or 0 for t in trades]
    gross_win  = sum(t["net_pnl"] for t in wins)
    gross_loss = abs(sum(t["net_pnl"] for t in losses))
    cum, peak, max_dd = 0, 0, 0
    for p in reversed(pnls):
        cum += p
        peak = max(peak, cum)
        max_dd = max(max_dd, peak - cum)
    # Ortalama sure (dakika)
    durations = []
    for t in trades:
        try:
            if t.get("open_time") and t.get("close_time"):
                from datetime import datetime as _dt
                ot = _dt.fromisoformat(str(t["open_time"]).replace("Z",""))
                ct = _dt.fromisoformat(str(t["close_time"]).replace("Z",""))
                durations.append(abs((ct - ot).total_seconds() / 60))
        except Exception:
            pass
    avg_dur = round(sum(durations) / len(durations), 1) if durations else 0
    r_vals = [t.get("r_multiple") or 0 for t in trades]
    return {
        "total":         len(trades),
        "wins":          len(wins),
        "losses":        len(losses),
        "win_rate":      round(len(wins) / len(trades), 4),
        "total_pnl":     round(sum(pnls), 4),
        "avg_pnl":       round(sum(pnls) / len(trades), 4),
        "avg_win":       round(gross_win  / len(wins),   4) if wins   else 0,
        "avg_loss":      round(gross_loss / len(losses), 4) if losses else 0,
        "avg_r":         round(sum(r_vals) / len(trades), 4),
        "avg_rr":        round(sum(r_vals) / len(trades), 4),
        "profit_factor": round(gross_win / gross_loss, 4) if gross_loss > 0 else 0,
        "max_drawdown":  round(max_dd, 4),
        "best_trade":    round(max(pnls), 4),
        "worst_trade":   round(min(pnls), 4),
        "avg_dur":       avg_dur,
        "open_count":    open_count,
        "last_ai":       {},
    }

# ─────────────────────────────────────────────────────────────────────────────
# SYSTEM STATE
# ─────────────────────────────────────────────────────────────────────────────

def get_state(key: str, default=None):
    with get_conn() as conn:
        row = conn.execute(
            "SELECT value FROM system_state WHERE key=?", (key,)
        ).fetchone()
        if row is None:
            return default
        try:
            return json.loads(row["value"])
        except Exception:
            return row["value"]

def set_state(key: str, value):
    with get_conn() as conn:
        conn.execute(
            "INSERT OR REPLACE INTO system_state (key, value, updated_at) VALUES (?, ?, datetime('now'))",
            (key, json.dumps(value))
        )


# ─────────────────────────────────────────────────────────────────────────────
# AI LOGS
# ─────────────────────────────────────────────────────────────────────────────

def save_ai_log(event: str, symbol: str = None, decision: str = None,
                score: float = None, confidence: float = None,
                reason: str = None, data: dict = None):
    with get_conn() as conn:
        conn.execute(
            """INSERT INTO ai_logs (event, symbol, decision, score, confidence, reason, data)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (event, symbol, decision, score, confidence, reason,
             json.dumps(data) if data else None)
        )


# ─────────────────────────────────────────────────────────────────────────────
# PARAMS
# ─────────────────────────────────────────────────────────────────────────────

def get_current_params() -> dict:
    with get_conn() as conn:
        row = conn.execute(
            "SELECT data FROM params ORDER BY id DESC LIMIT 1"
        ).fetchone()
        if row:
            try:
                return json.loads(row["data"])
            except Exception:
                pass
    return {}

def save_params(params: dict, reason: str = ""):
    with get_conn() as conn:
        conn.execute(
            "INSERT INTO params (data, reason) VALUES (?, ?)",
            (json.dumps(params), reason)
        )

def save_best_params(params: dict, win_rate: float, avg_r: float,
                     pnl: float, trade_count: int):
    with get_conn() as conn:
        conn.execute(
            """INSERT INTO best_params (data, win_rate, avg_r, pnl, trade_count)
               VALUES (?, ?, ?, ?, ?)""",
            (json.dumps(params), win_rate, avg_r, pnl, trade_count)
        )


# ─────────────────────────────────────────────────────────────────────────────
# DASHBOARD SNAPSHOT
# ─────────────────────────────────────────────────────────────────────────────

def save_snapshot(note: str = "", balance: float = 0, data: dict = None):
    with get_conn() as conn:
        conn.execute(
            "INSERT INTO dashboard_snapshots (note, balance, data) VALUES (?, ?, ?)",
            (note, balance, json.dumps(data or {}))
        )


# ─────────────────────────────────────────────────────────────────────────────
# PATTERN MEMORY
# ─────────────────────────────────────────────────────────────────────────────

def save_pattern(symbol: str, direction: str, session: str, result: str,
                 net_pnl: float, hold_minutes: float = 0, partial_exit: int = 0):
    with get_conn() as conn:
        conn.execute(
            """INSERT INTO pattern_memory
               (symbol, direction, session, result, net_pnl, hold_minutes, partial_exit)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (symbol, direction, session, result, net_pnl, hold_minutes, partial_exit)
        )

# ─────────────────────────────────────────────────────────────────────────────
# SCALP SIGNALS — Yeni Tek Schema
# ─────────────────────────────────────────────────────────────────────────────
def save_scalp_signal(sig_data: dict):
    """Yeni schema ile sinyali kaydet."""
    with get_conn() as conn:
        conn.execute("""
            INSERT OR REPLACE INTO scalp_signals (
                id, symbol, timestamp, source, timeframe, direction,
                coin_score, trend_score, trigger_score, risk_score, final_score,
                setup_quality, entry_zone, stop_loss, tp1, tp2, tp3,
                rr, risk_percent, position_size, notional_size, leverage_suggestion,
                max_loss, invalidation_level, confidence, status, reason,
                telegram_status, dashboard_status, error
            ) VALUES (
                :id, :symbol, :timestamp, :source, :timeframe, :direction,
                :coin_score, :trend_score, :trigger_score, :risk_score, :final_score,
                :setup_quality, :entry_zone, :stop_loss, :tp1, :tp2, :tp3,
                :rr, :risk_percent, :position_size, :notional_size, :leverage_suggestion,
                :max_loss, :invalidation_level, :confidence, :status, :reason,
                :telegram_status, :dashboard_status, :error
            )
        """, sig_data)

def get_active_scalp_signals(limit: int = 100) -> list:
    """Dashboard için aktif sinyalleri getir (null veri olmadan)."""
    with get_conn() as conn:
        rows = conn.execute("""
            SELECT * FROM scalp_signals
            WHERE status != 'archived'
              AND direction IS NOT NULL
              AND entry_zone > 0
              AND setup_quality NOT IN ('D', '')
            ORDER BY final_score DESC
            LIMIT ?
        """, (limit,)).fetchall()
    return [dict(r) for r in rows]

def archive_old_scalp_signals(hours: int = 24):
    """Eski sinyalleri arşivle."""
    with get_conn() as conn:
        conn.execute("""
            UPDATE scalp_signals
            SET status = 'archived', archived_at = datetime('now')
            WHERE created_at < datetime('now', ?) AND status != 'archived'
        """, (f"-{hours} hours",))

def get_daily_signal_count() -> dict:
    """Bugünkü sinyal istatistikleri."""
    with get_conn() as conn:
        rows = conn.execute("""
            SELECT setup_quality, COUNT(*) as cnt
            FROM scalp_signals
            WHERE created_at >= datetime('now', '-1 day')
              AND status != 'archived'
            GROUP BY setup_quality
        """).fetchall()
    counts = {"S": 0, "A+": 0, "A": 0, "B": 0, "C": 0, "total": 0}
    for row in rows:
        q = row["setup_quality"]
        if q in counts:
            counts[q] = row["cnt"]
        counts["total"] += row["cnt"]
    return counts


VALID_SIGNAL_STAGES = {
    "SCANNED", "TREND_CHECKED", "TRIGGER_CHECKED", "RISK_CHECKED", "AI_CHECKED",
    "APPROVED_FOR_WATCHLIST", "APPROVED_FOR_TELEGRAM", "APPROVED_FOR_TRADE",
    "REJECTED", "OPENED", "MANAGED", "CLOSED", "ERROR",
}

VALID_REJECT_REASONS = {
    "low_volume", "bad_spread", "weak_trend", "weak_trigger", "bad_rr", "high_funding",
    "low_confidence", "bad_session", "ai_veto", "risk_guard_failed", "duplicate_signal",
    "correlation_risk", "pump_dump_risk",
    "max_portfolio_exposure",
}


def _id() -> str:
    return str(uuid.uuid4())


def save_market_snapshot(data: dict):
    defaults = {
        "id": _id(), "symbol": "", "timestamp": None, "price": 0, "volume_24h": 0,
        "price_change_24h": 0, "atr_percent": 0, "spread_percent": 0, "funding_rate": 0,
        "open_interest_change": 0, "tradeability_score": 0, "scanner_status": "",
        "scanner_reason": "",
    }
    with get_conn() as conn:
        conn.execute(
            """INSERT INTO market_snapshots (
               id, symbol, timestamp, price, volume_24h, price_change_24h, atr_percent,
               spread_percent, funding_rate, open_interest_change, tradeability_score,
               scanner_status, scanner_reason
            ) VALUES (
               :id, :symbol, :timestamp, :price, :volume_24h, :price_change_24h, :atr_percent,
               :spread_percent, :funding_rate, :open_interest_change, :tradeability_score,
               :scanner_status, :scanner_reason
            )""",
            {**defaults, **data},
        )


def save_scanned_coin(data: dict):
    defaults = {
        "id": _id(), "symbol": "", "timestamp": None, "scanner_status": "", "scanner_reason": "",
        "volume": 0, "volatility": 0, "spread": 0, "price_change": 0, "funding": 0, "open_interest": 0,
        "trend_cleanliness": 0, "tradeability_score": 0, "volume_score": 0, "volatility_score": 0,
        "spread_score": 0, "orderbook_depth_score": 0, "open_interest_score": 0, "funding_score": 0,
        "trend_cleanliness_score": 0, "pump_dump_penalty": 0, "correlation_penalty": 0,
    }
    with get_conn() as conn:
        conn.execute(
            """INSERT INTO scanned_coins (
               id, symbol, timestamp, scanner_status, scanner_reason, volume, volatility, spread,
               price_change, funding, open_interest, trend_cleanliness, tradeability_score,
               volume_score, volatility_score, spread_score, orderbook_depth_score, open_interest_score,
               funding_score, trend_cleanliness_score, pump_dump_penalty, correlation_penalty
            ) VALUES (
               :id, :symbol, :timestamp, :scanner_status, :scanner_reason, :volume, :volatility, :spread,
               :price_change, :funding, :open_interest, :trend_cleanliness, :tradeability_score,
               :volume_score, :volatility_score, :spread_score, :orderbook_depth_score, :open_interest_score,
               :funding_score, :trend_cleanliness_score, :pump_dump_penalty, :correlation_penalty
            )""",
            {**defaults, **data},
        )


def save_candidate_signal(data: dict):
    defaults = {
        "id": _id(), "signal_id": None, "symbol": "", "direction": None, "trend_score": 0,
        "trigger_score": 0, "risk_score": 0, "ai_score": 0, "final_score": 0, "quality": "D",
        "reason": "", "reject_reason": "", "ai_veto_reason": "", "risk_reject_reason": "",
        "lifecycle_stage": "SCANNED", "entry": None, "stop": None, "tp1": None, "tp2": None,
        "tp3": None, "rr": 0, "atr": 0, "stop_distance_percent": 0, "estimated_fee": 0,
        "estimated_slippage": 0, "net_rr": 0, "position_size": 0, "notional": 0,
        "leverage_suggestion": 1, "risk_amount": 0, "max_loss": 0, "execution_status": "candidate",
    }
    payload = {**defaults, **data}
    with get_conn() as conn:
        conn.execute(
            """INSERT INTO candidate_signals (
               id, signal_id, symbol, direction, trend_score, trigger_score, risk_score,
               ai_score, final_score, quality, reason, reject_reason, ai_veto_reason,
               risk_reject_reason, lifecycle_stage, entry, stop, tp1, tp2, tp3, rr, atr,
               stop_distance_percent, estimated_fee, estimated_slippage, net_rr, position_size,
               notional, leverage_suggestion, risk_amount, max_loss, execution_status
            ) VALUES (
               :id, :signal_id, :symbol, :direction, :trend_score, :trigger_score, :risk_score,
               :ai_score, :final_score, :quality, :reason, :reject_reason, :ai_veto_reason,
               :risk_reject_reason, :lifecycle_stage, :entry, :stop, :tp1, :tp2, :tp3, :rr, :atr,
               :stop_distance_percent, :estimated_fee, :estimated_slippage, :net_rr, :position_size,
               :notional, :leverage_suggestion, :risk_amount, :max_loss, :execution_status
            )""",
            payload,
        )
    return payload["id"]


def update_candidate_status(candidate_id: str, **updates):
    if not updates:
        return
    sets = ", ".join(f"{k}=?" for k in updates)
    values = list(updates.values()) + [candidate_id]
    with get_conn() as conn:
        conn.execute(f"UPDATE candidate_signals SET {sets} WHERE id=?", values)


def save_signal_event(signal_id: str, stage: str, symbol: str = "", reason: str = "", reject_reason: str = "", payload: dict | None = None):
    if stage not in VALID_SIGNAL_STAGES:
        stage = "ERROR"
    if reject_reason and reject_reason not in VALID_REJECT_REASONS:
        reject_reason = "risk_guard_failed"
    with get_conn() as conn:
        conn.execute(
            """INSERT INTO signal_events (id, signal_id, symbol, stage, reason, reject_reason, payload)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (_id(), signal_id, symbol, stage, reason, reject_reason, json.dumps(payload or {})),
        )


def save_trade_event(trade_id: int, signal_id: str, symbol: str, event_type: str, payload: dict | None = None):
    with get_conn() as conn:
        conn.execute(
            """INSERT INTO trade_events (id, trade_id, signal_id, symbol, event_type, event_payload)
               VALUES (?, ?, ?, ?, ?, ?)""",
            (_id(), trade_id, signal_id, symbol, event_type, json.dumps(payload or {})),
        )


def save_paper_result(data: dict) -> str:
    defaults = {
        "id": _id(), "signal_id": None, "candidate_id": None, "symbol": "", "direction": None,
        "tracked_from": "candidate", "hit_tp": 0, "hit_stop_first": 0, "time_to_move_minutes": 0,
        "max_favorable_excursion": 0, "max_adverse_excursion": 0, "setup_worked": 0, "would_have_won": 0,
        "status": "pending", "preview_entry": None, "preview_sl": None, "preview_tp1": None,
        "preview_tp2": None, "preview_tp3": None, "leverage_hint": 10, "finalized_at": None,
        "first_touch": "", "horizon_minutes": 480.0, "skip_decision_correct": 1,
        "final_score_snap": 0.0, "reject_reason_snap": "",
    }
    payload = {**defaults, **data}
    if payload["horizon_minutes"] is None:
        payload["horizon_minutes"] = 480.0
    with get_conn() as conn:
        conn.execute(
            """INSERT INTO paper_results (
               id, signal_id, candidate_id, symbol, direction, tracked_from,
               hit_tp, hit_stop_first, time_to_move_minutes,
               max_favorable_excursion, max_adverse_excursion, setup_worked, would_have_won,
               status, preview_entry, preview_sl, preview_tp1, preview_tp2, preview_tp3,
               leverage_hint, finalized_at, first_touch, horizon_minutes,
               skip_decision_correct, final_score_snap, reject_reason_snap
            ) VALUES (
               :id, :signal_id, :candidate_id, :symbol, :direction, :tracked_from,
               :hit_tp, :hit_stop_first, :time_to_move_minutes,
               :max_favorable_excursion, :max_adverse_excursion, :setup_worked, :would_have_won,
               :status, :preview_entry, :preview_sl, :preview_tp1, :preview_tp2, :preview_tp3,
               :leverage_hint, :finalized_at, :first_touch, :horizon_minutes,
               :skip_decision_correct, :final_score_snap, :reject_reason_snap
            )""",
            payload,
        )
    return payload["id"]


def get_pending_paper_results(limit: int = 40) -> list:
    with get_conn() as conn:
        rows = conn.execute(
            """SELECT * FROM paper_results
               WHERE (status IS NULL OR status='pending')
                 AND preview_entry IS NOT NULL AND preview_sl IS NOT NULL AND preview_tp1 IS NOT NULL
               ORDER BY created_at ASC LIMIT ?""",
            (limit,),
        ).fetchall()
        return [dict(r) for r in rows]


def update_paper_result(row_id: str, updates: dict):
    if not updates:
        return
    sets = ", ".join(f"{k}=?" for k in updates)
    vals = list(updates.values()) + [row_id]
    with get_conn() as conn:
        conn.execute(f"UPDATE paper_results SET {sets} WHERE id=?", vals)


def save_telegram_message(signal_id: str, symbol: str, dedupe_key: str, message_body: str, status: str = "queued") -> bool:
    try:
        with get_conn() as conn:
            conn.execute(
                """INSERT INTO telegram_messages (id, signal_id, symbol, dedupe_key, status, message_body)
                   VALUES (?, ?, ?, ?, ?, ?)""",
                (_id(), signal_id, symbol, dedupe_key, status, message_body),
            )
        return True
    except sqlite3.IntegrityError:
        return False


def mark_telegram_message_sent(dedupe_key: str) -> None:
    with get_conn() as conn:
        conn.execute(
            "UPDATE telegram_messages SET status='sent' WHERE dedupe_key=? AND status!='sent'",
            (dedupe_key,),
        )
