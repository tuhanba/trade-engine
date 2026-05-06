"""
deploy_to_server.py — AX Engine Remote Deployment Script
=========================================================
Sunucuya SSH ile bağlanıp projeyi deploy eder ve çalıştırır.
Kullanım: python deploy_to_server.py
"""
import subprocess
import sys
import os

# Paramiko yükle
try:
    import paramiko
except ImportError:
    print("[*] paramiko kuruluyor...")
    subprocess.check_call([sys.executable, "-m", "pip", "install", "paramiko"])
    import paramiko

# ── Sunucu Bilgileri ──
HOST = "143.198.90.104"
USER = "root"
PASS = "B963753147Yz"
REMOTE_DIR = "/root/trade_engine"
REPO_URL = "https://github.com/tuhanba/trade-engine.git"

def ssh_exec(client, cmd, label=""):
    """Komutu çalıştır ve çıktıyı göster."""
    if label:
        print(f"\n{'='*50}")
        print(f"  {label}")
        print(f"{'='*50}")
    print(f"$ {cmd}")
    stdin, stdout, stderr = client.exec_command(cmd, timeout=120)
    out = stdout.read().decode("utf-8", errors="replace")
    err = stderr.read().decode("utf-8", errors="replace")
    exit_code = stdout.channel.recv_exit_status()
    if out.strip():
        print(out.strip())
    if err.strip() and exit_code != 0:
        print(f"[STDERR] {err.strip()}")
    return out, err, exit_code


def upload_env(client):
    """Lokal .env dosyasını sunucuya yükle."""
    env_path = os.path.join(os.path.dirname(__file__), ".env")
    if not os.path.exists(env_path):
        print("[!] .env dosyası bulunamadı, atlanıyor.")
        return
    sftp = client.open_sftp()
    remote_env = f"{REMOTE_DIR}/.env"
    print(f"\n[*] .env yükleniyor: {env_path} -> {remote_env}")
    sftp.put(env_path, remote_env)
    sftp.close()
    print("[OK] .env yüklendi.")


def main():
    print("=" * 60)
    print("  AX Trade Engine — Remote Deployment")
    print(f"  Server: {HOST}")
    print("=" * 60)

    # SSH bağlantısı
    print(f"\n[*] {HOST} sunucusuna bağlanılıyor...")
    client = paramiko.SSHClient()
    client.set_missing_host_key_policy(paramiko.AutoAddPolicy())
    try:
        client.connect(HOST, username=USER, password=PASS, timeout=15)
    except Exception as e:
        print(f"[HATA] SSH bağlantısı kurulamadı: {e}")
        return
    print("[OK] SSH bağlantısı kuruldu.")

    # 1. Sistem güncelle + gerekli paketler
    ssh_exec(client, "apt-get update -qq && apt-get install -y -qq python3 python3-pip python3-venv git screen",
             "1. Sistem Gereksinimleri")

    # 2. Repo klonla veya güncelle
    ssh_exec(client, f"""
if [ -d "{REMOTE_DIR}/.git" ]; then
    cd {REMOTE_DIR} && git fetch origin && git reset --hard origin/main
    echo "REPO GUNCELLENDI"
else
    rm -rf {REMOTE_DIR}
    git clone {REPO_URL} {REMOTE_DIR}
    echo "REPO KLONLANDI"
fi
""", "2. Git Repo")

    # 3. .env yükle
    upload_env(client)

    # 4. Virtual environment + dependencies
    ssh_exec(client, f"""
cd {REMOTE_DIR}
python3 -m venv .venv 2>/dev/null || true
source .venv/bin/activate
pip install --upgrade pip -q
pip install -r requirements.txt -q
echo "DEPENDENCIES OK"
""", "3. Python Dependencies")

    # 5. DB migration
    ssh_exec(client, f"""
cd {REMOTE_DIR}
source .venv/bin/activate
python scripts/migrate_accounting_schema.py
""", "4. DB Migration")

    # 6. Audit
    out, err, rc = ssh_exec(client, f"""
cd {REMOTE_DIR}
source .venv/bin/activate
python scripts/audit_pnl_consistency.py
""", "5. Audit")

    if rc != 0:
        print("\n[!] AUDIT BAŞARISIZ — Bot başlatılmayacak!")
        print("[!] Hataları düzelttikten sonra tekrar deneyin.")
        client.close()
        return

    # 7. Mevcut screen oturumlarını temizle
    ssh_exec(client, "screen -ls | grep -oP '\\d+\\.ax_' | xargs -I{} screen -X -S {} quit 2>/dev/null || true",
             "6. Eski Oturumlar Temizleniyor")

    # 8. Dashboard başlat (screen ile)
    ssh_exec(client, f"""
cd {REMOTE_DIR}
screen -dmS ax_dashboard bash -c 'source .venv/bin/activate && python app.py >> /var/log/ax_dashboard.log 2>&1'
echo "DASHBOARD STARTED on port 5000"
""", "7. Dashboard Başlatılıyor")

    # 9. Bot başlat (screen ile)
    ssh_exec(client, f"""
cd {REMOTE_DIR}
screen -dmS ax_bot bash -c 'source .venv/bin/activate && python scalp_bot_v3.py >> /var/log/ax_bot.log 2>&1'
echo "BOT STARTED"
""", "8. Scalp Bot Başlatılıyor")

    # 10. Kontrol
    import time
    time.sleep(3)
    ssh_exec(client, "screen -ls", "9. Çalışan Oturumlar")

    # Binance bağlantı testi
    ssh_exec(client, f"""
cd {REMOTE_DIR}
source .venv/bin/activate
python -c "import requests; r=requests.get('https://fapi.binance.com/fapi/v1/ping', timeout=5); print('Binance API:', r.status_code, r.text)"
""", "10. Binance API Testi")

    # Bot log kontrol
    ssh_exec(client, "sleep 5 && tail -20 /var/log/ax_bot.log 2>/dev/null || echo 'Log henüz yok'",
             "11. Bot Log (son 20 satır)")

    print("\n" + "=" * 60)
    print("  ✅ DEPLOYMENT TAMAMLANDI!")
    print(f"  Dashboard: http://{HOST}:5000")
    print(f"  Bot log:   ssh root@{HOST} 'tail -f /var/log/ax_bot.log'")
    print(f"  Screen:    ssh root@{HOST} 'screen -r ax_bot'")
    print("=" * 60)

    client.close()


if __name__ == "__main__":
    main()
