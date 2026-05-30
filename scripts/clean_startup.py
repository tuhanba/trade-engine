import sqlite3
import os

DB_PATH = os.path.join(os.path.dirname(os.path.dirname(__file__)), 'trading.db')

def clean_stuck_trades():
    if not os.path.exists(DB_PATH):
        print(f"DB bulunamadı: {DB_PATH}")
        return

    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()

    print("Veritabanı temizliği başlatılıyor...")

    # Kapatılmamış açık işlemleri force-close yapalım ki yeni motor kafası karışmasın
    cursor.execute("UPDATE trades SET status = 'closed', exit_reason = 'system_reset' WHERE status = 'open'")
    trades_closed = cursor.rowcount
    print(f"- {trades_closed} adet havada kalmış açık işlem zorla kapatıldı (Zombi işlemler temizlendi).")

    # Paper results tablosundaki havada kalan pending sonuçları iptal edelim
    cursor.execute("UPDATE paper_results SET status = 'finalized', exit_reason = 'system_reset' WHERE status = 'pending'")
    paper_closed = cursor.rowcount
    print(f"- {paper_closed} adet havada kalmış sanal (paper) işlem kapatıldı.")

    # İşleme girmemiş, eski sinyalleri silelim
    cursor.execute("DELETE FROM signal_candidates WHERE status IN ('pending', 'processing')")
    signals_deleted = cursor.rowcount
    print(f"- {signals_deleted} adet eski/bekleyen sinyal silindi.")

    conn.commit()
    conn.close()
    
    print("\n✅ Temizlik Tamamlandı! AI'ın öğrendiği geçmiş veriler (Kâr/Zarar oranları) KORUNDU.")
    print("Artık yeni motoru tertemiz bir şekilde başlatabilirsiniz.")

if __name__ == "__main__":
    clean_stuck_trades()
