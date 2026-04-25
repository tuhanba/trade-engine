
import sqlite3
import json
import random
from datetime import datetime, timedelta, timezone
from config import DB_PATH

def get_conn():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn

def seed():
    with get_conn() as conn:
        # 1. Paper Account
        conn.execute("INSERT OR IGNORE INTO paper_account (id, balance, initial_balance) VALUES (1, 1250.50, 1000.0)")
        
        # 2. Params
        params_data = {
            "sl_atr_mult": 1.5, "tp_atr_mult": 2.5,
            "rsi5_min": 40, "rsi5_max": 70,
            "rsi1_min": 40, "rsi1_max": 68,
            "vol_ratio_min": 1.8, "min_volume_m": 5.0,
            "min_change_pct": 1.5, "risk_pct": 1.0, "version": 4
        }
        conn.execute("INSERT INTO params (data, reason) VALUES (?, ?)", (json.dumps(params_data), "Initial seed"))
        
        # 3. Signal Candidates & Trades
        symbols = ["BTCUSDT", "ETHUSDT", "SOLUSDT", "BNBUSDT", "AVAXUSDT"]
        sessions = ["ASIA", "LONDON", "NEW_YORK", "OFF"]
        regimes = ["TRENDING", "RANGING", "VOLATILE"]
        
        now = datetime.now(timezone.utc)
        
        for i in range(50):
            symbol = random.choice(symbols)
            session = random.choice(sessions)
            regime = random.choice(regimes)
            direction = random.choice(["LONG", "SHORT"])
            
            # Signal Candidate
            cursor = conn.execute("""
                INSERT INTO signal_candidates (symbol, direction, session, market_regime, decision, created_at)
                VALUES (?, ?, ?, ?, 'ALLOW', ?)
            """, (symbol, direction, session, regime, (now - timedelta(days=random.randint(0, 30), hours=random.randint(0, 23))).isoformat()))
            candidate_id = cursor.lastrowid
            
            # Trade
            status = "closed"
            pnl = random.uniform(-20, 50)
            r_multiple = pnl / 10.0
            open_time = (now - timedelta(days=random.randint(0, 30), hours=random.randint(0, 23)))
            close_time = open_time + timedelta(minutes=random.randint(10, 300))
            
            conn.execute("""
                INSERT INTO trades (symbol, direction, status, entry, close_price, net_pnl, r_multiple, linked_candidate_id, open_time, close_time, hold_minutes, close_reason)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (symbol, direction, status, 50000, 50100, pnl, r_multiple, candidate_id, open_time.isoformat(), close_time.isoformat(), (close_time - open_time).total_seconds() / 60, "TP"))

        # 4. Vetoed Signals
        veto_reasons = ["RSI_OVERBOUGHT", "LOW_VOLUME", "TREND_MISMATCH", "VOLATILITY_HIGH"]
        for i in range(20):
            conn.execute("""
                INSERT INTO signal_candidates (symbol, direction, session, market_regime, decision, veto_reason, created_at)
                VALUES (?, ?, ?, ?, 'VETOED', ?, ?)
            """, (random.choice(symbols), random.choice(["LONG", "SHORT"]), random.choice(sessions), random.choice(regimes), random.choice(veto_reasons), (now - timedelta(days=random.randint(0, 30))).isoformat()))

        # 5. System State
        states = [
            ("bot_loop_status", "ACTIVE"),
            ("telegram_status", "OK"),
            ("dashboard_status", "OK"),
            ("n8n_status", "OK"),
            ("execution_mode", "paper")
        ]
        for key, val in states:
            conn.execute("INSERT OR REPLACE INTO system_state (key, value, updated_at) VALUES (?, ?, ?)", (key, val, now.isoformat()))

        conn.commit()
    print("Seed data created successfully.")

if __name__ == "__main__":
    seed()
