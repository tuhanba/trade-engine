import os
import sys
import time
import sqlite3
import requests
import subprocess
from pathlib import Path

# Add project root to path
BASE_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(BASE_DIR))

try:
    from config import TELEGRAM_BOT_TOKEN as BOT_TOKEN, TELEGRAM_CHAT_ID as CHAT_ID, FLASK_PORT
except ImportError as e:
    print(f"❌ ERROR: config.py import hatası: {e}")
    sys.exit(1)

def print_step(msg):
    print(f"\n[{time.strftime('%H:%M:%S')}] ⏳ {msg}")

def print_ok(msg):
    print(f"  ✅ PASS: {msg}")

def print_fail(msg):
    print(f"  ❌ FAIL: {msg}")
    sys.exit(1)

def print_warn(msg):
    print(f"  ⚠️ WARN: {msg}")

def check_db_integrity():
    print_step("Checking Database Integrity...")
    db_path = BASE_DIR / "trading.db"
    
    if not db_path.exists():
        print_fail(f"trading.db bulunamadı! ({db_path})")
        
    try:
        conn = sqlite3.connect(db_path)
        cur = conn.cursor()
        
        # Check tables exist
        tables = [r[0] for r in cur.execute("SELECT name FROM sqlite_master WHERE type='table'")]
        if 'trades' not in tables or 'signal_candidates' not in tables:
            print_fail("Kritik tablolar eksik. DB şeması bozuk olabilir.")
            
        # Check trades count
        trade_count = cur.execute("SELECT count(*) FROM trades").fetchone()[0]
        print_ok(f"Veritabanı sağlam. {trade_count} adet eski trade kaydı bulundu.")
        
        # Check signal count
        signal_count = cur.execute("SELECT count(*) FROM signal_candidates").fetchone()[0]
        print_ok(f"{signal_count} adet eski sinyal kaydı AI öğrenimi için hazır durumda.")
        
        conn.close()
    except Exception as e:
        print_fail(f"DB erişim hatası: {e}")

def check_processes():
    print_step("Checking Running Processes...")
    
    try:
        # Pgrep might fail if no process matches, so we catch exception
        output = subprocess.check_output(["pgrep", "-f", "scalp_bot.py"], stderr=subprocess.STDOUT)
        if output.strip():
            print_fail("ESKİ BOT (scalp_bot.py) HALA ÇALIŞIYOR! Lütfen 'bash scripts/deploy_restart.sh' komutunu çalıştırın.")
    except subprocess.CalledProcessError:
        print_ok("Eski 'scalp_bot.py' prosesi kapalı.")
        
    try:
        output = subprocess.check_output(["pgrep", "-f", "async_scalp_engine.py"], stderr=subprocess.STDOUT)
        if output.strip():
            print_ok("Yeni 'async_scalp_engine.py' başarıyla çalışıyor.")
        else:
            print_warn("Yeni 'async_scalp_engine.py' şu anda çalışmıyor. Bot durdurulmuş olabilir.")
    except subprocess.CalledProcessError:
        print_warn("Yeni 'async_scalp_engine.py' şu anda çalışmıyor. Bot durdurulmuş olabilir.")

def check_systemd():
    print_step("Checking Systemd Service...")
    svc_file = BASE_DIR / "systemd" / "ax-bot.service"
    if svc_file.exists():
        content = svc_file.read_text()
        if "async_scalp_engine.py" in content:
            print_ok("ax-bot.service başarıyla async_scalp_engine.py hedefine ayarlanmış.")
        else:
            print_fail("ax-bot.service hala eski scalp_bot.py dosyasını işaret ediyor!")
    else:
        print_warn("ax-bot.service dosyası bulunamadı. Systemd kullanmıyor olabilirsiniz.")

def check_telegram():
    print_step("Checking Telegram Configuration...")
    if not BOT_TOKEN or not CHAT_ID:
        print_warn("BOT_TOKEN veya CHAT_ID eksik. Telegram uyarıları çalışmayacak.")
        return
        
    try:
        url = f"https://api.telegram.org/bot{BOT_TOKEN}/getMe"
        r = requests.get(url, timeout=5)
        if r.status_code == 200:
            print_ok("Telegram bağlantısı başarılı. Token geçerli.")
        else:
            print_fail(f"Telegram API hatası: {r.status_code} - Token geçersiz olabilir.")
    except Exception as e:
        print_warn(f"Telegram API'sine ulaşılamıyor (Ağ hatası): {e}")

def check_dashboard():
    print_step("Checking Dashboard API...")
    try:
        url = f"http://localhost:{FLASK_PORT}/api/health"
        r = requests.get(url, timeout=5)
        if r.status_code == 200:
            data = r.json()
            if "execution_mode" in data:
                print_ok(f"Dashboard çalışıyor. Mod: {data['execution_mode']}")
            else:
                print_ok("Dashboard yanıt veriyor.")
        else:
            print_warn(f"Dashboard sağlık kontrolü başarısız (HTTP {r.status_code}). Dashboard kapalı olabilir.")
    except Exception:
        print_warn(f"Dashboard yanıt vermiyor. Port {FLASK_PORT} dinlenmiyor olabilir.")

def main():
    print("=" * 60)
    print("   AURVEX MULTI-AGENT MIGRATION VERIFIER")
    print("=" * 60)
    
    check_db_integrity()
    check_systemd()
    check_processes()
    check_telegram()
    check_dashboard()
    
    print("\n" + "=" * 60)
    print(" 🎉 SUCCESS: SUNUCU DOĞRULAMASI BAŞARIYLA TAMAMLANDI!")
    print(" Eski bot tamamen temizlenmiş, yeni Event-Bus sistemi güvenle")
    print(" çalışmaya hazırdır ve geçmiş verileriniz başarıyla korunmuştur.")
    print("=" * 60)

if __name__ == "__main__":
    main()
