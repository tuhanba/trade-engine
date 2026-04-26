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
    conn = sqlite3.connect(DB_PATH, timeout=30)
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
            expected_mae_r   REAL,
            score            REAL DEFAULT 0,
            confidence       REAL DEFAULT 0,
            decision         TEXT DEFAULT 'PENDING',
            veto_reason      TEXT,
            session          TEXT,
            market_regime    TEXT,
            ax_mode          TEXT DEFAULT 'execute',
            execution_mode   TEXT DEFAULT 'paper',
            linked_trade_id  INTEGER,
            created_at       TEXT DEFAULT (datetime('now')),
            outcome_checked  INTEGER DEFAULT 0,
            pseudo_mfe_r     REAL,
            pseudo_mae_r     REAL
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
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            data        TEXT NOT NULL,
            win_rate    REAL,
            avg_r       REAL,
            pnl         REAL,
            trade_count INTEGER,
            created_at  TEXT DEFAULT (datetime('now'))
        );

        -- ── system_state ─────────────────────────────────────────────────────
        CREATE TABLE IF NOT EXISTS system_state (
            key        TEXT PRIMARY KEY,
            value      TEXT,
            updated_at TEXT DEFAULT (datetime('now'))
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

        -- ── coin_params ──────────────────────────────────────────────────────
        CREATE TABLE IF NOT EXISTS coin_params (
            symbol             TEXT PRIMARY KEY,
            volatility_profile TEXT DEFAULT 'normal',
            sl_atr_mult        REAL DEFAULT 1.3,
            tp_atr_mult        REAL DEFAULT 2.0,
            risk_pct           REAL DEFAULT 1.0,
            max_leverage       INTEGER DEFAULT 15,
            min_adx            REAL DEFAULT 20,
            min_bb_width       REAL DEFAULT 1.3,
            min_volume_m       REAL DEFAULT 10.0,
            enabled            INTEGER DEFAULT 1,
            updated_at         TEXT DEFAULT (datetime('now'))
        );

        -- ── pipeline_stats ───────────────────────────────────────────────────
        CREATE TABLE IF NOT EXISTS pipeline_stats (
            id                   INTEGER PRIMARY KEY AUTOINCREMENT,
            scan_time            TEXT DEFAULT (datetime('now')),
            scanned_symbols      INTEGER DEFAULT 0,
            passed_market_filter INTEGER DEFAULT 0,
            candidates_created   INTEGER DEFAULT 0,
            ax_allow             INTEGER DEFAULT 0,
            ax_veto              INTEGER DEFAULT 0,
            ax_watch             INTEGER DEFAULT 0,
            risk_rejected        INTEGER DEFAULT 0,
            paper_trades_opened  INTEGER DEFAULT 0,
            last_error           TEXT
        );

        """)

    # ── Eski şemadan yeni kolonlara migration ─────────────────────────────────
    with get_conn() as _mc:
        _migrate(_mc)
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
        ("coin_params", "min_volume_m",       "REAL DEFAULT 10.0"),
        ("coin_params", "enabled",            "INTEGER DEFAULT 1"),
        ("coin_params", "updated_at",         "TEXT DEFAULT (datetime('now'))"),
        # paper_account — eski şemada 'paper_balance', yeni şemada 'balance'
        ("paper_account", "balance",         "REAL DEFAULT 250.0"),
        ("paper_account", "initial_balance", "REAL DEFAULT 250.0"),
        # signal_candidates — yeni alanlar
        ("signal_candidates", "expected_mae_r",  "REAL"),
        ("signal_candidates", "outcome_checked", "INTEGER DEFAULT 0"),
        ("signal_candidates", "pseudo_mfe_r",    "REAL"),
        ("signal_candidates", "pseudo_mae_r",    "REAL"),

        # ── ai_brain uyumluluk — trade_postmortem eski şema sütunları ────────
        # ai_brain.py kendi INSERT formatını kullanıyor, her iki şema aynı
        # tabloda yaşamalı. Bu sütunlar ai_brain tarafından dolduruluyor.
        ("trade_postmortem", "entry",       "REAL"),
        ("trade_postmortem", "exit_price",  "REAL"),
        ("trade_postmortem", "sl",          "REAL"),
        ("trade_postmortem", "tp",          "REAL"),
        ("trade_postmortem", "mfe",         "REAL"),
        ("trade_postmortem", "mae",         "REAL"),
        ("trade_postmortem", "opt_tp",      "REAL"),
        ("trade_postmortem", "actual_pnl",  "REAL"),

        # ── ai_brain uyumluluk — daily_summary eski şema sütunları ──────────
        ("daily_summary", "total",       "INTEGER DEFAULT 0"),
        ("daily_summary", "wins",        "INTEGER DEFAULT 0"),
        ("daily_summary", "losses",      "INTEGER DEFAULT 0"),
        ("daily_summary", "pnl",         "REAL DEFAULT 0"),
        ("daily_summary", "best_coin",   "TEXT"),
        ("daily_summary", "worst_coin",  "TEXT"),
        ("daily_summary", "sent",        "INTEGER DEFAULT 0"),

        # ── ai_brain uyumluluk — coin_profile eski şema sütunları ────────────
        ("coin_profile", "avg_rr",           "REAL DEFAULT 0"),
        ("coin_profile", "avg_efficiency",   "REAL DEFAULT 0"),
        ("coin_profile", "avg_hold_min",     "REAL DEFAULT 0"),
        ("coin_profile", "best_rsi_min",     "REAL DEFAULT 30"),
        ("coin_profile", "best_rsi_max",     "REAL DEFAULT 70"),
        ("coin_profile", "best_rv_min",      "REAL DEFAULT 1.2"),
        ("coin_profile", "sl_tight_rate",    "REAL DEFAULT 0"),
        ("coin_profile", "long_wr",          "REAL DEFAULT 0"),
        ("coin_profile", "short_wr",         "REAL DEFAULT 0"),
        ("coin_profile", "last_updated",     "TEXT"),

        # ── ai_brain uyumluluk — coin_cooldown eski şema sütunları ───────────
        ("coin_cooldown", "blacklisted_until", "TEXT"),
        ("coin_cooldown", "consec_losses",     "INTEGER DEFAULT 0"),

        # ── pipeline_stats — eski tablo eksik kolonlar ───────────────────────
        ("pipeline_stats", "risk_rejected",       "INTEGER DEFAULT 0"),
        ("pipeline_stats", "paper_trades_opened", "INTEGER DEFAULT 0"),
        ("pipeline_stats", "last_error",          "TEXT"),

        # ── ai_logs — eski şema sadece analytics kolonları taşıyor ───────────
        # Yeni kod event/symbol/decision/score/confidence/reason/data yazıyor
        ("ai_logs", "event",            "TEXT"),
        ("ai_logs", "symbol",           "TEXT"),
        ("ai_logs", "decision",         "TEXT"),
        ("ai_logs", "score",            "REAL"),
        ("ai_logs", "confidence",       "REAL"),
        ("ai_logs", "reason",           "TEXT"),
        ("ai_logs", "data",             "TEXT"),
        # Eski ai_brain analytics kolonları (zaten var ama idempotent)
        ("ai_logs", "trades_analyzed",  "INTEGER DEFAULT 0"),
        ("ai_logs", "win_rate",         "REAL DEFAULT 0"),
        ("ai_logs", "avg_rr",           "REAL DEFAULT 0"),
        ("ai_logs", "insight",          "TEXT"),
        ("ai_logs", "changes",          "TEXT"),
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

def get_stats(hours: int = 48) -> dict:
    cutoff = (datetime.now(timezone.utc) - timedelta(hours=hours)).isoformat()
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT * FROM trades WHERE status='closed' AND close_time >= ?", (cutoff,)
        ).fetchall()

    trades = [dict(r) for r in rows]
    if not trades:
        return {"total": 0, "wins": 0, "losses": 0, "win_rate": 0,
                "total_pnl": 0, "avg_win": 0, "avg_loss": 0,
                "avg_r": 0, "profit_factor": 0, "max_drawdown": 0}

    wins   = [t for t in trades if t["net_pnl"] > 0]
    losses = [t for t in trades if t["net_pnl"] <= 0]
    pnls   = [t["net_pnl"] for t in trades]

    gross_win  = sum(t["net_pnl"] for t in wins)
    gross_loss = abs(sum(t["net_pnl"] for t in losses))

    cum, peak, max_dd = 0, 0, 0
    for p in reversed(pnls):
        cum += p
        peak = max(peak, cum)
        max_dd = max(max_dd, peak - cum)

    return {
        "total":         len(trades),
        "wins":          len(wins),
        "losses":        len(losses),
        "win_rate":      len(wins) / len(trades),
        "total_pnl":     sum(pnls),
        "avg_win":       gross_win  / len(wins)   if wins   else 0,
        "avg_loss":      gross_loss / len(losses) if losses else 0,
        "avg_r":         sum((t.get("r_multiple") or 0) for t in trades) / len(trades),
        "profit_factor": gross_win / gross_loss if gross_loss > 0 else 0,
        "max_drawdown":  max_dd,
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
# PIPELINE STATS
# ─────────────────────────────────────────────────────────────────────────────

def save_pipeline_stats(stats: dict):
    """Bir tarama döngüsünün istatistiklerini kaydet."""
    sql = """
        INSERT INTO pipeline_stats
            (scan_time, scanned_symbols, passed_market_filter, candidates_created,
             ax_allow, ax_veto, ax_watch, risk_rejected, paper_trades_opened, last_error)
        VALUES
            (:scan_time, :scanned_symbols, :passed_market_filter, :candidates_created,
             :ax_allow, :ax_veto, :ax_watch, :risk_rejected, :paper_trades_opened, :last_error)
    """
    defaults = {
        "scan_time": datetime.now(timezone.utc).isoformat(),
        "scanned_symbols": 0, "passed_market_filter": 0, "candidates_created": 0,
        "ax_allow": 0, "ax_veto": 0, "ax_watch": 0,
        "risk_rejected": 0, "paper_trades_opened": 0, "last_error": None,
    }
    with get_conn() as conn:
        conn.execute(sql, {**defaults, **stats})


def get_pipeline_stats(limit: int = 50) -> list:
    """Son N tarama döngüsünün istatistiklerini döndür."""
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT * FROM pipeline_stats ORDER BY id DESC LIMIT ?", (limit,)
        ).fetchall()
        return [dict(r) for r in rows]


def get_pipeline_summary(hours: int = 24) -> dict:
    """Son N saatin birleşik pipeline özeti."""
    cutoff = (datetime.now(timezone.utc) - timedelta(hours=hours)).isoformat()
    with get_conn() as conn:
        row = conn.execute(
            """SELECT
                COUNT(*) as scan_count,
                SUM(scanned_symbols) as scanned,
                SUM(passed_market_filter) as passed,
                SUM(candidates_created) as candidates,
                SUM(ax_allow) as ax_allow,
                SUM(ax_veto) as ax_veto,
                SUM(ax_watch) as ax_watch,
                SUM(risk_rejected) as risk_rejected,
                SUM(paper_trades_opened) as paper_trades,
                MAX(scan_time) as last_scan_time
               FROM pipeline_stats WHERE scan_time >= ?""",
            (cutoff,)
        ).fetchone()
    if not row or not row[0]:
        return {
            "scan_count": 0, "scanned": 0, "passed": 0, "candidates": 0,
            "ax_allow": 0, "ax_veto": 0, "ax_watch": 0,
            "risk_rejected": 0, "paper_trades": 0, "last_scan_time": None,
        }
    return {
        "scan_count":   row[0] or 0,
        "scanned":      row[1] or 0,
        "passed":       row[2] or 0,
        "candidates":   row[3] or 0,
        "ax_allow":     row[4] or 0,
        "ax_veto":      row[5] or 0,
        "ax_watch":     row[6] or 0,
        "risk_rejected":row[7] or 0,
        "paper_trades": row[8] or 0,
        "last_scan_time": row[9],
    }


# ─────────────────────────────────────────────────────────────────────────────
# SIGNAL CANDIDATES — QUERY
# ─────────────────────────────────────────────────────────────────────────────

def get_recent_candidates(limit: int = 50, hours: int = 24) -> list:
    """Son N saatin signal candidate'lerini döndür."""
    cutoff = (datetime.now(timezone.utc) - timedelta(hours=hours)).isoformat()
    with get_conn() as conn:
        rows = conn.execute(
            """SELECT * FROM signal_candidates
               WHERE created_at >= ? ORDER BY id DESC LIMIT ?""",
            (cutoff, limit)
        ).fetchall()
        return [dict(r) for r in rows]


def get_veto_stats(hours: int = 24) -> list:
    """Veto sebeplerini say (son N saat)."""
    cutoff = (datetime.now(timezone.utc) - timedelta(hours=hours)).isoformat()
    with get_conn() as conn:
        rows = conn.execute(
            """SELECT veto_reason, COUNT(*) as cnt
               FROM signal_candidates
               WHERE decision='VETO' AND created_at >= ?
               GROUP BY veto_reason ORDER BY cnt DESC""",
            (cutoff,)
        ).fetchall()
        return [{"reason": r[0] or "unknown", "count": r[1]} for r in rows]


# ─────────────────────────────────────────────────────────────────────────────
# PSEUDO OUTCOME — VETO/WATCH SONUÇ TAKİBİ
# ─────────────────────────────────────────────────────────────────────────────

def get_unchecked_candidates(minutes_ago: int = 35) -> list:
    """
    Şu andan N dakika önce oluşturulmuş, henüz outcome_checked=0 olan
    VETO veya WATCH kararları. Pseudo MFE/MAE için kontrol edilecekler.
    """
    cutoff_start = (datetime.now(timezone.utc) - timedelta(minutes=minutes_ago + 5)).isoformat()
    cutoff_end   = (datetime.now(timezone.utc) - timedelta(minutes=minutes_ago - 5)).isoformat()
    with get_conn() as conn:
        rows = conn.execute(
            """SELECT id, symbol, direction, entry, sl, tp1, tp2
               FROM signal_candidates
               WHERE decision IN ('VETO','WATCH')
                 AND outcome_checked = 0
                 AND created_at BETWEEN ? AND ?""",
            (cutoff_start, cutoff_end)
        ).fetchall()
        return [dict(r) for r in rows]


def update_pseudo_outcome(candidate_id: int, pseudo_mfe_r: float, pseudo_mae_r: float):
    """VETO/WATCH candidate'in pseudo MFE/MAE sonucunu kaydet."""
    with get_conn() as conn:
        conn.execute(
            """UPDATE signal_candidates
               SET pseudo_mfe_r=?, pseudo_mae_r=?, outcome_checked=1
               WHERE id=?""",
            (pseudo_mfe_r, pseudo_mae_r, candidate_id)
        )
