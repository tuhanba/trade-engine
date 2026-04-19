"""
AURVEX Bot — Tüm Açık Pozisyonları Kapat
Paper mode: DB'deki açık trade'leri stop fiyatından kapatır (loss olarak işler).
Kullanım: python3 /root/trade_engine/close_all.py

NOT: Botu durdurmadan önce çalıştırın.
"""
import sqlite3
import os
from datetime import datetime, timezone

DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "trading.db")

conn = sqlite3.connect(DB_PATH)
conn.row_factory = sqlite3.Row
c = conn.cursor()

# Açık trade'leri bul
c.execute("SELECT * FROM trades WHERE status = 'OPEN'")
open_trades = c.fetchall()

if not open_trades:
    print("[close_all] Açık pozisyon yok.")
    conn.close()
    exit()

print(f"[close_all] {len(open_trades)} açık pozisyon bulundu.")

now = datetime.now(timezone.utc).isoformat()
closed = 0

for t in open_trades:
    trade_id  = t["id"]
    symbol    = t["symbol"]
    entry     = t["entry"] or 0
    sl        = t["sl"] or 0
    qty       = t["qty"] or 0
    direction = t["direction"]
    # risk kolonu olmayabilir, güvenli oku
    try:
        risk = t["risk"] or 0
    except (IndexError, KeyError):
        risk = abs(entry * qty * 0.01)  # fallback: pozisyonun %1'i

    # Mevcut fiyat yerine SL fiyatından kapat (paper mode)
    close_price = sl if sl > 0 else entry

    if direction == "LONG":
        gross = (close_price - entry) * qty
    else:
        gross = (entry - close_price) * qty

    commission = abs(entry * qty * 0.0004) + abs(close_price * qty * 0.0004)
    net_pnl    = gross - commission
    r_multiple = round(net_pnl / (risk + 1e-10), 3) if risk else 0
    result     = "WIN" if net_pnl > 0 else "LOSS"

    c.execute("""
        UPDATE trades
        SET status=?, close_price=?, close_time=?,
            pnl_usdt=?, net_pnl=?, r_multiple=?, result=?
        WHERE id=?
    """, (result, close_price, now, round(gross, 4), round(net_pnl, 4), r_multiple, result, trade_id))

    print(f"  ✓ {symbol} {direction} kapatıldı | PNL: {net_pnl:.3f}$ | {result}")
    closed += 1

conn.commit()
conn.close()

print(f"\n[close_all] {closed} pozisyon kapatıldı.")
print("[close_all] Şimdi reset_db.py veya restart.sh çalıştırabilirsiniz.")
