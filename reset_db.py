"""
AURVEX Bot — Trade Verisi Sıfırlama Scripti
Sadece trade geçmişini ve bakiyeyi sıfırlar.
AI Brain verileri (params, ai_logs, pattern_memory, ml_training_data) KORUNUR.

Kullanım: python3 /root/trade_engine/reset_db.py
"""
import sqlite3
import os

DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "trading.db")
STARTING_BALANCE = 250.0

print(f"[reset] DB yolu: {DB_PATH}")

conn = sqlite3.connect(DB_PATH)
c = conn.cursor()

# Mevcut tabloları listele
c.execute("SELECT name FROM sqlite_master WHERE type='table'")
tables = [row[0] for row in c.fetchall()]
print(f"[reset] Bulunan tablolar: {tables}")

# Sadece trade verileri temizlenir — AI Brain verileri KORUNUR
# KORUNANLAR: params, best_params, ai_logs, coin_profile, coin_params, coin_cooldown
CLEAR_TABLES = [
    "trades", "signal_candidates", "trade_postmortem",
    "daily_summary", "weekly_summary", "pipeline_stats",
    "coin_market_memory",
    # eski şema isimleri (varsa)
    "open_trades", "daily_stats", "trade_log",
]
cleared = []

for tbl in CLEAR_TABLES:
    if tbl in tables:
        c.execute(f"DELETE FROM {tbl}")
        cleared.append(tbl)

# Paper account sıfırla
if "paper_account" in tables:
    c.execute("DELETE FROM paper_account")
    c.execute(
        "INSERT INTO paper_account (id, balance, initial_balance) "
        "VALUES (1, ?, ?)",
        (STARTING_BALANCE, STARTING_BALANCE)
    )
    cleared.append(f"paper_account → {STARTING_BALANCE} USDT")

conn.commit()
conn.close()

print(f"\n[reset] Temizlenen tablolar:")
for t in cleared:
    print(f"  ✓ {t}")

print(f"\n[reset] TAMAMLANDI — {STARTING_BALANCE} USDT ile temiz başlangıç hazır.")
print("[reset] AI Brain verileri (params, ai_logs, pattern_memory, ml_training_data) KORUNDU.")
print("[reset] Şimdi botu başlatabilirsiniz:")
print("  bash /root/trade_engine/restart.sh")
