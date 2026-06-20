"""
live_tracker.py — AurvexAI Live Trade Feedback
===============================================
Kapanan her trade'i pattern_memory tablosuna kaydeder (MLSignalScorer eğitim verisi)
ve AIDecisionEngine.learn_from_outcome() ile coin profilini günceller.

Çağrı: execution_engine._finalize() → record_close(trade_id, close_price, reason)
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timezone

logger = logging.getLogger("ax.live_tracker")

_PATTERN_MEMORY_DDL = """
CREATE TABLE IF NOT EXISTS pattern_memory (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    symbol            TEXT NOT NULL,
    direction         TEXT NOT NULL DEFAULT 'LONG',
    result            TEXT NOT NULL,          -- WIN | LOSS
    adx               REAL DEFAULT 0,
    rv                REAL DEFAULT 0,
    rsi5              REAL DEFAULT 50,
    rsi1              REAL DEFAULT 50,
    funding_favorable INTEGER DEFAULT 1,
    bb_width_pct      REAL DEFAULT 0,
    ob_ratio          REAL DEFAULT 1,
    volume_m          REAL DEFAULT 0,
    btc_trend         TEXT DEFAULT 'NEUTRAL',
    session           TEXT DEFAULT 'OFF',
    hold_minutes      REAL DEFAULT 0,
    partial_exit      INTEGER DEFAULT 0,
    bb_width_chg      REAL DEFAULT 0,
    momentum_3c       REAL DEFAULT 0,
    prev_result       TEXT DEFAULT 'NONE',
    net_pnl           REAL DEFAULT 0,
    trade_id          INTEGER,
    created_at        TEXT DEFAULT (datetime('now'))
)
"""


def _ensure_table(conn) -> None:
    conn.execute(_PATTERN_MEMORY_DDL)
    conn.execute(
        "CREATE INDEX IF NOT EXISTS idx_pm_symbol ON pattern_memory(symbol)"
    )


def _session_from_hour(hour: int) -> str:
    if 1 <= hour < 4:
        return "ASIA"
    if 7 <= hour < 10:
        return "LONDON"
    if 13 <= hour < 17:
        return "NEWYORK"
    return "OFF"


def _prev_result(conn, symbol: str) -> str:
    """Bu coin için en son tamamlanmış trade'in sonucu."""
    row = conn.execute(
        "SELECT result FROM pattern_memory WHERE symbol=? ORDER BY id DESC LIMIT 1",
        (symbol,),
    ).fetchone()
    if row:
        return row[0]
    return "NONE"


def record_close(trade_id: int, close_price: float, reason: str) -> None:
    """
    Trade kapandığında çağrılır.
    1. Trade + metadata DB'den okunur
    2. pattern_memory'e yazılır (MLSignalScorer eğitim verisi)
    3. AIDecisionEngine.learn_from_outcome() tetiklenir
    """
    try:
        from database import get_conn
        with get_conn() as conn:
            _ensure_table(conn)

            row = conn.execute(
                "SELECT * FROM trades WHERE id=?", (trade_id,)
            ).fetchone()
            if not row:
                logger.warning("[LiveTracker] trade_id=%s bulunamadı", trade_id)
                return

            t = dict(row)

        symbol    = t.get("symbol", "")
        direction = (t.get("direction") or t.get("side", "LONG")).upper()
        net_pnl   = float(t.get("net_pnl") or t.get("realized_pnl") or 0)
        result    = "WIN" if net_pnl > 0 else "LOSS"

        # Hold süresini hesapla
        hold_minutes = 0.0
        try:
            open_t  = t.get("open_time", "") or ""
            close_t = t.get("close_time", "") or ""
            if open_t and close_t:
                open_dt  = datetime.fromisoformat(open_t.replace("Z", "+00:00"))
                close_dt = datetime.fromisoformat(close_t.replace("Z", "+00:00"))
                hold_minutes = (close_dt - open_dt).total_seconds() / 60.0
        except Exception:
            hold_minutes = float(t.get("hold_minutes", 0) or 0)

        # Seans
        hour    = datetime.now(timezone.utc).hour
        session = _session_from_hour(hour)

        # Sinyal özellikleri — metadata'dan oku
        meta: dict = {}
        try:
            raw_meta = t.get("metadata", "{}")
            if raw_meta and isinstance(raw_meta, str):
                meta = json.loads(raw_meta)
            elif isinstance(raw_meta, dict):
                meta = raw_meta
        except Exception:
            pass

        adx               = float(meta.get("adx", 0) or 0)
        rv                = float(meta.get("rv", 0) or 0)
        rsi5              = float(meta.get("rsi5", 50) or 50)
        rsi1              = float(meta.get("rsi1", 50) or 50)
        btc_trend         = str(meta.get("btc_trend", "NEUTRAL") or "NEUTRAL")
        bb_width_pct      = float(meta.get("bb_width_pct", 0) or 0)
        bb_width_chg      = float(meta.get("bb_width_chg", 0) or 0)
        momentum_3c       = float(meta.get("momentum_3c", 0) or 0)
        funding_favorable = int(meta.get("funding_favorable", 1) or 1)
        partial_exit      = 1 if t.get("tp1_hit") else 0

        with get_conn() as conn:
            _ensure_table(conn)
            prev = _prev_result(conn, symbol)
            conn.execute("""
                INSERT INTO pattern_memory
                    (symbol, direction, result, adx, rv, rsi5, rsi1,
                     funding_favorable, bb_width_pct, ob_ratio, volume_m,
                     btc_trend, session, hold_minutes, partial_exit,
                     bb_width_chg, momentum_3c, prev_result, net_pnl, trade_id)
                VALUES (?,?,?,?,?,?,?,?,?,1,0,?,?,?,?,?,?,?,?,?)
            """, (
                symbol, direction, result,
                adx, rv, rsi5, rsi1,
                funding_favorable, bb_width_pct,
                btc_trend, session,
                round(hold_minutes, 1), partial_exit,
                bb_width_chg, momentum_3c, prev,
                round(net_pnl, 6), trade_id,
            ))

        logger.info(
            "[LiveTracker] pattern_memory kaydı: #%s %s %s %s hold=%.0fm",
            trade_id, symbol, direction, result, hold_minutes,
        )

    except Exception as exc:
        logger.error("[LiveTracker] record_close hatası [#%s]: %s", trade_id, exc)
        return

    # AIDecisionEngine coin profil güncelleme
    try:
        from core.ai_decision_engine import AIDecisionEngine
        from config import DB_PATH
        ai = AIDecisionEngine(db_path=DB_PATH)
        ai.learn_from_outcome(symbol=symbol, net_pnl=net_pnl, reason=reason)
    except Exception as exc:
        logger.warning("[LiveTracker] learn_from_outcome hatası: %s", exc)
