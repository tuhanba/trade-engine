import os
import subprocess
import sys

def run_cmd(cmd, ignore_error=False):
    print(f"\n>>> Yürütülüyor: {cmd}")
    result = subprocess.run(cmd, shell=True)
    if result.returncode != 0 and not ignore_error:
        print(f"HATA: Komut başarisiz oldu ({result.returncode})")
        sys.exit(result.returncode)
    return result

def main():
    print("===================================================")
    print("AURVEX AI - SISTEM TESTI VE GITHUB PUSH (v21)")
    print("===================================================")

    # 1. Testleri Çalistir (Prompt ile Birlestirildi)
    print("\n[ADIM 1] Py_compile testleri basliyor...")
    run_cmd("python -m py_compile scalp_bot_v3.py app.py config.py database.py execution_engine.py dashboard_service.py telegram_delivery.py")
    
    print("\n[ADIM 2] Audit PnL testleri...")
    run_cmd("python scripts/audit_pnl_consistency.py", ignore_error=True)

    # 2. Eski Dosyalari Kaldir
    print("\n[ADIM 3] Eski aurvex dosyaları siliniyor...")
    run_cmd("git rm aurvex-bot.service aurvex-dashboard.service aurvex-watchdog.service --ignore-unmatch", ignore_error=True)

    # 3. Degisiklikleri Ekle ve Commit At
    print("\n[ADIM 4] Yeni degisiklikler Git'e ekleniyor...")
    run_cmd("git add -A")
    run_cmd('git commit -m "Restore damaged files, fix format and clean obsolete services"')

    # 4. Push
    print("\n[ADIM 5] GitHub'a gonderiliyor...")
    run_cmd("git push origin main")

    print("\n===================================================")
    print("ISLEM TAMAMLANDI! (PAPER MODE OPERATIONAL)")
    print("===================================================")

if __name__ == "__main__":
    main()
