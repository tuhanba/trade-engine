import sqlite3
conn = sqlite3.connect('/root/trade_engine/trading.db')
c = conn.cursor()

c.execute("SELECT version, tp_atr_mult, sl_atr_mult, risk_pct FROM params ORDER BY version DESC LIMIT 1")
row = c.fetchone()
print(f"Mevcut: v{row[0]} | TP:{row[1]} | SL:{row[2]} | Risk:{row[3]}")

new_version = row[0] + 1
c.execute("""
    INSERT INTO params (version, sl_atr_mult, tp_atr_mult,
    rsi5_min, rsi5_max, rsi1_min, rsi1_max,
    vol_ratio_min, min_volume_m, min_change_pct,
    risk_pct, updated_at, ai_reason)
    SELECT ?, sl_atr_mult, 2.8,
    rsi5_min, rsi5_max, rsi1_min, rsi1_max,
    vol_ratio_min, min_volume_m, min_change_pct,
    risk_pct, datetime('now'),
    'TP 2.0 -> 2.8: avg win 1.25R cok dusuk, expectancy negatif'
    FROM params ORDER BY version DESC LIMIT 1
""", (new_version,))
conn.commit()

c.execute("SELECT version, tp_atr_mult FROM params ORDER BY version DESC LIMIT 1")
row = c.fetchone()
print(f"Yeni: v{row[0]} | TP:{row[1]}")
conn.close()
