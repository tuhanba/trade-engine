import sqlite3
import pandas as pd
import sys
import os

# Ana dizini path'e ekle
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from config import DB_PATH

def run_simulation():
    print(f"Veritabanı: {DB_PATH}")
    if not os.path.exists(DB_PATH):
        print("HATA: Veritabanı dosyası bulunamadı.")
        return

    conn = sqlite3.connect(DB_PATH)
    
    # 1. Verileri Çek
    # paper_results ve signal_candidates tablolarını birleştirerek sonuçları al
    query = """
    SELECT 
        sc.symbol,
        sc.setup_quality,
        sc.final_score,
        pr.max_favorable_excursion as mfe,
        pr.max_adverse_excursion as mae,
        pr.would_have_won
    FROM signal_candidates sc
    JOIN paper_results pr ON sc.uuid = pr.signal_id
    WHERE pr.status = 'finalized'
    """
    df = pd.read_sql_query(query, conn)
    
    if df.empty:
        print("UYARI: Simülasyon için yeterli finalized veri bulunamadı.")
        # Test için sahte veri oluşturma (Opsiyonel - Eğer DB boşsa)
        return

    print(f"Toplam {len(df)} sinyal analiz ediliyor...")

    # 2. Coin Bazlı Analiz
    coin_stats = []
    
    # Parametre Uzayı
    sl_options = [1.0, 1.2, 1.5, 1.8, 2.0]
    tp_options = [1.5, 2.0, 2.5, 3.0]
    
    symbols = df['symbol'].unique()
    
    for symbol in symbols:
        symbol_df = df[df['symbol'] == symbol]
        best_wr = 0
        best_params = (1.5, 2.0)
        
        for sl in sl_options:
            for tp in tp_options:
                # Basitleştirilmiş Replay Mantığı:
                # MAE < SL (Stop olmadı) VE MFE >= TP (Hedefe ulaştı)
                wins = len(symbol_df[(symbol_df['mae'] < sl) & (symbol_df['mfe'] >= tp)])
                total = len(symbol_df)
                wr = wins / total if total > 0 else 0
                
                if wr > best_wr:
                    best_wr = wr
                    best_params = (sl, tp)
        
        coin_stats.append({
            'symbol': symbol,
            'sample_size': len(symbol_df),
            'best_sl': best_params[0],
            'best_tp': best_params[1],
            'win_rate': best_wr
        })

    stats_df = pd.DataFrame(coin_stats)
    
    # 3. Filtreleme Uygula
    # Örn: Kazanma oranı %40'ın altında olan veya örneklem sayısı çok düşük olan coinleri filtrele
    filtered_df = stats_df[(stats_df['win_rate'] >= 0.40) & (stats_df['sample_size'] >= 3)]
    
    print("\n--- COİN BAZLI OPTİMİZASYON SONUÇLARI ---")
    print(stats_df.sort_values(by='win_rate', ascending=False).to_string())
    
    print("\n--- ÖNERİLEN FİLTRELER (WR >= %40 & Sample >= 3) ---")
    if filtered_df.empty:
        print("Filtre kriterlerine uyan coin bulunamadı.")
    else:
        print(filtered_df.to_string())
        
    # 4. Sonuçları Kaydet
    stats_df.to_csv('coin_optimization_results.csv', index=False)
    print("\nSonuçlar 'coin_optimization_results.csv' dosyasına kaydedildi.")
    
    conn.close()

if __name__ == "__main__":
    run_simulation()
