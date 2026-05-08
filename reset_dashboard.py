"""
reset_dashboard.py — Dashboard + Bakiye Sıfırlama
Çalıştır: python3 reset_dashboard.py
AI Brain ve pattern_memory korunur, sadece trade/sinyal verileri temizlenir.
"""
import sqlite3
import os
import json

DB_PATH    = os.environ.get("DB_PATH", "/root/trade_engine/trading.db")
STATE_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "paper_state.json")

print(f"DB: {DB_PATH}")

conn = sqlite3.connect(DB_PATH)

tables = [
    "trades",
    "signal_candidates",
    "scalp_signals",
    "signal_events",
    "trade_postmortem",
    "daily_summary",
    "weekly_summary",
    "dashboard_snapshots",
    "best_params",
    "params",
    "coin_cooldown",
    "paper_results",
    "telegram_messages",
]

for t in tables:
    try:
        conn.execute(f"DELETE FROM {t}")
        print(f"  OK: {t} temizlendi")
    except Exception as e:
        print(f"  SKIP: {t} — {e}")

# Bakiye 1000 USDT
try:
    conn.execute("DELETE FROM paper_account")
    conn.execute(
        "INSERT INTO paper_account (id, balance, initial_balance) "
        "VALUES (1, 1000.0, 1000.0)"
    )
    print("  OK: paper_account → 1000 USDT")
except Exception as e:
    print(f"  HATA: paper_account — {e}")

# System state sıfırla
try:
    conn.execute("DELETE FROM system_state")
    print("  OK: system_state temizlendi")
except Exception as e:
    print(f"  SKIP: system_state — {e}")

conn.commit()
conn.close()

# paper_state.json sıfırla
try:
    empty = {"balance": 1000.0, "initial_balance": 1000.0, "open_trades": [], "closed_trades": []}
    with open(STATE_FILE, "w") as f:
        json.dump(empty, f, indent=2)
    print("  OK: paper_state.json sıfırlandı")
except Exception as e:
    print(f"  SKIP: paper_state.json — {e}")

print("\nTAMAMLANDI — Bakiye 1000 USDT, tüm pozisyonlar temizlendi.")
print("Şimdi: systemctl restart aurvex-bot aurvex-dashboard")
