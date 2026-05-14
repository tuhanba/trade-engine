import sqlite3
import random
import uuid
from datetime import datetime, timedelta

DB_PATH = '/home/ubuntu/trade-engine/trade_engine.db'

def generate_data():
    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    
    symbols = ["BTCUSDT", "ETHUSDT", "SOLUSDT", "BNBUSDT", "XRPUSDT", "ADAUSDT", "AVAXUSDT", "DOTUSDT"]
    qualities = ["S", "A+", "A", "B"]
    
    print("Sentetik veri üretiliyor...")
    
    for _ in range(200):
        u = str(uuid.uuid4())
        symbol = random.choice(symbols)
        quality = random.choice(qualities)
        score = random.uniform(60, 95)
        
        # signal_candidates'a ekle (Şemaya uygun kolonlar)
        cursor.execute("""
            INSERT INTO signal_candidates (uuid, symbol, setup_quality, final_score, decision)
            VALUES (?, ?, ?, ?, ?)
        """, (u, symbol, quality, score, 'ALLOW'))
        
        # paper_results'a ekle (finalized)
        if symbol in ["BTCUSDT", "ETHUSDT", "SOLUSDT"]:
            mfe = random.uniform(1.0, 5.0)
            mae = random.uniform(0.1, 1.2)
        else:
            mfe = random.uniform(0.1, 2.0)
            mae = random.uniform(0.5, 3.0)
            
        cursor.execute("""
            INSERT INTO paper_results (signal_id, symbol, max_favorable_excursion, max_adverse_excursion, status, would_have_won)
            VALUES (?, ?, ?, ?, ?, ?)
        """, (u, symbol, mfe, mae, 'finalized', 1 if mfe > 2.0 and mae < 1.5 else 0))
        
    conn.commit()
    conn.close()
    print("200 adet sentetik sinyal ve sonuç verisi oluşturuldu.")

if __name__ == "__main__":
    generate_data()
