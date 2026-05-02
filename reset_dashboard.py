"""
reset_dashboard.py — Dashboard sıfırlama scripti
Çalıştır: venv/bin/python3 reset_dashboard.py
AI Brain korunur, sadece trade/dashboard verileri temizlenir.
"""
import sqlite3
import os
import sys

DB_PATH = os.environ.get("DB_PATH", "/root/trade_engine/trading.db")

print(f"DB: {DB_PATH}")

conn = sqlite3.connect(DB_PATH)

# Temizlenecek tablolar (AI Brain korunuyor)
tables = [
    "trades",
    "signal_candidates",
    "scalp_signals",
    "trade_postmortem",
    "daily_summary",
    "weekly_summary",
    "dashboard_snapshots",
    "best_params",
    "params",
    "coin_cooldown",
]

for t in tables:
    try:
        conn.execute(f"DELETE FROM {t}")
        print(f"  OK: {t} temizlendi")
    except Exception as e:
        print(f"  SKIP: {t} — {e}")

# Bakiye sıfırla — 1000 USDT
try:
    conn.execute("DELETE FROM paper_account")
    conn.execute(
        "INSERT INTO paper_account (id, balance, initial_balance) "
        "VALUES (1, 1000.0, 1000.0)"
    )
    print("  OK: paper_account 1000 USDT ayarlandı")
except Exception as e:
    print(f"  HATA: paper_account — {e}")

conn.commit()
conn.close()
print("\nTAMAMLANDI — Dashboard sıfırlandı, bakiye 1000 USDT.")
