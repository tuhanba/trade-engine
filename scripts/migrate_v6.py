import sqlite3
import os

DB_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "trading.db")

def migrate():
    print(f"Veritabanı migration başlatılıyor: {DB_PATH}")
    conn = sqlite3.connect(DB_PATH)
    
    # trades tablosuna eksik alanlar
    try:
        conn.execute("ALTER TABLE trades ADD COLUMN entry_zone REAL DEFAULT 0")
        conn.execute("ALTER TABLE trades ADD COLUMN invalidation_level REAL DEFAULT 0")
        conn.execute("ALTER TABLE trades ADD COLUMN stop_reason TEXT")
        conn.execute("ALTER TABLE trades ADD COLUMN target_reason TEXT")
        conn.execute("ALTER TABLE trades ADD COLUMN trigger_score REAL DEFAULT 0")
        conn.execute("ALTER TABLE trades ADD COLUMN current_R REAL DEFAULT 0")
        conn.execute("ALTER TABLE trades ADD COLUMN distance_to_sl REAL DEFAULT 0")
        conn.execute("ALTER TABLE trades ADD COLUMN distance_to_tp1 REAL DEFAULT 0")
        conn.execute("ALTER TABLE trades ADD COLUMN distance_to_tp2 REAL DEFAULT 0")
        conn.execute("ALTER TABLE trades ADD COLUMN distance_to_tp3 REAL DEFAULT 0")
    except Exception as e:
        print(f"Trades migration notları (veya zaten var): {e}")

    # coin_library tablosuna eksik alanlar
    try:
        conn.execute("ALTER TABLE coin_library ADD COLUMN volume_24h REAL DEFAULT 0")
        conn.execute("ALTER TABLE coin_library ADD COLUMN avg_volume_7d REAL DEFAULT 0")
        conn.execute("ALTER TABLE coin_library ADD COLUMN spread_pct REAL DEFAULT 0")
        conn.execute("ALTER TABLE coin_library ADD COLUMN funding_rate REAL DEFAULT 0")
        conn.execute("ALTER TABLE coin_library ADD COLUMN open_interest REAL DEFAULT 0")
        conn.execute("ALTER TABLE coin_library ADD COLUMN oi_change_15m REAL DEFAULT 0")
        conn.execute("ALTER TABLE coin_library ADD COLUMN oi_change_1h REAL DEFAULT 0")
        conn.execute("ALTER TABLE coin_library ADD COLUMN atr_1m REAL DEFAULT 0")
        conn.execute("ALTER TABLE coin_library ADD COLUMN atr_5m REAL DEFAULT 0")
        conn.execute("ALTER TABLE coin_library ADD COLUMN atr_15m REAL DEFAULT 0")
        conn.execute("ALTER TABLE coin_library ADD COLUMN volatility_24h REAL DEFAULT 0")
        conn.execute("ALTER TABLE coin_library ADD COLUMN listing_age INTEGER DEFAULT 0")
        conn.execute("ALTER TABLE coin_library ADD COLUMN manipulation_score REAL DEFAULT 0")
        conn.execute("ALTER TABLE coin_library ADD COLUMN danger_score REAL DEFAULT 0")
        conn.execute("ALTER TABLE coin_library ADD COLUMN tradeability_score REAL DEFAULT 0")
    except Exception as e:
        print(f"Coin library migration notları (veya zaten var): {e}")

    # signal_candidates
    try:
        conn.execute("ALTER TABLE signal_candidates ADD COLUMN reject_reason TEXT")
    except Exception:
        pass

    conn.commit()
    conn.close()
    print("Migration tamamlandı.")

if __name__ == '__main__':
    migrate()
