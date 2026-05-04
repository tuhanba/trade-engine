"""
coin_library.py — Coin Performans Kütüphanesi
=============================================
Her coin için win_rate, total_pnl, R-multiple ve MFE/MAE istatistiklerini tutar.
"""
import sqlite3
import logging
from datetime import datetime, timezone
from config import DB_PATH

logger = logging.getLogger(__name__)

def _conn():
    c = sqlite3.connect(DB_PATH, timeout=30, check_same_thread=False)
    c.row_factory = sqlite3.Row
    c.execute("PRAGMA journal_mode=WAL")
    return c

def _init_table():
    try:
        with _conn() as c:
            c.execute("""
                CREATE TABLE IF NOT EXISTS coin_library (
                    symbol TEXT PRIMARY KEY,
                    total_trades INTEGER DEFAULT 0,
                    win_count INTEGER DEFAULT 0,
                    loss_count INTEGER DEFAULT 0,
                    win_rate REAL DEFAULT 0,
                    total_pnl REAL DEFAULT 0,
                    avg_r_multiple REAL DEFAULT 0,
                    avg_mfe_r REAL DEFAULT 0,
                    avg_mae_r REAL DEFAULT 0,
                    danger_score REAL DEFAULT 0,
                    last_result TEXT,
                    updated_at TEXT DEFAULT (datetime('now'))
                )
            """)
    except Exception as e:
        logger.warning(f"[CoinLibrary] init hatasi: {e}")

_init_table()

def update_coin_stats(symbol: str, result: str, net_pnl: float,
                      r_multiple: float = 0, mfe_r: float = 0,
                      mae_r: float = 0, direction: str = None):
    """Coin istatistiklerini günceller."""
    try:
        with _conn() as c:
            row = c.execute(
                "SELECT * FROM coin_library WHERE symbol=?", (symbol,)
            ).fetchone()

            if row:
                r = dict(row)
                total = r["total_trades"] + 1
                wins  = r["win_count"] + (1 if result == "WIN" else 0)
                losses = r["loss_count"] + (1 if result == "LOSS" else 0)
                win_rate = round(wins / total * 100, 1)
                total_pnl = round((r["total_pnl"] or 0) + net_pnl, 4)
                # Hareketli ortalama
                avg_r = round(((r["avg_r_multiple"] or 0) * (total - 1) + r_multiple) / total, 3)
                avg_mfe = round(((r["avg_mfe_r"] or 0) * (total - 1) + mfe_r) / total, 3)
                avg_mae = round(((r["avg_mae_r"] or 0) * (total - 1) + mae_r) / total, 3)
                # Danger score: art arda kayıp varsa artar
                danger = r["danger_score"] or 0
                if result == "LOSS":
                    danger = min(danger + 10, 100)
                else:
                    danger = max(danger - 5, 0)

                c.execute("""
                    UPDATE coin_library SET
                        total_trades=?, win_count=?, loss_count=?, win_rate=?,
                        total_pnl=?, avg_r_multiple=?, avg_mfe_r=?, avg_mae_r=?,
                        danger_score=?, last_result=?, updated_at=?
                    WHERE symbol=?
                """, (total, wins, losses, win_rate, total_pnl, avg_r, avg_mfe, avg_mae,
                      danger, result, datetime.now(timezone.utc).isoformat(), symbol))
            else:
                win_count = 1 if result == "WIN" else 0
                loss_count = 1 if result == "LOSS" else 0
                c.execute("""
                    INSERT INTO coin_library
                        (symbol, total_trades, win_count, loss_count, win_rate,
                         total_pnl, avg_r_multiple, avg_mfe_r, avg_mae_r,
                         danger_score, last_result, updated_at)
                    VALUES (?, 1, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, (symbol, win_count, loss_count,
                      100.0 if result == "WIN" else 0.0,
                      round(net_pnl, 4), round(r_multiple, 3),
                      round(mfe_r, 3), round(mae_r, 3),
                      10 if result == "LOSS" else 0,
                      result, datetime.now(timezone.utc).isoformat()))

            # coin_profiles tablosunu da güncelle (dashboard okur)
            try:
                row2 = c.execute(
                    "SELECT * FROM coin_library WHERE symbol=?", (symbol,)
                ).fetchone()
                if row2:
                    r2 = dict(row2)
                    c.execute("""
                        INSERT OR REPLACE INTO coin_profiles
                            (symbol, win_rate, total_trades, total_pnl, danger_score, updated_at)
                        VALUES (?, ?, ?, ?, ?, ?)
                    """, (symbol, r2["win_rate"], r2["total_trades"],
                          r2["total_pnl"], r2["danger_score"],
                          datetime.now(timezone.utc).isoformat()))
            except Exception:
                pass

        logger.info(f"[CoinLibrary] {symbol} guncellendi: {result} pnl={net_pnl:+.3f}$")
    except Exception as e:
        logger.warning(f"[CoinLibrary] update_coin_stats hatasi: {e}")

def get_coin_stats(symbol: str) -> dict:
    """Coin istatistiklerini döner."""
    try:
        with _conn() as c:
            row = c.execute(
                "SELECT * FROM coin_library WHERE symbol=?", (symbol,)
            ).fetchone()
            return dict(row) if row else {}
    except Exception as e:
        logger.warning(f"[CoinLibrary] get_coin_stats hatasi: {e}")
        return {}

def get_danger_coins(threshold: float = 50) -> list:
    """Danger score'u yüksek coinleri döner."""
    try:
        with _conn() as c:
            rows = c.execute(
                "SELECT symbol, danger_score FROM coin_library WHERE danger_score >= ? ORDER BY danger_score DESC",
                (threshold,)
            ).fetchall()
            return [dict(r) for r in rows]
    except Exception as e:
        logger.warning(f"[CoinLibrary] get_danger_coins hatasi: {e}")
        return []
