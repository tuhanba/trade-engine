"""
n8n workflow deploy scripti.
Kullanim: python3 /root/trade_engine/deploy_n8n.py
"""
import os, json, subprocess, requests
from pathlib import Path
from dotenv import load_dotenv

load_dotenv()

N8N_URL = os.getenv("N8N_URL", "http://localhost:5678")
API_KEY = os.getenv("N8N_API_KEY", "")
WF_DIR  = Path(__file__).parent / "n8n_workflows"

if not API_KEY:
    print("HATA: N8N_API_KEY bulunamadi. .env dosyasina ekle.")
    exit(1)

headers = {"X-N8N-API-KEY": API_KEY, "Content-Type": "application/json"}

def get_existing():
    r = requests.get(f"{N8N_URL}/api/v1/workflows", headers=headers)
    if r.status_code != 200:
        return {}
    return {w["name"]: w["id"] for w in r.json().get("data", [])}

def deploy(wf_path):
    data = json.loads(wf_path.read_text())
    name = data.get("name", wf_path.stem)
    existing = get_existing()

    READONLY = {"id", "active"}
    if name in existing:
        wf_id = existing[name]
        payload = {k: v for k, v in data.items() if k not in READONLY}
        r = requests.put(f"{N8N_URL}/api/v1/workflows/{wf_id}", headers=headers, json=payload)
        action = "Güncellendi"
    else:
        for k in READONLY:
            data.pop(k, None)
        r = requests.post(f"{N8N_URL}/api/v1/workflows", headers=headers, json=data)
        action = "Oluşturuldu"
        if r.status_code in (200, 201):
            wf_id = r.json().get("id")
        else:
            print(f"  HATA: {r.status_code} {r.text[:100]}")
            return

    if r.status_code not in (200, 201):
        print(f"  HATA: {r.status_code} {r.text[:100]}")
        return

    wf_id = r.json().get("id", existing.get(name))
    ra = requests.post(f"{N8N_URL}/api/v1/workflows/{wf_id}/activate", headers=headers)
    status = "aktif" if ra.status_code == 200 else f"aktivasyon hata {ra.status_code}"
    print(f"  {action}: {name} (id={wf_id}) — {status}")

print("=== n8n Deploy ===")
for wf_file in sorted(WF_DIR.glob("*.json")):
    print(f"Deploying: {wf_file.name}")
    deploy(wf_file)
print("=== Tamamlandi ===")
