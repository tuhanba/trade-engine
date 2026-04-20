import requests, os
from dotenv import load_dotenv
load_dotenv()

url = "http://localhost:5678/webhook/ax-signal"
payload = {
    "event": "test",
    "message": "⚡ <b>n8n TEST</b>\n\nAX → n8n bağlantısı çalışıyor.",
    "tg_token": os.getenv("TELEGRAM_BOT_TOKEN", ""),
    "chat_id": os.getenv("TELEGRAM_CHAT_ID", ""),
}

try:
    r = requests.post(url, json=payload, timeout=5)
    print(f"Status: {r.status_code}")
    print(f"Response: {r.text[:200]}")
except Exception as e:
    print(f"Error: {e}")
