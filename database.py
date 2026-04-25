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

        """)

    # ── Eski şemadan yeni kolonlara migration ─────────────────────────────────
    with get_conn() as conn:
        _migrate(conn)
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
        ("coin_params", "min_volume_m",       "REAL DEFAULT 15.0"),
        ("coin_params", "enabled",            "INTEGER DEFAULT 1"),
        ("coin_params", "updated_at",         "TEXT DEFAULT (datetime('now'))"),
        # paper_account — eski şemada 'paper_balance', yeni şemada 'balance'
        ("paper_account", "balance",         "REAL DEFAULT 250.0"),
        ("paper_account", "initial_balance", "REAL DEFAULT 250.0"),
        # coin_cooldown — eski şemada eksik kolonlar
        ("coin_cooldown", "until",      "TEXT DEFAULT '2000-01-01T00:00:00'"),
        ("coin_cooldown", "reason",     "TEXT"),
        ("coin_cooldown", "created_at", "TEXT DEFAULT (datetime('now'))"),
        # signal_candidates — yeni kolonlar
        ("signal_candidates", "runner_target",  "REAL"),
        ("signal_candidates", "expected_mfe_r", "REAL DEFAULT 0"),
        ("signal_candidates", "market_regime",  "TEXT"),
        ("signal_candidates", "ax_mode",        "TEXT DEFAULT 'execute'"),
        ("signal_candidates", "execution_mode", "TEXT DEFAULT 'paper'"),
        ("signal_candidates", "linked_trade_id","INTEGER"),
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
