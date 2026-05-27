import sqlite3
import pandas as pd
import os

DB_PATH = '/home/ubuntu/trade-engine/trade_engine.db'

def export_signals():
    if not os.path.exists(DB_PATH):
        print("Veritabanı bulunamadı.")
        return

    conn = sqlite3.connect(DB_PATH)
    
    # Sinyalleri ve sonuçlarını çek
    query = """
    SELECT 
        sc.symbol,
        sc.direction,
        sc.setup_quality,
        sc.final_score,
        pr.max_favorable_excursion as mfe,
        pr.max_adverse_excursion as mae,
        pr.would_have_won,
        pr.status as result_status,
        sc.created_at
    FROM signal_candidates sc
    LEFT JOIN paper_results pr ON sc.uuid = pr.signal_id
    ORDER BY sc.created_at DESC
    """
    df = pd.read_sql_query(query, conn)
    
    output_file = 'past_signals_report.csv'
    df.to_csv(output_file, index=False)
    print(f"Toplam {len(df)} sinyal '{output_file}' dosyasına aktarıldı.")
    
    # Konsola kısa bir özet bas
    print("\n--- Son 10 Sinyal Özeti ---")
    print(df.head(10).to_string(index=False))
    
    conn.close()

if __name__ == "__main__":
    export_signals()
