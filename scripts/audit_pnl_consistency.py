import sqlite3
import os

DB_PATH = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "trading.db")

def audit_pnl():
    print(f"PnL Consistency Audit başlatılıyor...")
    try:
        conn = sqlite3.connect(DB_PATH)
        conn.row_factory = sqlite3.Row
        
        trades = conn.execute("SELECT id, status, realized_pnl, net_pnl, open_fee, close_fee FROM trades WHERE status='closed'").fetchall()
        
        inconsistencies = 0
        for t in trades:
            # Net PnL = Realized PnL - (open_fee + close_fee) mantığı
            # Hesaplama ufak yuvarlama farklarına sahip olabilir.
            expected_net = t["realized_pnl"] - t["open_fee"] - t["close_fee"]
            actual_net = t["net_pnl"]
            
            if abs(expected_net - actual_net) > 0.1:
                print(f"TUTARSIZLIK BULUNDU: Trade ID {t['id']} | Expected Net: {expected_net:.4f} | Actual Net: {actual_net:.4f}")
                inconsistencies += 1
                
        if inconsistencies == 0:
            print("Tüm PnL hesaplamaları tutarlı. (0 Hata)")
            
        conn.close()
    except Exception as e:
        print(f"Audit hatası: {e}")

if __name__ == '__main__':
    audit_pnl()
