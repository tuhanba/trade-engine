"""
scripts/audit_system.py — AX Final Audit v4.12
==============================================
Aşama 12: Blocker kontrolü ve sistem temizliği.
"""
import os
import sys
import sqlite3

# Ana dizini ekle
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from config import DB_PATH

def run_audit():
    errors = 0
    print("🔍 AX Final Audit Başlatılıyor...")
    
    # 1. DB Kontrolü
    if not os.path.exists(DB_PATH):
        print("❌ HATA: Veritabanı dosyası bulunamadı!")
        errors += 1
    else:
        print("✅ Veritabanı dosyası mevcut.")
        
    # 2. Tablo Kontrolü
    try:
        conn = sqlite3.connect(DB_PATH)
        cursor = conn.cursor()
        tables = ["trades", "balance_ledger", "paper_account", "ai_logs"]
        for t in tables:
            cursor.execute(f"SELECT name FROM sqlite_master WHERE type='table' AND name='{t}'")
            if not cursor.fetchone():
                print(f"❌ HATA: '{t}' tablosu eksik!")
                errors += 1
        conn.close()
    except Exception as e:
        print(f"❌ HATA: DB bağlantı hatası: {e}")
        errors += 1

    # 3. Modül Kontrolü
    modules = [
        "core/accounting.py",
        "core/advanced_risk_engine.py",
        "core/ai_decision_engine.py",
        "core/coin_library.py",
        "core/trailing_engine.py"
    ]
    for m in modules:
        if not os.path.exists(m):
            print(f"❌ HATA: '{m}' modülü eksik!")
            errors += 1
            
    if errors == 0:
        print("\n🏆 AUDIT TEMİZ: Sistem 12/14 aşamaya hazır!")
    else:
        print(f"\n⚠️ AUDIT BAŞARISIZ: {errors} adet blocker bulundu.")
    
    return errors == 0

if __name__ == "__main__":
    run_audit()
