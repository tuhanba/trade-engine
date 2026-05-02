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

        -- ── scalp_signals — ana sinyal tablosu (candidate + executed) ─────────
        CREATE TABLE IF NOT EXISTS scalp_signals (
            id                  TEXT PRIMARY KEY,
            symbol              TEXT NOT NULL,
            timestamp           REAL,
            source              TEXT DEFAULT 'system',
            timeframe           TEXT DEFAULT '1m',
            direction           TEXT,

            -- Scores
            coin_score          REAL DEFAULT 0,
            trend_score         REAL DEFAULT 0,
            trigger_score       REAL DEFAULT 0,
            risk_score          REAL DEFAULT 0,
            ai_score            REAL DEFAULT 0,
            final_score         REAL DEFAULT 0,
            setup_quality       TEXT DEFAULT 'D',
            confidence          REAL DEFAULT 0,

            -- Risk & Entry
            entry_zone          REAL DEFAULT 0,
            stop_loss           REAL DEFAULT 0,
            tp1                 REAL DEFAULT 0,
            tp2                 REAL DEFAULT 0,
            tp3                 REAL DEFAULT 0,
            rr                  REAL DEFAULT 0,
            net_rr              REAL DEFAULT 0,
            risk_percent        REAL DEFAULT 0,
            risk_amount         REAL DEFAULT 0,
            position_size       REAL DEFAULT 0,
            notional_size       REAL DEFAULT 0,
            leverage_suggestion INTEGER DEFAULT 1,
            max_loss            REAL DEFAULT 0,
            estimated_fee       REAL DEFAULT 0,
            estimated_slippage  REAL DEFAULT 0,
            invalidation_level  REAL DEFAULT 0,

            -- Status & lifecycle stage
            status              TEXT DEFAULT 'SCANNED',
            lifecycle_stage     TEXT DEFAULT 'SCANNED',
            reason              TEXT DEFAULT '',
            reject_reason       TEXT DEFAULT '',
            telegram_status     TEXT DEFAULT 'pending',
            dashboard_status    TEXT DEFAULT 'pending',
            error               TEXT DEFAULT '',

            -- Tracking: candidate vs trade
            approved_for_watchlist  INTEGER DEFAULT 0,
            approved_for_telegram   INTEGER DEFAULT 0,
            approved_for_trade      INTEGER DEFAULT 0,
            linked_trade_id         INTEGER,

            -- Paper outcome tracking (for rejected candidates too)
            outcome_checked     INTEGER DEFAULT 0,
            outcome_tp1_reached INTEGER DEFAULT 0,
            outcome_tp2_reached INTEGER DEFAULT 0,
            outcome_sl_hit      INTEGER DEFAULT 0,
            outcome_max_favorable_r REAL DEFAULT 0,
            outcome_max_adverse_r   REAL DEFAULT 0,
            outcome_minutes_to_move INTEGER DEFAULT 0,
            outcome_would_win   INTEGER DEFAULT 0,

            created_at          TEXT DEFAULT (datetime('now')),
            archived_at         TEXT
        );
        CREATE INDEX IF NOT EXISTS idx_scalp_signals_symbol ON scalp_signals(symbol);
        CREATE INDEX IF NOT EXISTS idx_scalp_signals_status ON scalp_signals(lifecycle_stage);
        CREATE INDEX IF NOT EXISTS idx_scalp_signals_created ON scalp_signals(created_at);

        -- ── scanned_coins — scanner geçen/elenip neden elendiği ─────────────
        CREATE TABLE IF NOT EXISTS scanned_coins (
            id                    INTEGER PRIMARY KEY AUTOINCREMENT,
            symbol                TEXT NOT NULL,
            price                 REAL,
            volume_24h            REAL,
            price_change_24h      REAL,
            atr_percent           REAL,
            spread_percent        REAL,
            funding_rate          REAL,
            open_interest_change  REAL,
            volume_score          REAL DEFAULT 0,
            volatility_score      REAL DEFAULT 0,
            spread_score          REAL DEFAULT 0,
            orderbook_depth_score REAL DEFAULT 0,
            oi_score              REAL DEFAULT 0,
            funding_score         REAL DEFAULT 0,
            trend_cleanliness_score REAL DEFAULT 0,
            pump_dump_penalty     REAL DEFAULT 0,
            tradeability_score    REAL DEFAULT 0,
            scanner_status        TEXT DEFAULT 'WATCH',
            scanner_reason        TEXT,
            created_at            TEXT DEFAULT (datetime('now'))
        );
        CREATE INDEX IF NOT EXISTS idx_scanned_coins_sym ON scanned_coins(symbol, created_at);

        -- ── paper_results — watchlist kandidat sinyallerin paper sonuçları ──
        CREATE TABLE IF NOT EXISTS paper_results (
            id                  INTEGER PRIMARY KEY AUTOINCREMENT,
            signal_id           TEXT,
            trade_id            INTEGER,
            symbol              TEXT NOT NULL,
            direction           TEXT,
            entry               REAL,
            stop                REAL,
            tp1                 REAL,
            tp2                 REAL,
            final_score         REAL DEFAULT 0,
            quality             TEXT,
            environment         TEXT DEFAULT 'paper',
            open_time           TEXT,
            close_time          TEXT,
            close_price         REAL,
            close_reason        TEXT,
            gross_pnl           REAL DEFAULT 0,
            fee_paid            REAL DEFAULT 0,
            slippage_cost       REAL DEFAULT 0,
            net_pnl             REAL DEFAULT 0,
            r_multiple          REAL DEFAULT 0,
            hold_minutes        REAL DEFAULT 0,
            tp1_hit             INTEGER DEFAULT 0,
            tp2_hit             INTEGER DEFAULT 0,
            max_favorable_excursion REAL DEFAULT 0,
            max_adverse_excursion   REAL DEFAULT 0,
            is_candidate_track  INTEGER DEFAULT 0,
            created_at          TEXT DEFAULT (datetime('now'))
        );

        -- ── adaptive_stats — öğrenme istatistikleri ─────────────────────────
        CREATE TABLE IF NOT EXISTS adaptive_stats (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            stat_type       TEXT NOT NULL,
            dimension       TEXT,
            dimension_value TEXT,
            total_count     INTEGER DEFAULT 0,
            win_count       INTEGER DEFAULT 0,
            loss_count       INTEGER DEFAULT 0,
            total_pnl       REAL DEFAULT 0,
            avg_r           REAL DEFAULT 0,
            win_rate        REAL DEFAULT 0,
            profit_factor   REAL DEFAULT 0,
            updated_at      TEXT DEFAULT (datetime('now')),
            UNIQUE(stat_type, dimension, dimension_value)
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

        -- ── signal_events — sinyal lifecycle ────────────────────────────────
        CREATE TABLE IF NOT EXISTS signal_events (
            id         INTEGER PRIMARY KEY AUTOINCREMENT,
            signal_id  TEXT NOT NULL,
            stage      TEXT NOT NULL,
            detail     TEXT,
            created_at TEXT DEFAULT (datetime('now'))
        );
        CREATE INDEX IF NOT EXISTS idx_signal_events_signal ON signal_events(signal_id);

        -- ── trade_events — trade durum geçişleri ────────────────────────────
        CREATE TABLE IF NOT EXISTS trade_events (
            id         INTEGER PRIMARY KEY AUTOINCREMENT,
            trade_id   INTEGER NOT NULL,
            event      TEXT NOT NULL,
            price      REAL,
            pnl        REAL,
            detail     TEXT,
            created_at TEXT DEFAULT (datetime('now'))
        );
        CREATE INDEX IF NOT EXISTS idx_trade_events_trade ON trade_events(trade_id);

        -- ── telegram_messages — DB-based duplicate engeli ───────────────────
        CREATE TABLE IF NOT EXISTS telegram_messages (
            id         INTEGER PRIMARY KEY AUTOINCREMENT,
            signal_id  TEXT UNIQUE NOT NULL,
            symbol     TEXT,
            direction  TEXT,
            quality    TEXT,
            sent_at    TEXT DEFAULT (datetime('now'))
        );

        """)

    # ── Eski şemadan yeni kolonlara migration ─────────────────────────────────
    _migrate(get_conn())
    logger.info("DB init tamamlandı.")


def _migrate(conn):
    """Eksik kolonları ekle — her çalıştırmada idempotent."""
    migrations = [
        # trades
        ("trades", "qty_tp1",            "REAL"),
        ("trades", "qty_tp2",            "REAL"),
        ("trades", "qty_runner",         "REAL"),
        ("trades", "trail_stop",         "REAL"),
        ("trades", "realized_pnl",       "REAL DEFAULT 0"),
        ("trades", "r_multiple",         "REAL DEFAULT 0"),
        ("trades", "tp1_hit",            "INTEGER DEFAULT 0"),
        ("trades", "tp2_hit",            "INTEGER DEFAULT 0"),
        ("trades", "linked_candidate_id","INTEGER"),
        ("trades", "hold_minutes",       "REAL DEFAULT 0"),
        ("trades", "ax_mode",            "TEXT DEFAULT 'execute'"),
        ("trades", "environment",        "TEXT DEFAULT 'paper'"),
        ("trades", "close_reason",       "TEXT"),
        ("trades", "fee_paid",           "REAL DEFAULT 0"),
        ("trades", "slippage_cost",      "REAL DEFAULT 0"),
        ("trades", "risk_percent",       "REAL DEFAULT 0"),
        ("trades", "risk_amount",        "REAL DEFAULT 0"),
        ("trades", "notional",           "REAL DEFAULT 0"),
        ("trades", "leverage",           "INTEGER DEFAULT 1"),
        ("trades", "session",            "TEXT"),
        # scalp_signals — yeni kolonlar (eski DB'ler için)
        ("scalp_signals", "ai_score",              "REAL DEFAULT 0"),
        ("scalp_signals", "net_rr",                "REAL DEFAULT 0"),
        ("scalp_signals", "risk_amount",           "REAL DEFAULT 0"),
        ("scalp_signals", "estimated_fee",         "REAL DEFAULT 0"),
        ("scalp_signals", "estimated_slippage",    "REAL DEFAULT 0"),
        ("scalp_signals", "lifecycle_stage",       "TEXT DEFAULT 'SCANNED'"),
        ("scalp_signals", "reject_reason",         "TEXT DEFAULT ''"),
        ("scalp_signals", "approved_for_watchlist","INTEGER DEFAULT 0"),
        ("scalp_signals", "approved_for_telegram", "INTEGER DEFAULT 0"),
        ("scalp_signals", "approved_for_trade",    "INTEGER DEFAULT 0"),
        ("scalp_signals", "linked_trade_id",       "INTEGER"),
        ("scalp_signals", "outcome_checked",       "INTEGER DEFAULT 0"),
        ("scalp_signals", "outcome_tp1_reached",   "INTEGER DEFAULT 0"),
        ("scalp_signals", "outcome_tp2_reached",   "INTEGER DEFAULT 0"),
        ("scalp_signals", "outcome_sl_hit",        "INTEGER DEFAULT 0"),
        ("scalp_signals", "outcome_max_favorable_r","REAL DEFAULT 0"),
        ("scalp_signals", "outcome_max_adverse_r", "REAL DEFAULT 0"),
        ("scalp_signals", "outcome_minutes_to_move","INTEGER DEFAULT 0"),
        ("scalp_signals", "outcome_would_win",     "INTEGER DEFAULT 0"),
        # coin_params
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
        # paper_account
        ("paper_account", "balance",         "REAL DEFAULT 250.0"),
        ("paper_account", "initial_balance", "REAL DEFAULT 250.0"),
    ]
    for table, col, col_type in migrations:
        try:
            conn.execute(f"ALTER TABLE {table} ADD COLUMN {col} {col_type}")
        except Exception:
            pass

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
             linked_candidate_id, open_time)
        VALUES
            (:symbol, :direction, :status, :environment, :ax_mode,
             :entry, :sl, :tp1, :tp2, :trail_stop,
             :qty, :qty_tp1, :qty_tp2, :qty_runner,
             :linked_candidate_id, :open_time)
    """
    defaults = {
        "status": "open", "environment": "paper", "ax_mode": "execute",
        "trail_stop": None, "qty_tp1": None, "qty_tp2": None, "qty_runner": None,
        "linked_candidate_id": None,
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
    with get_conn() as conn:
        conn.execute(
            """UPDATE trades SET
                status='closed', close_price=?, net_pnl=?, close_reason=?,
                hold_minutes=?, close_time=?
               WHERE id=?""",
            (close_price, net_pnl, reason, hold_minutes, now, trade_id)
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
    KNOWN_COLS = {
        "id", "symbol", "timestamp", "source", "timeframe", "direction",
        "coin_score", "trend_score", "trigger_score", "risk_score", "ai_score",
        "final_score", "setup_quality", "confidence",
        "entry_zone", "stop_loss", "tp1", "tp2", "tp3",
        "rr", "net_rr", "risk_percent", "risk_amount",
        "position_size", "notional_size", "leverage_suggestion",
        "max_loss", "estimated_fee", "estimated_slippage", "invalidation_level",
        "lifecycle_stage", "status", "reason", "reject_reason",
        "telegram_status", "dashboard_status", "error",
        "approved_for_watchlist", "approved_for_telegram", "approved_for_trade",
        "linked_trade_id",
        "outcome_tp1_reached", "outcome_tp2_reached", "outcome_sl_hit",
        "outcome_max_favorable_r", "outcome_max_adverse_r",
        "outcome_minutes_to_move", "outcome_would_win",
    }
    row = {k: v for k, v in sig_data.items() if k in KNOWN_COLS}
    cols = ", ".join(row.keys())
    placeholders = ", ".join(f":{k}" for k in row.keys())
    with get_conn() as conn:
        conn.execute(
            f"INSERT OR REPLACE INTO scalp_signals ({cols}) VALUES ({placeholders})",
            row,
        )

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


# ─────────────────────────────────────────────────────────────────────────────
# SIGNAL EVENTS — Lifecycle tracking
# SCANNED → TREND_OK → TRIGGER_OK → RISK_OK → AI_ALLOWED/AI_VETOED
# → SENT_TELEGRAM → OPENED → MANAGED → CLOSED/ERROR
# ─────────────────────────────────────────────────────────────────────────────

def log_signal_event(signal_id: str, stage: str, detail: str = None):
    """Sinyal lifecycle geçişini DB'ye yaz."""
    try:
        with get_conn() as conn:
            conn.execute(
                "INSERT INTO signal_events (signal_id, stage, detail) VALUES (?, ?, ?)",
                (signal_id, stage, detail)
            )
    except Exception as e:
        logger.error(f"signal_event yazma hatası {signal_id} {stage}: {e}")

def get_signal_lifecycle(signal_id: str) -> list:
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT stage, detail, created_at FROM signal_events WHERE signal_id=? ORDER BY id",
            (signal_id,)
        ).fetchall()
    return [dict(r) for r in rows]

def get_signal_funnel_today() -> dict:
    """Bugünkü sinyal hunisi — kaç sinyal hangi aşamada elenmiş."""
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    with get_conn() as conn:
        rows = conn.execute(
            """SELECT stage, COUNT(*) as cnt
               FROM signal_events
               WHERE DATE(created_at)=?
               GROUP BY stage""",
            (today,)
        ).fetchall()
    return {r["stage"]: r["cnt"] for r in rows}


# ─────────────────────────────────────────────────────────────────────────────
# TRADE EVENTS — Trade durum geçişleri
# ─────────────────────────────────────────────────────────────────────────────

def log_trade_event(trade_id: int, event: str, price: float = None,
                    pnl: float = None, detail: str = None):
    try:
        with get_conn() as conn:
            conn.execute(
                "INSERT INTO trade_events (trade_id, event, price, pnl, detail) VALUES (?, ?, ?, ?, ?)",
                (trade_id, event, price, pnl, detail)
            )
    except Exception as e:
        logger.error(f"trade_event yazma hatası {trade_id} {event}: {e}")

def get_trade_events(trade_id: int) -> list:
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT event, price, pnl, detail, created_at FROM trade_events WHERE trade_id=? ORDER BY id",
            (trade_id,)
        ).fetchall()
    return [dict(r) for r in rows]


# ─────────────────────────────────────────────────────────────────────────────
# TELEGRAM MESSAGES — DB-based duplicate engeli
# ─────────────────────────────────────────────────────────────────────────────

def is_telegram_sent(signal_id: str) -> bool:
    with get_conn() as conn:
        row = conn.execute(
            "SELECT id FROM telegram_messages WHERE signal_id=?", (signal_id,)
        ).fetchone()
    return row is not None

def mark_telegram_sent(signal_id: str, symbol: str, direction: str, quality: str):
    try:
        with get_conn() as conn:
            conn.execute(
                """INSERT OR IGNORE INTO telegram_messages
                   (signal_id, symbol, direction, quality) VALUES (?, ?, ?, ?)""",
                (signal_id, symbol, direction, quality)
            )
    except Exception as e:
        logger.error(f"telegram_messages yazma hatası: {e}")

def get_telegram_stats_today() -> dict:
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT quality, COUNT(*) as cnt FROM telegram_messages WHERE DATE(sent_at)=? GROUP BY quality",
            (today,)
        ).fetchall()
    stats = {"S": 0, "A+": 0, "A": 0, "B": 0, "total": 0}
    for r in rows:
        q = r["quality"]
        if q in stats:
            stats[q] = r["cnt"]
        stats["total"] += r["cnt"]
    return stats


# ─────────────────────────────────────────────────────────────────────────────
# COIN PROFILES — database.py'de coin_profile tablosuna alias
# ─────────────────────────────────────────────────────────────────────────────

def get_coin_profiles(symbols: list = None) -> list:
    with get_conn() as conn:
        if symbols:
            placeholders = ",".join("?" * len(symbols))
            rows = conn.execute(
                f"SELECT * FROM coin_profile WHERE symbol IN ({placeholders})", symbols
            ).fetchall()
        else:
            rows = conn.execute("SELECT * FROM coin_profile ORDER BY danger_score DESC").fetchall()
    return [dict(r) for r in rows]


# ─────────────────────────────────────────────────────────────────────────────
# SCANNED COINS — Scanner verileri
# ─────────────────────────────────────────────────────────────────────────────

def save_scanned_coin(data: dict):
    """Tarama verisini kaydet. data dict alanları scanned_coins kolonlarına uygun olmalı."""
    cols = [
        "symbol", "price", "volume_24h", "price_change_24h", "atr_percent",
        "spread_percent", "funding_rate", "open_interest_change",
        "volume_score", "volatility_score", "spread_score", "orderbook_depth_score",
        "oi_score", "funding_score", "trend_cleanliness_score", "pump_dump_penalty",
        "tradeability_score", "scanner_status", "scanner_reason",
    ]
    row = {c: data.get(c) for c in cols if c in data or c == "symbol"}
    placeholders = ", ".join(f":{c}" for c in row)
    col_list = ", ".join(row.keys())
    try:
        with get_conn() as conn:
            conn.execute(
                f"INSERT INTO scanned_coins ({col_list}) VALUES ({placeholders})", row
            )
    except Exception as e:
        logger.error(f"scanned_coins yazma hatası {data.get('symbol')}: {e}")

def get_scanned_coins_today(limit: int = 500) -> list:
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    with get_conn() as conn:
        rows = conn.execute(
            """SELECT * FROM scanned_coins
               WHERE DATE(created_at)=? ORDER BY tradeability_score DESC LIMIT ?""",
            (today, limit)
        ).fetchall()
    return [dict(r) for r in rows]

def get_scanner_stats_today() -> dict:
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    with get_conn() as conn:
        row = conn.execute(
            """SELECT
               COUNT(*) as total,
               SUM(CASE WHEN scanner_status='ELIGIBLE' THEN 1 ELSE 0 END) as eligible,
               SUM(CASE WHEN scanner_status='WATCH' THEN 1 ELSE 0 END) as watch,
               SUM(CASE WHEN scanner_status='AVOID' THEN 1 ELSE 0 END) as avoid,
               AVG(tradeability_score) as avg_score
               FROM scanned_coins WHERE DATE(created_at)=?""",
            (today,)
        ).fetchone()
    if row:
        return {
            "total": row[0] or 0, "eligible": row[1] or 0,
            "watch": row[2] or 0, "avoid": row[3] or 0,
            "avg_score": round(row[4] or 0, 2),
        }
    return {"total": 0, "eligible": 0, "watch": 0, "avoid": 0, "avg_score": 0}


# ─────────────────────────────────────────────────────────────────────────────
# PAPER RESULTS — Paper/candidate outcome tracking
# ─────────────────────────────────────────────────────────────────────────────

def save_paper_result(data: dict) -> int:
    cols = [
        "signal_id", "trade_id", "symbol", "direction", "entry", "stop",
        "tp1", "tp2", "final_score", "quality", "environment",
        "open_time", "close_time", "close_price", "close_reason",
        "gross_pnl", "fee_paid", "slippage_cost", "net_pnl", "r_multiple",
        "hold_minutes", "tp1_hit", "tp2_hit",
        "max_favorable_excursion", "max_adverse_excursion", "is_candidate_track",
    ]
    row = {c: data.get(c) for c in cols}
    placeholders = ", ".join(f":{c}" for c in row)
    col_list = ", ".join(row.keys())
    with get_conn() as conn:
        cur = conn.execute(
            f"INSERT INTO paper_results ({col_list}) VALUES ({placeholders})", row
        )
        return cur.lastrowid

def update_paper_result(result_id: int, fields: dict):
    if not fields:
        return
    sets = ", ".join(f"{k}=?" for k in fields)
    vals = list(fields.values()) + [result_id]
    with get_conn() as conn:
        conn.execute(f"UPDATE paper_results SET {sets} WHERE id=?", vals)

def get_open_paper_results() -> list:
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT * FROM paper_results WHERE close_time IS NULL ORDER BY open_time"
        ).fetchall()
    return [dict(r) for r in rows]

def get_paper_stats(days: int = 30) -> dict:
    cutoff = (datetime.utcnow() - timedelta(days=days)).strftime("%Y-%m-%dT%H:%M:%S")
    with get_conn() as conn:
        rows = conn.execute(
            """SELECT net_pnl, r_multiple, is_candidate_track, tp1_hit, tp2_hit
               FROM paper_results WHERE close_time IS NOT NULL AND open_time >= ?""",
            (cutoff,)
        ).fetchall()
    if not rows:
        return {"total": 0, "wins": 0, "win_rate": 0, "avg_r": 0, "total_pnl": 0}
    rows = [dict(r) for r in rows]
    wins = [r for r in rows if (r.get("net_pnl") or 0) > 0]
    return {
        "total":    len(rows),
        "wins":     len(wins),
        "win_rate": round(len(wins) / len(rows), 4) if rows else 0,
        "avg_r":    round(sum(r.get("r_multiple") or 0 for r in rows) / len(rows), 4),
        "total_pnl": round(sum(r.get("net_pnl") or 0 for r in rows), 4),
        "candidates": sum(1 for r in rows if r.get("is_candidate_track")),
    }


# ─────────────────────────────────────────────────────────────────────────────
# ADAPTIVE STATS — Coin/saat/setup bazlı öğrenme istatistikleri
# ─────────────────────────────────────────────────────────────────────────────

def upsert_adaptive_stat(stat_type: str, dimension: str, dimension_value: str,
                          is_win: bool, pnl: float, r_multiple: float):
    """Bir istatistiği güncelle (coin bazlı, saat bazlı, setup bazlı vb.)"""
    try:
        with get_conn() as conn:
            existing = conn.execute(
                "SELECT id, total_count, win_count, loss_count, total_pnl FROM adaptive_stats "
                "WHERE stat_type=? AND dimension=? AND dimension_value=?",
                (stat_type, dimension, dimension_value)
            ).fetchone()
            if existing:
                total = existing["total_count"] + 1
                wins  = existing["win_count"] + (1 if is_win else 0)
                losses = existing["loss_count"] + (0 if is_win else 1)
                tot_pnl = (existing["total_pnl"] or 0) + pnl
                conn.execute(
                    """UPDATE adaptive_stats SET
                       total_count=?, win_count=?, loss_count=?, total_pnl=?,
                       win_rate=?, avg_r=?, updated_at=datetime('now')
                       WHERE id=?""",
                    (total, wins, losses, tot_pnl,
                     round(wins / total, 4),
                     round(tot_pnl / total, 4),
                     existing["id"])
                )
            else:
                wins = 1 if is_win else 0
                conn.execute(
                    """INSERT INTO adaptive_stats
                       (stat_type, dimension, dimension_value, total_count, win_count, loss_count,
                        total_pnl, win_rate, avg_r)
                       VALUES (?,?,?,1,?,?,?,?,?)""",
                    (stat_type, dimension, dimension_value, wins, 1-wins, pnl,
                     float(is_win), round(pnl, 4))
                )
    except Exception as e:
        logger.error(f"adaptive_stat upsert hatası: {e}")

def get_adaptive_stats(stat_type: str, dimension: str, min_count: int = 5) -> list:
    with get_conn() as conn:
        rows = conn.execute(
            """SELECT * FROM adaptive_stats
               WHERE stat_type=? AND dimension=? AND total_count >= ?
               ORDER BY win_rate DESC""",
            (stat_type, dimension, min_count)
        ).fetchall()
    return [dict(r) for r in rows]


# ─────────────────────────────────────────────────────────────────────────────
# SIGNAL FUNNEL — Dashboard için sinyal hunisi
# ─────────────────────────────────────────────────────────────────────────────

def get_signal_funnel_today() -> dict:
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    with get_conn() as conn:
        # signal_events tablosundan lifecycle stage sayıları
        rows = conn.execute(
            """SELECT stage, COUNT(DISTINCT signal_id) as cnt
               FROM signal_events WHERE DATE(created_at)=? GROUP BY stage""",
            (today,)
        ).fetchall()
        funnel_events = {r["stage"]: r["cnt"] for r in rows}

        # scalp_signals tablosundan doğrudan sayılar
        row = conn.execute(
            """SELECT
               COUNT(*) as total_candidates,
               SUM(approved_for_watchlist) as watchlist,
               SUM(approved_for_telegram) as telegram,
               SUM(approved_for_trade) as trade,
               SUM(CASE WHEN lifecycle_stage='REJECTED' THEN 1 ELSE 0 END) as rejected
               FROM scalp_signals WHERE DATE(created_at)=?""",
            (today,)
        ).fetchone()

    funnel = {
        "scanned":    get_scanner_stats_today().get("total", 0),
        "candidates": row[0] or 0 if row else 0,
        "watchlist":  row[1] or 0 if row else 0,
        "telegram":   row[2] or 0 if row else 0,
        "trade":      row[3] or 0 if row else 0,
        "rejected":   row[4] or 0 if row else 0,
    }
    funnel.update(funnel_events)
    return funnel

def get_candidate_signals(limit: int = 100, stage: str = None,
                          approved_only: bool = False) -> list:
    sql = "SELECT * FROM scalp_signals WHERE direction IS NOT NULL AND entry_zone > 0"
    params = []
    if stage:
        sql += " AND lifecycle_stage=?"
        params.append(stage)
    if approved_only:
        sql += " AND approved_for_watchlist=1"
    sql += " ORDER BY final_score DESC LIMIT ?"
    params.append(limit)
    with get_conn() as conn:
        rows = conn.execute(sql, params).fetchall()
    return [dict(r) for r in rows]

def update_signal_lifecycle(signal_id: str, stage: str, fields: dict = None):
    """Sinyal lifecycle stage'ini güncelle ve event logla."""
    try:
        updates = {"lifecycle_stage": stage}
        if fields:
            updates.update(fields)
        sets = ", ".join(f"{k}=?" for k in updates)
        vals = list(updates.values()) + [signal_id]
        with get_conn() as conn:
            conn.execute(f"UPDATE scalp_signals SET {sets} WHERE id=?", vals)
        log_signal_event(signal_id, stage, fields.get("reject_reason") if fields else None)
    except Exception as e:
        logger.error(f"lifecycle güncelleme hatası {signal_id}: {e}")

def update_signal_outcome(signal_id: str, outcome: dict):
    """Elenen/watchlist sinyallerin sonucunu güncelle."""
    fields = {
        "outcome_checked": 1,
        **{k: v for k, v in outcome.items() if k.startswith("outcome_")}
    }
    sets = ", ".join(f"{k}=?" for k in fields)
    vals = list(fields.values()) + [signal_id]
    with get_conn() as conn:
        conn.execute(f"UPDATE scalp_signals SET {sets} WHERE id=?", vals)

def get_missed_opportunities(days: int = 7) -> list:
    """Reddedilmiş ama aslında kazandıracak sinyaller."""
    cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
    with get_conn() as conn:
        rows = conn.execute(
            """SELECT symbol, direction, final_score, setup_quality, reject_reason,
               outcome_tp1_reached, outcome_tp2_reached, outcome_max_favorable_r,
               outcome_would_win
               FROM scalp_signals
               WHERE lifecycle_stage='REJECTED' AND outcome_checked=1
               AND outcome_would_win=1 AND created_at >= ?
               ORDER BY outcome_max_favorable_r DESC LIMIT 50""",
            (cutoff,)
        ).fetchall()
    return [dict(r) for r in rows]
