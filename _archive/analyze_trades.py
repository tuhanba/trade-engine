import sqlite3

conn = sqlite3.connect('/root/trade_engine/trading.db')
cur = conn.cursor()

# Coin bazlı win rate
cur.execute("""
SELECT symbol, COUNT(*) total,
       SUM(CASE WHEN result='WIN' THEN 1 ELSE 0 END) wins,
       ROUND(100.0*SUM(CASE WHEN result='WIN' THEN 1 ELSE 0 END)/COUNT(*),1) wr_pct,
       ROUND(AVG(net_pnl),3) avg_pnl,
       ROUND(SUM(net_pnl),3) total_pnl
FROM trades WHERE result IS NOT NULL
GROUP BY symbol ORDER BY total DESC LIMIT 20
""")
print('=== COIN BAZLI (total, wins, WR%, avg_pnl, total_pnl) ===')
for r in cur.fetchall():
    print(r)

# Saat bazlı win rate
cur.execute("""
SELECT CAST(strftime('%H', open_time) AS INT) hour,
       COUNT(*) total,
       SUM(CASE WHEN result='WIN' THEN 1 ELSE 0 END) wins,
       ROUND(100.0*SUM(CASE WHEN result='WIN' THEN 1 ELSE 0 END)/COUNT(*),1) wr_pct,
       ROUND(AVG(net_pnl),3) avg_pnl
FROM trades WHERE result IS NOT NULL
GROUP BY hour ORDER BY hour
""")
print('\n=== SAAT BAZLI (UTC saat, total, wins, WR%, avg_pnl) ===')
for r in cur.fetchall():
    print(r)

# Direction bazlı
cur.execute("""
SELECT direction, COUNT(*) total,
       SUM(CASE WHEN result='WIN' THEN 1 ELSE 0 END) wins,
       ROUND(100.0*SUM(CASE WHEN result='WIN' THEN 1 ELSE 0 END)/COUNT(*),1) wr_pct,
       ROUND(AVG(net_pnl),3) avg_pnl
FROM trades WHERE result IS NOT NULL GROUP BY direction
""")
print('\n=== DIRECTION (LONG vs SHORT) ===')
for r in cur.fetchall():
    print(r)

# RSI1 zone bazlı
cur.execute("""
SELECT CASE WHEN rsi1<30 THEN 'OVERSOLD(<30)'
            WHEN rsi1>70 THEN 'OVERBOUGHT(>70)'
            ELSE 'NEUTRAL(30-70)' END rsi_zone,
       COUNT(*) total,
       SUM(CASE WHEN result='WIN' THEN 1 ELSE 0 END) wins,
       ROUND(100.0*SUM(CASE WHEN result='WIN' THEN 1 ELSE 0 END)/COUNT(*),1) wr_pct,
       ROUND(AVG(net_pnl),3) avg_pnl
FROM trades WHERE result IS NOT NULL GROUP BY rsi_zone
""")
print('\n=== RSI1 ZONE ===')
for r in cur.fetchall():
    print(r)

# Duration bazlı
cur.execute("""
SELECT CASE WHEN duration_min<2 THEN '<2min'
            WHEN duration_min<5 THEN '2-5min'
            WHEN duration_min<10 THEN '5-10min'
            ELSE '>10min' END dur_bucket,
       COUNT(*) total,
       SUM(CASE WHEN result='WIN' THEN 1 ELSE 0 END) wins,
       ROUND(100.0*SUM(CASE WHEN result='WIN' THEN 1 ELSE 0 END)/COUNT(*),1) wr_pct,
       ROUND(AVG(net_pnl),3) avg_pnl
FROM trades WHERE result IS NOT NULL GROUP BY dur_bucket
""")
print('\n=== DURATION BAZLI ===')
for r in cur.fetchall():
    print(r)

conn.close()
