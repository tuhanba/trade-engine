# CLAUDE_DEPLOY.md
# AurvexAI v6.0 — Claude Code Deployment Talimatı

## Sen kimsin

trade-engine/ reposunun baş mühendisisin.
Aşağıdaki 3 adımı sırayla uygula. Sormadan yaz.

---

## Adım 1 — Dosyaları yerleştir

Bu dosyaları trade-engine/ kök dizinine kopyala:

| Kaynak dosya         | Hedef                                    |
|----------------------|------------------------------------------|
| `telegram_delivery.py`  | `telegram_delivery.py`                |
| `risk_service.py`       | `core/services/risk_service.py`       |
| `scanner_service.py`    | `core/services/scanner_service.py`    |
| `execution_service.py`  | `core/services/execution_service.py`  |
| `apply_patches.py`      | `apply_patches.py`                    |
| `verify_fixes.py`       | `verify_fixes.py`                     |

---

## Adım 2 — Patch'leri uygula

```bash
cd /root/trade_engine
python3 apply_patches.py
```

Çıktıda `❌` görürsen o patch'i manuel uygula (apply_patches.py içindeki fonksiyonu oku).

---

## Adım 3 — Doğrula

```bash
python3 verify_fixes.py
```

Tüm testler `✅` olmalı. `❌` varsa hata mesajını oku, ilgili dosyaya dön, düzelt, tekrar çalıştır.

---

## Adım 4 — Restart

```bash
systemctl restart aurvex-bot
sleep 8
systemctl status aurvex-bot
systemctl restart aurvex-dashboard
```

---

## Adım 5 — Canlı Doğrulama

```bash
# Bot log — hata var mı?
journalctl -u aurvex-bot -n 30 --no-pager

# API diagnostics — pipeline çalışıyor mu?
curl -s http://localhost:5000/api/diagnostics | python3 -m json.tool

# Signal events — veri geliyor mu?
python3 -c "
import sqlite3, config
conn = sqlite3.connect(config.DB_PATH)
rows = conn.execute(
    'SELECT stage, COUNT(*) FROM signal_events '
    'GROUP BY stage ORDER BY COUNT(*) DESC'
).fetchall()
print('Signal Events:')
for r in rows: print(f'  {r[0]}: {r[1]}')
"
```

---

## Başarı Kriterleri

- `verify_fixes.py` → tüm ✅
- `journalctl` → syntax error, ImportError yok
- `/api/diagnostics` → 200, stage_summary dolu
- `signal_events` → RISK_REJECTED ve EXECUTED kayıtları geliyor
- Telegram → trade açıldığında sadece 1 mesaj geliyor

---

## Önemli

Bu deploy'dan sonra yapılacak bir sonraki şey:
Ghost Learning'in threshold önerilerini (`ghost_suggestions` tablosu) okuyan
ve otomatik uygulayan bir servis yazmak. Ama önce bu baseline'ın stabil olduğunu gör.
