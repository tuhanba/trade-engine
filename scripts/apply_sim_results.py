import sqlite3
import pandas as pd
import sys
import os

# Ana dizini path'e ekle
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from config import DB_PATH

def apply_results():
    print(f"Veritabanı: {DB_PATH}")
    if not os.path.exists(DB_PATH):
        print("HATA: Veritabanı dosyası bulunamadı.")
        return

    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()
    
    # 1. Simülasyon Verilerini Oku (CSV'den veya tekrar hesapla)
    # Burada scriptin bağımsız çalışması için CSV'den okuyoruz
    csv_path = 'coin_optimization_results.csv'
    if not os.path.exists(csv_path):
        print("HATA: 'coin_optimization_results.csv' bulunamadı. Önce optimizasyonu çalıştırın.")
        return
        
    df = pd.read_csv(csv_path)
    
    print(f"Toplam {len(df)} coin için sonuçlar uygulanıyor...")

    for _, row in df.iterrows():
        symbol = row['symbol']
        wr = row['win_rate']
        sl = row['best_sl']
        tp = row['best_tp']
        sample = row['sample_size']
        
        # 2. coin_profiles tablosunu güncelle
        # Not: Şemada best_sl/best_tp kolonları olmayabilir, onları win_rate ve sample_size üzerinden güncelliyoruz
        # Ayrıca fakeout_rate ve danger_score gibi alanları WR'ye göre manipüle edebiliriz
        
        danger_score = 0.0
        if wr < 0.30:
            danger_score = 0.9 # Cooldown'a sok
        elif wr < 0.50:
            danger_score = 0.5
            
        cursor.execute("""
            INSERT INTO coin_profiles (symbol, win_rate, sample_size, danger_score, last_updated)
            VALUES (?, ?, ?, ?, datetime('now'))
            ON CONFLICT(symbol) DO UPDATE SET
                win_rate = excluded.win_rate,
                sample_size = excluded.sample_size,
                danger_score = excluded.danger_score,
                last_updated = excluded.last_updated
        """, (symbol, wr, int(sample), danger_score))

    conn.commit()
    print("coin_profiles tablosu güncellendi.")
    
    # 3. Filtreleme Özeti
    cursor.execute("SELECT symbol FROM coin_profiles WHERE danger_score > 0.8")
    blocked = [r[0] for r in cursor.fetchall()]
    print(f"\nFiltrelenen (Engellenen) Coinler (WR < %30): {blocked}")
    
    conn.close()

if __name__ == "__main__":
    apply_results()
