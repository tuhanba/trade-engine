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
from typing import Any, Optional

import config
from core.data_layer import SignalData, TradeData

logger = logging.getLogger("ax.database")


# ── Bağlantı ────────────────────────────────────────────────────────

def get_connection() -> sqlite3.Connection:
    """WAL modunda SQLite bağlantısı döner."""
    conn = sqlite3.connect(config.DB_PATH, timeout=10)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    conn.execute("PRAGMA synchronous=NORMAL")
    conn.execute("PRAGMA cache_size=-8000")  # 8MB cache
    return conn


# ── Tablo DDL'leri ───────────────────────────────────────────────────

_TRADES_DDL = """
CREATE TABLE IF NOT EXISTS trades (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    symbol              TEXT NOT NULL,
    side                TEXT NOT NULL,
    entry_price         REAL NOT NULL,
    stop_loss           REAL DEFAULT 0,
    tp1                 REAL DEFAULT 0,
    tp2                 REAL DEFAULT 0,
    tp3                 REAL DEFAULT 0,
    quantity            REAL DEFAULT 0,
    leverage            INTEGER DEFAULT 1,
    notional            REAL DEFAULT 0,
    margin_used         REAL DEFAULT 0,
    risk_usd            REAL DEFAULT 0,
    risk_pct            REAL DEFAULT 0,
    status              TEXT DEFAULT 'OPEN',
    opened_at           TEXT,
    closed_at           TEXT,
    current_price       REAL DEFAULT 0,
    unrealized_pnl      REAL DEFAULT 0,
    realized_pnl        REAL DEFAULT 0,
    accumulated_pnl     REAL DEFAULT 0,
    remaining_qty_pct   REAL DEFAULT 100,
    exit_price          REAL DEFAULT 0,
    close_reason        TEXT DEFAULT '',
    metadata            TEXT DEFAULT '{}'
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
    metadata        TEXT DEFAULT '{}'
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

# ── Migration kolonları ──────────────────────────────────────────────

_EXPECTED_COLUMNS: dict[str, list[tuple[str, str]]] = {
    "trades": [
        ("tp2", "REAL DEFAULT 0"),
        ("tp3", "REAL DEFAULT 0"),
        ("leverage", "INTEGER DEFAULT 1"),
        ("notional", "REAL DEFAULT 0"),
        ("margin_used", "REAL DEFAULT 0"),
        ("risk_usd", "REAL DEFAULT 0"),
        ("risk_pct", "REAL DEFAULT 0"),
        ("current_price", "REAL DEFAULT 0"),
        ("unrealized_pnl", "REAL DEFAULT 0"),
        ("realized_pnl", "REAL DEFAULT 0"),
        ("accumulated_pnl", "REAL DEFAULT 0"),
        ("remaining_qty_pct", "REAL DEFAULT 100"),
        ("exit_price", "REAL DEFAULT 0"),
        ("close_reason", "TEXT DEFAULT ''"),
        ("metadata", "TEXT DEFAULT '{}'"),
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
    ],
}


def init_db() -> None:
    """Tabloları oluşturur (var olanları silmez)."""
    conn = get_connection()
    try:
        conn.execute(_TRADES_DDL)
        conn.execute(_SIGNAL_CANDIDATES_DDL)
        conn.execute(_BALANCE_LEDGER_DDL)
        conn.execute(_BOT_STATUS_DDL)
        conn.execute(_PARTIAL_CLOSES_DDL)
        # İndeksler (performans)
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_trades_status ON trades(status)"
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_trades_symbol ON trades(symbol)"
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_signals_symbol ON signal_candidates(symbol)"
        )
        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_signals_created ON signal_candidates(created_at)"
        )
        conn.commit()
        logger.info("DB tabloları hazır: %s", config.DB_PATH)
    finally:
        conn.close()


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
    try:
        cur = conn.execute(
            """
            INSERT INTO trades
                (symbol, side, entry_price, stop_loss, tp1, tp2, tp3,
                 quantity, leverage, notional, margin_used, risk_usd,
                 risk_pct, status, opened_at, current_price,
                 unrealized_pnl, realized_pnl, accumulated_pnl,
                 remaining_qty_pct, exit_price, close_reason, metadata)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            """,
            (
                trade.symbol, trade.side, trade.entry_price,
                trade.stop_loss,
                trade.tp1 or 0, trade.tp2 or 0, trade.tp3 or 0,
                trade.quantity, trade.leverage, trade.notional,
                trade.margin_used, trade.risk_usd, trade.risk_pct,
                trade.status, trade.opened_at, trade.current_price,
                trade.unrealized_pnl, trade.realized_pnl,
                0.0,    # accumulated_pnl
                100.0,  # remaining_qty_pct
                trade.exit_price, trade.close_reason,
                metadata or "{}",
            ),
        )
        conn.commit()
        return cur.lastrowid
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
    """Açık trade'in güncel fiyatını ve unrealized PnL'ini günceller."""
    conn = get_connection()
    try:
        conn.execute(
            """
            UPDATE trades
            SET current_price = ?, unrealized_pnl = ?
            WHERE id = ? AND status = 'OPEN'
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
            "UPDATE trades SET stop_loss = ? WHERE id = ? AND status = 'OPEN'",
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
        update_sql = """
            UPDATE trades
            SET accumulated_pnl = accumulated_pnl + ?,
                remaining_qty_pct = CASE
                    WHEN remaining_qty_pct - ? < 0 THEN 0
                    ELSE remaining_qty_pct - ?
                END
        """
        params = [partial_pnl, close_pct, close_pct]

        if new_sl is not None and new_sl > 0:
            update_sql += ", stop_loss = ?"
            params.append(new_sl)

        update_sql += " WHERE id = ? AND status = 'OPEN'"
        params.append(trade_id)

        conn.execute(update_sql, params)
        conn.commit()

        logger.info(
            "Partial close kaydedildi: #%s  qty=%.4f  pct=%.1f%%  pnl=%.4f  reason=%s",
            trade_id, close_qty, close_pct, partial_pnl, reason,
        )
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
            SET status = 'CLOSED',
                exit_price = ?,
                realized_pnl = ?,
                unrealized_pnl = 0,
                close_reason = ?,
                closed_at = ?
            WHERE id = ?
            """,
            (exit_price, realized_pnl, close_reason, now, trade_id),
        )
        conn.commit()
    except Exception as exc:
        logger.error("Trade kapatılamadı [%s]: %s", trade_id, exc)
    finally:
        conn.close()


# ── Trade sorgular ─────────────────────────────────────────────────

def get_open_trades() -> list[dict]:
    """Açık trade'leri dict listesi olarak döner."""
    conn = get_connection()
    try:
        rows = conn.execute(
            "SELECT * FROM trades WHERE status = 'OPEN' ORDER BY opened_at DESC"
        ).fetchall()
        return [dict(r) for r in rows]
    except Exception as exc:
        logger.error("Open trades alınamadı: %s", exc)
        return []
    finally:
        conn.close()


def get_recent_trades(limit: int = 100) -> list[dict]:
    """Son trade'leri döner."""
    conn = get_connection()
    try:
        rows = conn.execute(
            "SELECT * FROM trades ORDER BY id DESC LIMIT ?", (limit,)
        ).fetchall()
        return [dict(r) for r in rows]
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

def get_dashboard_stats() -> dict:
    """Dashboard için özet istatistikler."""
    conn = get_connection()
    try:
        total = conn.execute("SELECT COUNT(*) FROM trades").fetchone()[0]
        open_count = conn.execute(
            "SELECT COUNT(*) FROM trades WHERE status='OPEN'"
        ).fetchone()[0]
        closed_count = conn.execute(
            "SELECT COUNT(*) FROM trades WHERE status='CLOSED'"
        ).fetchone()[0]

        rpnl_row = conn.execute(
            "SELECT COALESCE(SUM(realized_pnl), 0) FROM trades WHERE status='CLOSED'"
        ).fetchone()
        realized_pnl = float(rpnl_row[0]) if rpnl_row else 0.0

        upnl_row = conn.execute(
            "SELECT COALESCE(SUM(unrealized_pnl), 0) FROM trades WHERE status='OPEN'"
        ).fetchone()
        unrealized_pnl = float(upnl_row[0]) if upnl_row else 0.0

        # Accumulated partial PnL (açık trade'lerdeki)
        accum_row = conn.execute(
            "SELECT COALESCE(SUM(accumulated_pnl), 0) FROM trades WHERE status='OPEN'"
        ).fetchone()
        accumulated_pnl = float(accum_row[0]) if accum_row else 0.0

        win_count = conn.execute(
            "SELECT COUNT(*) FROM trades WHERE status='CLOSED' AND realized_pnl > 0"
        ).fetchone()[0]
        winrate = round(
            (win_count / closed_count * 100), 1
        ) if closed_count > 0 else 0.0

        # Bugünkü PnL
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        today_row = conn.execute(
            "SELECT COALESCE(SUM(realized_pnl), 0) FROM trades "
            "WHERE status='CLOSED' AND DATE(closed_at) = ?",
            (today,),
        ).fetchone()
        today_pnl = float(today_row[0]) if today_row else 0.0

        # Bakiye: ledger + realized PnL
        initial_balance = get_latest_balance(
            getattr(config, "INITIAL_PAPER_BALANCE", 250.0)
        )
        balance = initial_balance + realized_pnl

        # Ghost tracking özeti
        ghost_tp = conn.execute(
            "SELECT COUNT(*) FROM signal_candidates WHERE status='TP_HIT'"
        ).fetchone()[0]
        ghost_sl = conn.execute(
            "SELECT COUNT(*) FROM signal_candidates WHERE status='SL_HIT'"
        ).fetchone()[0]

        return {
            "total_trades": total,
            "open_trades": open_count,
            "closed_trades": closed_count,
            "realized_pnl": round(realized_pnl, 4),
            "unrealized_pnl": round(unrealized_pnl, 4),
            "accumulated_pnl": round(accumulated_pnl, 4),
            "total_pnl": round(realized_pnl + unrealized_pnl + accumulated_pnl, 4),
            "today_pnl": round(today_pnl, 4),
            "winrate": winrate,
            "balance": round(balance, 4),
            "ghost_tp_hits": ghost_tp,
            "ghost_sl_hits": ghost_sl,
            "ghost_winrate": round(
                ghost_tp / (ghost_tp + ghost_sl) * 100, 1
            ) if (ghost_tp + ghost_sl) > 0 else 0.0,
        }
    except Exception as exc:
        logger.error("Dashboard stats alınamadı: %s", exc)
        return {
            "total_trades": 0, "open_trades": 0, "closed_trades": 0,
            "realized_pnl": 0, "unrealized_pnl": 0, "accumulated_pnl": 0,
            "total_pnl": 0, "today_pnl": 0, "winrate": 0,
            "balance": getattr(config, "INITIAL_PAPER_BALANCE", 250.0),
            "ghost_tp_hits": 0, "ghost_sl_hits": 0, "ghost_winrate": 0,
        }
    finally:
        conn.close()


# ── Bot status ─────────────────────────────────────────────────────

def update_bot_status(key: str, value: str) -> None:
    """Bot durum anahtarını günceller (upsert)."""
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
    """Bot durum bilgisini döner."""
    conn = get_connection()
    try:
        if key is not None:
            row = conn.execute(
                "SELECT value, updated_at FROM bot_status WHERE key = ?",
                (key,),
            ).fetchone()
            if row:
                return {"value": row["value"], "updated_at": row["updated_at"]}
            return {}

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


def get_latest_balance(default: float = 250.0) -> float:
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
