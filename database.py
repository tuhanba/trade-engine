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
            ax_mode TEXT DEFAULT 'execute',
            entry REAL, sl REAL, tp1 REAL, tp2 REAL, tp3 REAL,
            runner_target REAL,
            current_price REAL,
            unrealized_pnl REAL DEFAULT 0,
            realized_pnl REAL DEFAULT 0,
            net_pnl REAL DEFAULT 0,
            qty REAL DEFAULT 0,
            qty_tp1 REAL DEFAULT 0,
            qty_tp2 REAL DEFAULT 0,
            qty_runner REAL DEFAULT 0,
            position_size REAL DEFAULT 0,
            notional_size REAL DEFAULT 0,
            risk_percent REAL DEFAULT 1.0,
            confidence REAL DEFAULT 0.8,
            score REAL DEFAULT 0,
            setup_quality TEXT DEFAULT 'B',
            trade_stage TEXT DEFAULT 'open',
            active_target TEXT DEFAULT 'tp1',
            tp1_hit INTEGER DEFAULT 0,
            tp2_hit INTEGER DEFAULT 0,
            trail_stop REAL,
            linked_candidate_id INTEGER,
            linked_candidate_uuid TEXT,
            breakeven_enabled INTEGER DEFAULT 1,
            breakeven_sl REAL,
            open_time TEXT,
            close_time TEXT,
            close_reason TEXT,
            updated_at TEXT DEFAULT (datetime('now'))
        );
        CREATE TABLE IF NOT EXISTS signal_candidates (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            symbol TEXT NOT NULL,
            direction TEXT,
            entry REAL, sl REAL, tp1 REAL, tp2 REAL, tp3 REAL,
            score REAL DEFAULT 0,
            decision TEXT DEFAULT 'PENDING',
            reject_reason TEXT DEFAULT '',
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
        CREATE TABLE IF NOT EXISTS trade_postmortem (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            trade_id INTEGER,
            mfe_r REAL DEFAULT 0,
            mae_r REAL DEFAULT 0,
            created_at TEXT DEFAULT (datetime('now'))
        );
        """)
    _run_migration()

def _run_migration():
    """Mevcut trading.db'ye eksik kolonlari guvenli ALTER TABLE ile ekler. Tekrar calisinca hata vermez."""
    new_columns = [
        ("trades", "tp3",              "REAL"),
        ("trades", "runner_target",    "REAL"),
        ("trades", "current_price",    "REAL"),
        ("trades", "unrealized_pnl",   "REAL DEFAULT 0"),
        ("trades", "realized_pnl",     "REAL DEFAULT 0"),
        ("trades", "qty",              "REAL DEFAULT 0"),
        ("trades", "qty_tp1",          "REAL DEFAULT 0"),
        ("trades", "qty_tp2",          "REAL DEFAULT 0"),
        ("trades", "qty_runner",       "REAL DEFAULT 0"),
        ("trades", "position_size",    "REAL DEFAULT 0"),
        ("trades", "notional_size",    "REAL DEFAULT 0"),
        ("trades", "risk_percent",     "REAL DEFAULT 1.0"),
        ("trades", "confidence",       "REAL DEFAULT 0.8"),
        ("trades", "score",            "REAL DEFAULT 0"),
        ("trades", "setup_quality",    "TEXT DEFAULT 'B'"),
        ("trades", "trade_stage",      "TEXT DEFAULT 'open'"),
        ("trades", "active_target",    "TEXT DEFAULT 'tp1'"),
        ("trades", "tp1_hit",          "INTEGER DEFAULT 0"),
        ("trades", "tp2_hit",          "INTEGER DEFAULT 0"),
        ("trades", "trail_stop",       "REAL"),
        ("trades", "ax_mode",          "TEXT DEFAULT 'execute'"),
        ("trades", "linked_candidate_id",   "INTEGER"),
        ("trades", "linked_candidate_uuid", "TEXT"),
        ("trades", "breakeven_enabled", "INTEGER DEFAULT 1"),
        ("trades", "breakeven_sl",     "REAL"),
        ("trades", "updated_at",       "TEXT DEFAULT (datetime('now'))"),
        ("signal_candidates", "tp3",   "REAL"),
        ("signal_candidates", "reject_reason", "TEXT DEFAULT ''"),
    ]
    with get_conn() as conn:
        for table, col, col_type in new_columns:
            try:
                conn.execute(f"ALTER TABLE {table} ADD COLUMN {col} {col_type}")
                logger.info(f"[Migration] {table}.{col} eklendi.")
            except sqlite3.OperationalError:
                pass

def save_trade(trade: dict) -> int:
    cols = ", ".join(trade.keys())
    placeholders = ", ".join(["?"] * len(trade))
    vals = list(trade.values())
    with get_conn() as conn:
        cur = conn.execute(f"INSERT INTO trades ({cols}) VALUES ({placeholders})", vals)
        return cur.lastrowid

def update_trade(trade_id: int, updates: dict):
    if not updates:
        return
    updates["updated_at"] = datetime.now(timezone.utc).isoformat()
    set_clause = ", ".join([f"{k}=?" for k in updates.keys()])
    vals = list(updates.values()) + [trade_id]
    with get_conn() as conn:
        conn.execute(f"UPDATE trades SET {set_clause} WHERE id=?", vals)

def close_trade(trade_id: int, close_price: float, net_pnl: float, reason: str, hold_min: float = 0):
    with get_conn() as conn:
        conn.execute("""
            UPDATE trades SET
                status='closed', close_reason=?, close_time=?, net_pnl=?,
                current_price=?, trade_stage='closed', updated_at=?
            WHERE id=?
        """, (reason, datetime.now(timezone.utc).isoformat(), net_pnl,
              close_price, datetime.now(timezone.utc).isoformat(), trade_id))

def update_paper_balance(delta: float):
    with get_conn() as conn:
        conn.execute("UPDATE paper_account SET balance = balance + ? WHERE id=1", (delta,))

def save_postmortem(trade_id: int, data: dict):
    with get_conn() as conn:
        conn.execute("""
            INSERT OR REPLACE INTO trade_postmortem (trade_id, mfe_r, mae_r, created_at)
            VALUES (?, ?, ?, datetime('now'))
        """, (trade_id, data.get("mfe_r", 0), data.get("mae_r", 0)))

def get_trades(limit=50):
    with get_conn() as conn:
        rows = conn.execute("SELECT * FROM trades ORDER BY id DESC LIMIT ?", (limit,)).fetchall()
        return [dict(r) for r in rows]

def get_open_trades():
    with get_conn() as conn:
        rows = conn.execute(
            "SELECT * FROM trades WHERE status NOT IN ('closed','closed_win','closed_loss','sl','trail','timeout')"
        ).fetchall()
        return [dict(r) for r in rows]

def get_stats():
    with get_conn() as conn:
        row = conn.execute(
            "SELECT COUNT(*), SUM(net_pnl) FROM trades WHERE status NOT IN ('open','tp1_hit','runner')"
        ).fetchone()
        total = row[0] or 0
        pnl = row[1] or 0
        wins = conn.execute(
            "SELECT COUNT(*) FROM trades WHERE net_pnl > 0 AND status NOT IN ('open','tp1_hit','runner')"
        ).fetchone()[0] or 0
        win_rate = round((wins / total * 100), 1) if total > 0 else 0
        return {
            "total_trades": total,
            "total_pnl": round(pnl, 2),
            "win_rate": win_rate
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

def set_state(key, value):
    with get_conn() as conn:
        conn.execute("INSERT OR REPLACE INTO state (key, value) VALUES (?, ?)", (key, str(value)))

def save_scalp_signal(data):
    try:
        with get_conn() as conn:
            conn.execute("""
                INSERT INTO signal_candidates
                    (symbol, direction, entry, sl, tp1, tp2, tp3, score, decision, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, datetime('now'))
            """, (
                data.get('symbol'), data.get('direction'),
                data.get('entry_zone', data.get('entry', 0)),
                data.get('stop_loss', data.get('sl', 0)),
                data.get('tp1', 0), data.get('tp2', 0), data.get('tp3', 0),
                data.get('final_score', data.get('score', 0)),
                data.get('decision', 'PENDING')
            ))
    except Exception as e:
        logger.warning(f"save_scalp_signal hatasi: {e}")

def save_paper_trade(sig_dict, tracked_from="ghost"):
    try:
        with get_conn() as conn:
            conn.execute("""
                INSERT INTO signal_candidates
                    (symbol, direction, entry, sl, tp1, tp2, tp3, score, decision, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, datetime('now'))
            """, (
                sig_dict.get('symbol'),
                sig_dict.get('direction'),
                sig_dict.get('entry_zone', 0),
                sig_dict.get('stop_loss', 0),
                sig_dict.get('tp1', 0),
                sig_dict.get('tp2', 0),
                sig_dict.get('tp3', 0),
                sig_dict.get('final_score', 0),
                tracked_from
            ))
    except Exception as e:
        logger.warning(f"save_paper_trade hatasi: {e}")

def save_daily_summary(data):
    with get_conn() as conn:
        conn.execute("""
            INSERT OR REPLACE INTO daily_summary
                (date, trade_count, win_count, loss_count, win_rate, gross_pnl, net_pnl, avg_r, max_drawdown, balance_eod)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (data['date'], data['trade_count'], data['win_count'], data['loss_count'],
              data['win_rate'], data['gross_pnl'], data['net_pnl'], data['avg_r'],
              data['max_drawdown'], data['balance_eod']))

def save_weekly_summary(data):
    with get_conn() as conn:
        conn.execute("""
            INSERT OR REPLACE INTO weekly_summary
                (week_start, trade_count, win_count, loss_count, win_rate, net_pnl, avg_r, best_day, worst_day)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (data['week_start'], data['trade_count'], data['win_count'], data['loss_count'],
              data['win_rate'], data['net_pnl'], data['avg_r'], data['best_day'], data['worst_day']))

def save_market_snapshot(data):
    pass

def save_scanned_coin(data):
    pass

def _ensure_paper_results_table():
    """paper_results tablosunu idempotent olarak olusturur."""
    try:
        with get_conn() as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS paper_results (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    candidate_id TEXT,
                    symbol TEXT,
                    direction TEXT,
                    entry REAL,
                    sl REAL,
                    tp1 REAL,
                    tp2 REAL,
                    tp3 REAL,
                    preview_entry REAL,
                    preview_sl REAL,
                    preview_tp1 REAL,
                    horizon_minutes REAL DEFAULT 480.0,
                    result TEXT DEFAULT 'pending',
                    first_touch TEXT,
                    close_price REAL,
                    net_pnl REAL DEFAULT 0,
                    hit_tp INTEGER DEFAULT 0,
                    hit_stop_first INTEGER DEFAULT 0,
                    setup_worked INTEGER DEFAULT 0,
                    would_have_won INTEGER DEFAULT 0,
                    skip_decision_correct INTEGER DEFAULT 0,
                    time_to_move_minutes REAL DEFAULT 0,
                    max_favorable_excursion REAL DEFAULT 0,
                    max_adverse_excursion REAL DEFAULT 0,
                    hold_minutes REAL DEFAULT 0,
                    finalized_at TEXT,
                    tracked_from TEXT DEFAULT 'ghost',
                    status TEXT DEFAULT 'pending',
                    created_at TEXT DEFAULT (datetime('now')),
                    updated_at TEXT DEFAULT (datetime('now'))
                )
            """)
    except Exception as e:
        logger.warning(f"_ensure_paper_results_table hatasi: {e}")

_ensure_paper_results_table()


def get_pending_paper_results(limit: int = 35) -> list:
    """Sonucu henuz belirlenmemis paper trade kayitlarini doner."""
    try:
        with get_conn() as conn:
            rows = conn.execute(
                """SELECT * FROM paper_results
                   WHERE status='pending'
                   ORDER BY created_at ASC
                   LIMIT ?""",
                (limit,)
            ).fetchall()
            return [dict(r) for r in rows]
    except Exception as e:
        logger.warning(f"get_pending_paper_results hatasi: {e}")
        return []


def update_paper_result(row_id: int, updates: dict):
    """Paper result kaydini gunceller."""
    if not updates:
        return
    try:
        with get_conn() as conn:
            set_parts = ", ".join(f"{k}=?" for k in updates)
            vals = list(updates.values()) + [row_id]
            conn.execute(
                f"UPDATE paper_results SET {set_parts}, updated_at=datetime('now') WHERE id=?",
                vals
            )
    except Exception as e:
        logger.warning(f"update_paper_result hatasi: {e}")


def save_paper_result(sig_dict: dict, tracked_from: str = "ghost") -> int:
    """Yeni paper result kaydi olusturur. paper_tracker ile uyumlu."""
    try:
        entry  = sig_dict.get("entry")  or sig_dict.get("preview_entry")
        sl     = sig_dict.get("sl")     or sig_dict.get("preview_sl")
        tp1    = sig_dict.get("tp1")    or sig_dict.get("preview_tp1")
        with get_conn() as conn:
            cur = conn.execute("""
                INSERT INTO paper_results
                    (candidate_id, symbol, direction,
                     entry, sl, tp1, tp2, tp3,
                     preview_entry, preview_sl, preview_tp1,
                     horizon_minutes,
                     tracked_from, status, created_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'pending', datetime('now'))
            """, (
                sig_dict.get("candidate_id"),
                sig_dict.get("symbol"),
                sig_dict.get("direction"),
                entry, sl, tp1,
                sig_dict.get("tp2"),
                sig_dict.get("tp3"),
                entry, sl, tp1,
                float(sig_dict.get("horizon_minutes") or 480.0),
                tracked_from,
            ))
            return cur.lastrowid
    except Exception as e:
        logger.warning(f"save_paper_result hatasi: {e}")
        return 0


def reset_paper_data(initial_balance: float = 250.0, keep_ai_learning: bool = True):
    """
    Kasa ve trade gecmisini sifirlar.
    - trades tablosu temizlenir
    - signal_candidates tablosu temizlenir
    - paper_results tablosu temizlenir
    - live_tracker tablosu temizlenir
    - trade_postmortem tablosu temizlenir
    - paper_account bakiyesi sifirlanir
    - daily_summary / weekly_summary temizlenir
    - AI ogrenme verileri (ai_learning, coin_profiles, ai_postmortem) KORUNUR
      (keep_ai_learning=False ise onlar da silinir)
    """
    try:
        with get_conn() as conn:
            conn.execute("DELETE FROM trades")
            conn.execute("DELETE FROM signal_candidates")
            conn.execute("DELETE FROM trade_postmortem")
            conn.execute("DELETE FROM daily_summary")
            conn.execute("DELETE FROM weekly_summary")
            try:
                conn.execute("DELETE FROM paper_results")
            except Exception:
                pass
            try:
                conn.execute("DELETE FROM live_tracker")
            except Exception:
                pass
            # Kasa sifirla
            conn.execute(
                "UPDATE paper_account SET balance=?, initial_balance=? WHERE id=1",
                (initial_balance, initial_balance)
            )
            if not keep_ai_learning:
                try:
                    conn.execute("DELETE FROM ai_learning")
                    conn.execute("DELETE FROM coin_profiles")
                    conn.execute("DELETE FROM ai_postmortem")
                except Exception:
                    pass
        logger.info(
            f"[Reset] Paper data sifirlandı. Bakiye: {initial_balance}$ | "
            f"AI learning korundu: {keep_ai_learning}"
        )
        return True
    except Exception as e:
        logger.error(f"reset_paper_data hatasi: {e}")
        return False
