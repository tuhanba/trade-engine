"""
AURVEX Bot — Trade Verisi Sıfırlama Scripti
Sadece trade geçmişini temizler, bakiyeyi 250'ye sıfırlar.

AX'in öğrenilmiş zekası KORUNUR:
  ✓ params, best_params      — öğrenilmiş parametreler
  ✓ coin_profile             — coin kişilikleri
  ✓ coin_cooldown            — soğuma listesi
  ✓ ai_logs                  — AX analiz geçmişi

Temizlenen (trade verisi):
  ✗ trades                   — geçmiş tradeler
  ✗ open_trades              — açık pozisyonlar
  ✗ pattern_memory           — ML eğitim verisi
  ✗ trade_postmortem         — trade otopsisi
  ✗ outcome_labels           — outcome etiketleri
  ✗ counterfactual_analysis  — counterfactual analizi
  ✗ trade_analysis / daily   — günlük istatistikler
  ✗ bot_control              — kontrol durumu sıfırlanır

Kullanım: python3 /root/trade_engine/reset_db.py
"""
import sqlite3, os

DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "trading.db")
STARTING_BALANCE = 250.0

print(f"[reset] DB: {DB_PATH}")

conn = sqlite3.connect(DB_PATH)
c = conn.cursor()

c.execute("SELECT name FROM sqlite_master WHERE type='table'")
tables = [row[0] for row in c.fetchall()]

TRADE_TABLES = [
    "trades", "open_trades", "pattern_memory",
    "trade_postmortem", "trade_analysis",
    "daily_summary", "daily_stats", "trade_log",
    "outcome_labels", "counterfactual_analysis",
]

cleared = []
for tbl in TRADE_TABLES:
    if tbl in tables:
        c.execute(f"DELETE FROM {tbl}")
        cleared.append(tbl)

# Paper bakiye sıfırla
if "paper_account" in tables:
    c.execute("DELETE FROM paper_account")
    c.execute(
        "INSERT INTO paper_account (id, paper_balance, updated_at) "
        "VALUES (1, ?, datetime('now'))",
        (STARTING_BALANCE,)
    )
    cleared.append(f"paper_account → {STARTING_BALANCE} USDT")

# Bot kontrolü sıfırla (pause/finish mode temizle)
if "bot_control" in tables:
    c.execute("UPDATE bot_control SET paused=0, finish_mode=0, updated_at=datetime('now'), updated_by='reset' WHERE id=1")
    cleared.append("bot_control → aktif")

conn.commit()
conn.close()

print(f"\n[reset] Temizlenen tablolar:")
for t in cleared:
    print(f"  ✗ {t}")

print(f"\n[reset] KORUNAN (AX zekası):")
for t in ["params", "best_params", "coin_profile", "coin_cooldown", "ai_logs"]:
    if t in tables:
        print(f"  ✓ {t}")

print(f"\n[reset] TAMAMLANDI — AX {STARTING_BALANCE} USDT ile yeni hayatına başlıyor.")
