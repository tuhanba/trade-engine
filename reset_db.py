"""
AURVEX Bot — Trade Verisi Sıfırlama Scripti
Sadece trade geçmişini temizler, bakiyeyi 250'ye sıfırlar.
AX'in öğrenilmiş kişiliği KORUNUR:
  ✓ params, best_params  — öğrenilmiş parametreler
  ✓ coin_profile         — coin kişilikleri
  ✓ coin_cooldown        — soğuma listesi
  ✓ ai_logs              — AX analiz geçmişi

Kullanım: python3 /root/trade_engine/reset_db.py
"""
import sqlite3
import os

DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "trading.db")
STARTING_BALANCE = 250.0

print(f"[reset] DB yolu: {DB_PATH}")

conn = sqlite3.connect(DB_PATH)
c = conn.cursor()

c.execute("SELECT name FROM sqlite_master WHERE type='table'")
tables = [row[0] for row in c.fetchall()]
print(f"[reset] Bulunan tablolar: {tables}")

# Sadece trade kaynaklı veriler temizlenir
TRADE_TABLES = [
    "trades",
    "open_trades",
    "trade_postmortem",
    "trade_analysis",
    "pattern_memory",
    "daily_summary",
    "daily_stats",
    "trade_log",
]

cleared = []
for tbl in TRADE_TABLES:
    if tbl in tables:
        c.execute(f"DELETE FROM {tbl}")
        cleared.append(tbl)

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

print(f"\n[reset] TAMAMLANDI — {STARTING_BALANCE} USDT ile temiz başlangıç.")
print("[reset] AX kişiliği KORUNDU (params, coin_profile, ai_logs).")
