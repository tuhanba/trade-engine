import sqlite3
import os

db_path = "trading.db"
if os.path.exists(db_path):
    conn = sqlite3.connect(db_path)
    try:
        cursor = conn.cursor()
        cursor.execute("DELETE FROM pattern_memory WHERE pattern_hash LIKE 'mock_hash_%' OR pattern_hash = 'test_hash_val'")
        conn.commit()
        print(f"Successfully cleaned up {cursor.rowcount} mock rows from pattern_memory in {db_path}.")
    except Exception as e:
        print("Error during clean up:", e)
    finally:
        conn.close()
else:
    print(f"{db_path} does not exist.")
