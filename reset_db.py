#!/usr/bin/env python3
"""
reset_db.py — AX Sistem Veri Sıfırlama Scripti
================================================
Eski/bozuk verilerden temiz başlangıç yapar.
- Dashboard verileri temizlenir (trades, signals, daily_summary, weekly_summary)
- AI Brain öğrenme verileri KORUNUR (coin_profile, ai_learning, best_params)
- Bakiye $10,000 olarak sıfırlanır
- 92 coin parametreleri yeniden yüklenir

Kullanım: python3 reset_db.py
"""
import sqlite3
import os
import sys
import json
from datetime import datetime, timezone

# Config'den DB yolunu al
try:
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    from config import DB_PATH, COIN_UNIVERSE
except ImportError:
    DB_PATH = "/root/trade_engine/trading.db"
    COIN_UNIVERSE = []

def reset_db():
    db_path = DB_PATH
    
    # DB yoksa yeni oluştur
    if not os.path.exists(os.path.dirname(db_path)):
        os.makedirs(os.path.dirname(db_path), exist_ok=True)
    
    print(f"[RESET] DB: {db_path}")
    
    if not os.path.exists(db_path):
        print("[RESET] DB bulunamadı — init_db çalıştırılıyor...")
        from database import init_db
        init_db()
        from coin_library import init_coin_library
        init_coin_library()
        print("[RESET] Yeni DB oluşturuldu.")
        return

    with sqlite3.connect(db_path) as conn:
        conn.row_factory = sqlite3.Row
        
        # ── Mevcut durumu göster ──────────────────────────────────────────
        print("\n[RESET] Mevcut durum:")
        for tbl in ["trades", "signal_candidates", "scalp_signals", "daily_summary", "weekly_summary"]:
            try:
                cnt = conn.execute(f"SELECT COUNT(*) FROM {tbl}").fetchone()[0]
                print(f"  {tbl}: {cnt} kayıt")
            except Exception:
                print(f"  {tbl}: tablo yok")
        
        print("\n[RESET] Temizleniyor...")
        
        # ── Dashboard verileri temizle ────────────────────────────────────
        tables_to_clear = [
            "trades",
            "signal_candidates",
            "scalp_signals",
            "daily_summary",
            "weekly_summary",
            "dashboard_snapshots",
            "ai_logs",
            "coin_market_memory",
        ]
        for tbl in tables_to_clear:
            try:
                conn.execute(f"DELETE FROM {tbl}")
                print(f"  ✅ {tbl} temizlendi")
            except Exception as e:
                print(f"  ⚠️  {tbl}: {e}")
        
        # ── AI Brain öğrenme verileri KORUNUYOR ──────────────────────────
        kept = ["coin_profile", "ai_learning", "best_params"]
        for tbl in kept:
            try:
                cnt = conn.execute(f"SELECT COUNT(*) FROM {tbl}").fetchone()[0]
                print(f"  🧠 {tbl}: {cnt} kayıt KORUNDU")
            except Exception:
                pass
        
        # ── Bakiye sıfırla ────────────────────────────────────────────────
        try:
            conn.execute("DELETE FROM system_state WHERE key='paper_balance'")
            conn.execute(
                "INSERT INTO system_state (key, value, updated_at) VALUES ('paper_balance', '10000.0', datetime('now'))"
            )
            print("  ✅ Bakiye $10,000 olarak sıfırlandı")
        except Exception as e:
            print(f"  ⚠️  Bakiye sıfırlama: {e}")
        
        # ── Circuit breaker temizle ───────────────────────────────────────
        try:
            conn.execute("DELETE FROM system_state WHERE key='circuit_breaker_until'")
            conn.execute("DELETE FROM system_state WHERE key='paused'")
            print("  ✅ Circuit breaker ve pause temizlendi")
        except Exception as e:
            print(f"  ⚠️  State temizleme: {e}")
        
        # ── Coin parametrelerini yenile ───────────────────────────────────
        try:
            conn.execute("DELETE FROM coin_params")
            print("  ✅ coin_params temizlendi (yeniden yüklenecek)")
        except Exception as e:
            print(f"  ⚠️  coin_params: {e}")
        
        conn.commit()
    
    # Coin library yeniden yükle
    try:
        from coin_library import init_coin_library
        init_coin_library()
        print(f"  ✅ {len(COIN_UNIVERSE)} coin parametresi yüklendi")
    except Exception as e:
        print(f"  ⚠️  coin_library init: {e}")
    
    print(f"\n[RESET] ✅ Tamamlandı — {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}")
    print("[RESET] Sistemi başlatabilirsiniz: python3 scalp_bot.py")

if __name__ == "__main__":
    print("="*50)
    print("AX SİSTEM SIFIRLAMA")
    print("="*50)
    print("Bu işlem trade ve sinyal verilerini siler.")
    print("AI Brain öğrenme verileri KORUNUR.")
    print()
    ans = input("Devam etmek istiyor musunuz? (evet/hayır): ").strip().lower()
    if ans in ("evet", "e", "yes", "y"):
        reset_db()
    else:
        print("İptal edildi.")
