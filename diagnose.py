import sqlite3
conn = sqlite3.connect('/root/trade_engine/trading.db')
c = conn.cursor()

print("=== WIN/LOSS RR ===")
c.execute("""SELECT status, COUNT(*), ROUND(AVG(r_multiple),2),
ROUND(AVG(net_pnl),3) FROM trades
WHERE status IN ('WIN','LOSS') GROUP BY status""")
for r in c.fetchall(): print(r)

print("\n=== OUTCOME LABELS ===")
c.execute("SELECT quality, COUNT(*) FROM outcome_labels GROUP BY quality")
for r in c.fetchall(): print(r)

print("\n=== SON 10 TRADE ===")
c.execute("""SELECT symbol, status, ROUND(r_multiple,2),
ROUND(net_pnl,3) FROM trades
WHERE status IN ('WIN','LOSS')
ORDER BY close_time DESC LIMIT 10""")
for r in c.fetchall(): print(r)
conn.close()
