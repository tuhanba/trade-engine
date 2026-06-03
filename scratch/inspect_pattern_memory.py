import sqlite3

conn = sqlite3.connect("trading.db")
c = conn.cursor()

try:
    c.execute("SELECT COUNT(*) FROM pattern_memory")
    count = c.fetchone()[0]
    print(f"Total rows in pattern_memory: {count}")
    
    c.execute("SELECT result, prev_result, COUNT(*) FROM pattern_memory GROUP BY result, prev_result")
    print("\nResult vs PrevResult counts:")
    for row in c.fetchall():
        print(f"  Result={row[0]}, PrevResult={row[1]} => Count={row[2]}")
        
    c.execute("SELECT * FROM pattern_memory LIMIT 5")
    columns = [desc[0] for desc in c.description]
    print("\nFirst 5 rows:")
    for row in c.fetchall():
        print(dict(zip(columns, row)))
except Exception as e:
    print("Error:", e)
finally:
    conn.close()
