with open('database.py', 'r', encoding='utf-8') as f:
    content = f.read()

replacement = """
            CREATE TABLE IF NOT EXISTS best_params (
                id              INTEGER PRIMARY KEY AUTOINCREMENT,
                version         INTEGER DEFAULT 1,
                rsi_period      INTEGER DEFAULT 14,
                macd_fast       INTEGER DEFAULT 12,
                macd_slow       INTEGER DEFAULT 26,
                macd_signal     INTEGER DEFAULT 9,
                bb_period       INTEGER DEFAULT 20,
                bb_std          REAL DEFAULT 2.0,
                atr_period      INTEGER DEFAULT 14,
                sl_atr_multi    REAL DEFAULT 1.5,
                tp_atr_multi    REAL DEFAULT 2.5,
                score           REAL DEFAULT 0,
                created_at      TEXT
            );

            CREATE TABLE IF NOT EXISTS pattern_memory (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                pattern_hash TEXT,
                win_rate REAL,
                occurrences INTEGER,
                last_seen TEXT
            );
        \"\"\"
        conn.executescript(schema)
"""
if 'CREATE TABLE IF NOT EXISTS best_params' not in content:
    content = content.replace('\"\"\"\n        conn.executescript(schema)', replacement)
    with open('database.py', 'w', encoding='utf-8') as f:
        f.write(content)
    print('Added missing tables.')
