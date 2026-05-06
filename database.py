"""
database.py — AX Merkezi Veritabanı v5.0 (LIVE-READY)
======================================================
Tüm tablo tanımları, CRUD fonksiyonları ve migration desteği.
Eski gerçek veriyi silmez. reset_paper_data varsayılan olarak DELETE yapmaz.
"""
import sqlite3
import json
import logging
from datetime import datetime, timezone
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
            status TEXT DEFAULT 'open',
            entry REAL, sl REAL, tp1 REAL, tp2 REAL, tp3 REAL,
            original_qty REAL, remaining_qty REAL,
            qty_tp1 REAL, qty_tp2 REAL, qty_runner REAL,
            leverage INTEGER DEFAULT 10,
            notional_size REAL DEFAULT 0,
            margin_used REAL DEFAULT 0,
            risk_pct REAL DEFAULT 1.0,
            risk_usd REAL DEFAULT 0,
            max_loss_after_fee REAL DEFAULT 0,
            realized_pnl REAL DEFAULT 0,
            unrealized_pnl REAL DEFAULT 0,
            net_pnl REAL DEFAULT 0,
            total_fee REAL DEFAULT 0,
            open_fee REAL DEFAULT 0,
            close_fee REAL DEFAULT 0,
            fee_rate REAL DEFAULT 0.0004,
            tp1_hit INTEGER DEFAULT 0,
            tp2_hit INTEGER DEFAULT 0,
            open_time TEXT,
            close_time TEXT,
            duration_seconds INTEGER DEFAULT 0,
            close_reason TEXT,
            r_multiple REAL DEFAULT 0,
            mfe REAL DEFAULT 0,
            mae REAL DEFAULT 0,
            setup_quality TEXT,
            final_score REAL,
            market_regime TEXT,
            is_valid_for_stats INTEGER DEFAULT 1,
            archived_reason TEXT
        );

        CREATE TABLE IF NOT EXISTS partial_closes (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            trade_id INTEGER NOT NULL,
            symbol TEXT,
            close_type TEXT,
            close_qty REAL,
            close_price REAL,
            net_pnl REAL,
            fee REAL,
            timestamp TEXT DEFAULT (datetime('now')),
            FOREIGN KEY (trade_id) REFERENCES trades(id)
        );

        CREATE TABLE IF NOT EXISTS balance_ledger (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            trade_id INTEGER,
            symbol TEXT,
            event_type TEXT,
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

        CREATE TABLE IF NOT EXISTS signal_candidates (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            uuid TEXT,
            symbol TEXT,
            direction TEXT,
            entry REAL, sl REAL, tp1 REAL, tp2 REAL, tp3 REAL,
            setup_quality TEXT,
            final_score REAL,
            decision TEXT,
            reason TEXT,
            market_regime TEXT,
            created_at TEXT DEFAULT (datetime('now'))
        );

        CREATE TABLE IF NOT EXISTS paper_results (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            symbol TEXT,
            direction TEXT,
            preview_entry REAL,
            preview_sl REAL,
            preview_tp1 REAL,
            tracked_from TEXT,
            horizon_minutes REAL DEFAULT 480,
            hit_tp INTEGER DEFAULT 0,
            hit_stop_first INTEGER DEFAULT 0,
            time_to_move_minutes REAL DEFAULT 0,
            max_favorable_excursion REAL DEFAULT 0,
            max_adverse_excursion REAL DEFAULT 0,
            setup_worked INTEGER DEFAULT 0,
            would_have_won INTEGER DEFAULT 0,
            first_touch TEXT,
            skip_decision_correct INTEGER,
            status TEXT DEFAULT 'pending',
            created_at TEXT DEFAULT (datetime('now')),
            finalized_at TEXT
        );

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

        CREATE TABLE IF NOT EXISTS coin_profiles (
            symbol TEXT PRIMARY KEY,
            win_rate REAL DEFAULT 0,
            avg_r REAL DEFAULT 0,
            profit_factor REAL DEFAULT 0,
            tp1_hit_rate REAL DEFAULT 0,
            tp2_hit_rate REAL DEFAULT 0,
            runner_contribution REAL DEFAULT 0,
            avg_duration REAL DEFAULT 0,
            fakeout_rate REAL DEFAULT 0,
            fee_drag REAL DEFAULT 0,
            best_hour INTEGER,
            best_session TEXT,
            long_bias REAL DEFAULT 0.5,
            regime_performance TEXT,
            danger_score REAL DEFAULT 0,
            sample_size INTEGER DEFAULT 0,
            last_updated TEXT
        );

        CREATE TABLE IF NOT EXISTS coin_library (
            symbol TEXT PRIMARY KEY,
            min_qty REAL,
            step_size REAL,
            tick_size REAL,
            min_notional REAL DEFAULT 5.0,
            status TEXT DEFAULT 'TRADING',
            last_updated TEXT
        );

        CREATE TABLE IF NOT EXISTS trade_events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            trade_id INTEGER,
            event_type TEXT,
            data TEXT,
            timestamp TEXT DEFAULT (datetime('now'))
        );

        CREATE TABLE IF NOT EXISTS daily_summary (
            date TEXT PRIMARY KEY,
            trade_count INTEGER DEFAULT 0,
            win_count INTEGER DEFAULT 0,
            loss_count INTEGER DEFAULT 0,
            win_rate REAL DEFAULT 0,
            gross_pnl REAL DEFAULT 0,
            net_pnl REAL DEFAULT 0,
            avg_r REAL DEFAULT 0,
            max_drawdown REAL DEFAULT 0,
            balance_eod REAL DEFAULT 0
        );

        CREATE TABLE IF NOT EXISTS weekly_summary (
            week_start TEXT PRIMARY KEY,
            trade_count INTEGER DEFAULT 0,
            win_count INTEGER DEFAULT 0,
            loss_count INTEGER DEFAULT 0,
            win_rate REAL DEFAULT 0,
            net_pnl REAL DEFAULT 0,
            avg_r REAL DEFAULT 0,
            best_day TEXT,
            worst_day TEXT
        );

        CREATE TABLE IF NOT EXISTS system_state (
            key TEXT PRIMARY KEY,
            value TEXT,
            updated_at TEXT DEFAULT (datetime('now'))
        );
        """)
    logger.info("[DB] init_db tamamlandı.")


# ── TRADE CRUD ───────────────────────────────────────────────────────────────

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


def close_trade(trade_id: int, net_pnl: float, total_fee: float,
                reason: str, r_multiple: float = 0):
    now = datetime.now(timezone.utc).isoformat()
    with get_conn() as conn:
        row = conn.execute("SELECT open_time FROM trades WHERE id = ?", (trade_id,)).fetchone()
        duration_seconds = 0
        if row and row["open_time"]:
            try:
                opened = datetime.fromisoformat(str(row["open_time"]).replace("Z", "+00:00"))
                if opened.tzinfo is None:
                    opened = opened.replace(tzinfo=timezone.utc)
                duration_seconds = int((datetime.now(timezone.utc) - opened).total_seconds())
            except Exception:
                pass
        conn.execute("""
            UPDATE trades SET
            status = 'closed',
            net_pnl = ?,
            total_fee = ?,
            remaining_qty = 0,
            close_time = ?,
            close_reason = ?,
            r_multiple = ?,
            duration_seconds = ?
            WHERE id = ?
        """, (net_pnl, total_fee, now, reason, r_multiple, duration_seconds, trade_id))


def get_open_trades() -> list:
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT * FROM trades WHERE status NOT IN ('closed') AND status IS NOT NULL"
        ).fetchall()
        return [dict(r) for r in rows]


def get_trade_by_id(trade_id: int) -> dict:
    with get_conn() as conn:
        row = conn.execute("SELECT * FROM trades WHERE id = ?", (trade_id,)).fetchone()
        return dict(row) if row else {}


# ── STATS ────────────────────────────────────────────────────────────────────

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


# ── PAPER BALANCE ────────────────────────────────────────────────────────────

def get_paper_balance() -> float:
    with get_conn() as conn:
        row = conn.execute("SELECT balance FROM paper_account WHERE id=1").fetchone()
        return float(row[0]) if row else 250.0


def update_paper_balance(amount: float) -> float:
    with get_conn() as conn:
        row = conn.execute("SELECT balance FROM paper_account WHERE id=1").fetchone()
        current = float(row[0]) if row else 250.0
        new_balance = current + amount
        conn.execute("UPDATE paper_account SET balance = ? WHERE id=1", (new_balance,))
        return new_balance


# ── LEDGER ───────────────────────────────────────────────────────────────────

def add_ledger_entry(trade_id, symbol, event_type, amount, note=""):
    with get_conn() as conn:
        row = conn.execute("SELECT balance FROM paper_account WHERE id=1").fetchone()
        balance_before = float(row[0]) if row else 250.0
        balance_after = balance_before + amount
        conn.execute("UPDATE paper_account SET balance = ? WHERE id=1", (balance_after,))
        conn.execute("""
            INSERT INTO balance_ledger (trade_id, symbol, event_type, amount,
                                        balance_before, balance_after, note)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        """, (trade_id, symbol, event_type, amount, balance_before, balance_after, note))
        return balance_after


# ── PARTIAL CLOSES ───────────────────────────────────────────────────────────

def save_partial_close(trade_id, symbol, close_type, close_qty,
                       close_price, net_pnl, fee):
    with get_conn() as conn:
        conn.execute("""
            INSERT INTO partial_closes
            (trade_id, symbol, close_type, close_qty, close_price, net_pnl, fee)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        """, (trade_id, symbol, close_type, close_qty, close_price, net_pnl, fee))


def get_partial_closes(trade_id) -> list:
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT * FROM partial_closes WHERE trade_id = ? ORDER BY id",
            (trade_id,)
        ).fetchall()
        return [dict(r) for r in rows]


# ── ARCHIVE ──────────────────────────────────────────────────────────────────

def archive_invalid_trade(trade_id, reason="manual_archive"):
    with get_conn() as conn:
        conn.execute("""
            UPDATE trades SET is_valid_for_stats = 0, archived_reason = ?
            WHERE id = ?
        """, (reason, trade_id))


# ── SIGNAL CANDIDATES ───────────────────────────────────────────────────────

def save_signal_candidate(data: dict):
    with get_conn() as conn:
        conn.execute("""
            INSERT INTO signal_candidates
            (uuid, symbol, direction, entry, sl, tp1, tp2, tp3,
             setup_quality, final_score, decision, reason, market_regime)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            data.get("uuid"), data.get("symbol"), data.get("direction"),
            data.get("entry"), data.get("sl"), data.get("tp1"),
            data.get("tp2"), data.get("tp3"),
            data.get("setup_quality"), data.get("final_score"),
            data.get("decision"), data.get("reason"),
            data.get("market_regime"),
        ))


def save_scalp_signal(data: dict):
    save_signal_candidate({
        "uuid": data.get("id"),
        "symbol": data.get("symbol"),
        "direction": data.get("direction"),
        "entry": data.get("entry_zone", data.get("entry")),
        "sl": data.get("stop_loss", data.get("sl")),
        "tp1": data.get("tp1"), "tp2": data.get("tp2"), "tp3": data.get("tp3"),
        "setup_quality": data.get("setup_quality"),
        "final_score": data.get("final_score"),
        "decision": "ALLOW",
        "reason": data.get("reason", ""),
    })


# ── PAPER RESULTS ───────────────────────────────────────────────────────────

def save_paper_trade(data: dict, tracked_from: str = "candidate"):
    with get_conn() as conn:
        conn.execute("""
            INSERT INTO paper_results
            (symbol, direction, preview_entry, preview_sl, preview_tp1,
             tracked_from, status)
            VALUES (?, ?, ?, ?, ?, ?, 'pending')
        """, (
            data.get("symbol"),
            data.get("direction"),
            data.get("entry_zone", data.get("entry")),
            data.get("stop_loss", data.get("sl")),
            data.get("tp1"),
            tracked_from,
        ))


def save_paper_result(data: dict):
    save_paper_trade(data)


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


# ── COIN PROFILE ─────────────────────────────────────────────────────────────

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
                "long_bias", "regime_performance", "danger_score",
                "sample_size",
            }
            filtered = {k: v for k, v in updates.items() if k in valid_cols}
            filtered["last_updated"] = now
            set_clause = ", ".join([f"{k} = ?" for k in filtered.keys()])
            conn.execute(
                f"UPDATE coin_profiles SET {set_clause} WHERE symbol = ?",
                list(filtered.values()) + [symbol]
            )
        else:
            conn.execute(
                "INSERT INTO coin_profiles (symbol, last_updated) VALUES (?, ?)",
                (symbol, now)
            )
            if updates:
                update_coin_profile(symbol, updates)


# ── TRADE STATS (MFE/MAE) ───────────────────────────────────────────────────

def update_trade_stats(trade_id, mfe=None, mae=None):
    with get_conn() as conn:
        if mfe is not None:
            conn.execute("UPDATE trades SET mfe = MAX(mfe, ?) WHERE id = ?", (mfe, trade_id))
        if mae is not None:
            conn.execute("UPDATE trades SET mae = MIN(mae, ?) WHERE id = ?", (mae, trade_id))


# ── AI LOGS ──────────────────────────────────────────────────────────────────

def save_ai_log(event, symbol, decision, score, confidence, reason, data):
    with get_conn() as conn:
        conn.execute("""
            INSERT INTO ai_logs (event, symbol, decision, score, confidence, reason, data)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        """, (event, symbol, decision, score, confidence, reason, data))


# ── POSTMORTEM ───────────────────────────────────────────────────────────────

def save_postmortem(trade_id, data: dict):
    save_trade_event(trade_id, "POSTMORTEM", json.dumps(data))


# ── TRADE EVENTS ─────────────────────────────────────────────────────────────

def save_trade_event(trade_id, event_type, data=""):
    with get_conn() as conn:
        conn.execute("""
            INSERT INTO trade_events (trade_id, event_type, data)
            VALUES (?, ?, ?)
        """, (trade_id, event_type, data))


# ── SYSTEM STATE ─────────────────────────────────────────────────────────────

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


# ── DAILY/WEEKLY SUMMARY ────────────────────────────────────────────────────

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
            data["net_pnl"], data["avg_r"], data["max_drawdown"],
            data["balance_eod"],
            data["trade_count"], data["win_count"],
            data["loss_count"], data["win_rate"], data["gross_pnl"],
            data["net_pnl"], data["avg_r"], data["max_drawdown"],
            data["balance_eod"],
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
            data["trade_count"], data["win_count"],
            data["loss_count"], data["win_rate"], data["net_pnl"],
            data["avg_r"], data.get("best_day"), data.get("worst_day"),
        ))


# ── COIN LIBRARY ─────────────────────────────────────────────────────────────

def save_coin_library(symbol, filters: dict):
    with get_conn() as conn:
        now = datetime.now(timezone.utc).isoformat()
        conn.execute("""
            INSERT INTO coin_library (symbol, min_qty, step_size, tick_size, min_notional, status, last_updated)
            VALUES (?, ?, ?, ?, ?, 'TRADING', ?)
            ON CONFLICT(symbol) DO UPDATE SET
            min_qty=?, step_size=?, tick_size=?, min_notional=?, status='TRADING', last_updated=?
        """, (
            symbol, filters.get("min_qty"), filters.get("step_size"),
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


# ── RESET (SAFE) ─────────────────────────────────────────────────────────────

def reset_paper_data(force_delete=False):
    """
    Paper verileri sıfırlar.
    force_delete=False: Sadece is_valid_for_stats=0 yapar.
    force_delete=True: Gerçek DELETE yapar (açık onay gerekir).
    """
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
