"""
scripts/migrate_accounting_schema.py — DB Migration v5.1
========================================================
Mevcut DB'ye eksik kolonları güvenli şekilde ekler.
Veri silmez, tablo düşürmez.
"""
import os
import sys
import sqlite3

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from config import DB_PATH
from database import init_db


def _add_columns(cursor, table: str, columns: dict):
    cursor.execute(f"PRAGMA table_info({table})")
    existing = {row[1] for row in cursor.fetchall()}
    added = 0
    for col, col_type in columns.items():
        if col not in existing:
            try:
                cursor.execute(f"ALTER TABLE {table} ADD COLUMN {col} {col_type}")
                print(f"  [OK] {table}.{col} eklendi")
                added += 1
            except Exception as e:
                print(f"  [WARN] {table}.{col} eklenemedi: {e}")
    return added


def migrate():
    print(f"[Migration] DB: {DB_PATH}")
    init_db()

    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    total_added = 0

    trades_columns = {
        "qty_tp1":            "REAL DEFAULT 0",
        "qty_tp2":            "REAL DEFAULT 0",
        "qty_runner":         "REAL DEFAULT 0",
        "original_qty":       "REAL DEFAULT 0",
        "remaining_qty":      "REAL DEFAULT 0",
        "notional_size":      "REAL DEFAULT 0",
        "margin_used":        "REAL DEFAULT 0",
        "risk_pct":           "REAL DEFAULT 1.0",
        "risk_usd":           "REAL DEFAULT 0",
        "max_loss_after_fee": "REAL DEFAULT 0",
        "tp3":                "REAL",
        "close_price":        "REAL DEFAULT 0",
        "mark_price":         "REAL DEFAULT 0",
        "current_price":      "REAL DEFAULT 0",
        "unrealized_pnl":     "REAL DEFAULT 0",
        "open_fee":           "REAL DEFAULT 0",
        "close_fee":          "REAL DEFAULT 0",
        "fee_rate":           "REAL DEFAULT 0.0004",
        "duration_seconds":   "INTEGER DEFAULT 0",
        "last_update":        "TEXT",
        "source":             "TEXT DEFAULT 'bot'",
        "market_regime":      "TEXT",
        "is_valid_for_stats": "INTEGER DEFAULT 1",
        "archived_reason":    "TEXT",
        "mfe":                "REAL DEFAULT 0",
        "mae":                "REAL DEFAULT 0",
        "entry_zone":         "REAL DEFAULT 0",
        "invalidation_level": "REAL DEFAULT 0",
        "stop_reason":        "TEXT",
        "target_reason":      "TEXT",
        "trigger_score":      "REAL DEFAULT 0",
        "current_R":          "REAL DEFAULT 0",
        "distance_to_sl":     "REAL DEFAULT 0",
        "distance_to_tp1":    "REAL DEFAULT 0",
        "distance_to_tp2":    "REAL DEFAULT 0",
        "distance_to_tp3":    "REAL DEFAULT 0",
    }
    total_added += _add_columns(cursor, "trades", trades_columns)

    coin_profile_columns = {
        "short_bias":          "REAL DEFAULT 0.5",
        "tp1_hit_rate":        "REAL DEFAULT 0",
        "tp2_hit_rate":        "REAL DEFAULT 0",
        "runner_contribution": "REAL DEFAULT 0",
        "avg_duration":        "REAL DEFAULT 0",
        "fakeout_rate":        "REAL DEFAULT 0",
        "fee_drag":            "REAL DEFAULT 0",
        "best_hour":           "INTEGER",
        "best_session":        "TEXT",
        "long_bias":           "REAL DEFAULT 0.5",
        "regime_performance":  "TEXT",
        "danger_score":        "REAL DEFAULT 0",
        "sample_size":         "INTEGER DEFAULT 0",
        "last_updated":        "TEXT",
    }
    total_added += _add_columns(cursor, "coin_profiles", coin_profile_columns)

    signal_columns = {
        "risk_status":     "TEXT",
        "margin_loss_pct": "REAL DEFAULT 0",
        "spread":          "REAL DEFAULT 0",
        "volume":          "REAL DEFAULT 0",
        "volatility":      "REAL DEFAULT 0",
    }
    total_added += _add_columns(cursor, "signal_candidates", signal_columns)

    conn.commit()
    conn.close()

    try:
        import database as db_module
        db_module._TRADE_COLUMNS = None
        print("[Migration] _TRADE_COLUMNS cache sıfırlandı.")
    except Exception as e:
        print(f"[Migration] Cache sıfırlama uyarısı: {e}")

    if total_added == 0:
        print("[Migration] Tüm kolonlar zaten mevcut.")
    else:
        print(f"[Migration] Toplam {total_added} kolon eklendi.")

    print("[Migration] Tamamlandı.")


if __name__ == "__main__":
    migrate()
