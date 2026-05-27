import os
import sys
from pathlib import Path

# Config dosyasını yükleyebilmek için ana dizini path'e ekle
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

try:
    import config
    from telegram_delivery import _send_raw_detailed
except ImportError as e:
    print(f"❌ Kütüphane hatası: {e}")
    sys.exit(1)

def test_telegram():
    token = config.TELEGRAM_BOT_TOKEN
    chat_id = config.TELEGRAM_CHAT_ID

    print("🔍 Telegram Ayarları Kontrol Ediliyor...")
    print(f"👉 BOT TOKEN: '{token}'")
    print(f"👉 CHAT ID:   '{chat_id}'\n")

    if not token or not chat_id:
        print("❌ HATA: .env dosyasında TELEGRAM_BOT_TOKEN veya TELEGRAM_CHAT_ID boş!")
        print("Lütfen nano .env komutuyla dosyayı açıp bilgileri girin.")
        sys.exit(1)

    print("⏳ Telegram'a test mesajı gönderiliyor...")
    test_message = "✅ <b>Bağlantı Başarılı!</b>\nBu bir test mesajıdır."
    
    success, status_code = _send_raw_detailed(test_message)
    
    if success:
        print("✅ BAŞARILI! Telefonuna mesaj gelmiş olmalı.")
    else:
        print(f"❌ HATA! Telegram API mesajı reddetti. HTTP Kodu: {status_code}")
        if status_code == 404:
            print("💡 İpucu: BOT_TOKEN yanlış olabilir.")
        elif status_code in (400, 401):
            print("💡 İpucu: CHAT_ID yanlış olabilir veya Bota Telegram'dan ilk mesajı (/start) atmamış olabilirsin.")
        print("Lütfen bilgileri kontrol edip tekrar dene.")

if __name__ == "__main__":
    test_telegram()
