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
cleared = []

if "trades" in tables:
    c.execute("DELETE FROM trades")
    cleared.append("trades")

if "open_trades" in tables:
    c.execute("DELETE FROM open_trades")
    cleared.append("open_trades")

if "daily_stats" in tables:
    c.execute("DELETE FROM daily_stats")
    cleared.append("daily_stats")

if "trade_log" in tables:
    c.execute("DELETE FROM trade_log")
    cleared.append("trade_log")

# Paper account sıfırla — 250 USDT
if "paper_account" in tables:
    c.execute("DELETE FROM paper_account")
    c.execute(
        "INSERT INTO paper_account (id, paper_balance, total_commission, updated_at) "
        "VALUES (1, ?, 0.0, datetime('now'))",
        (STARTING_BALANCE,)
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
