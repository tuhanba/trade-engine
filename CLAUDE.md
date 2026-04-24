# AX Trade Engine — Proje Durumu

## Genel Bilgi
- **Proje:** AX — öz-öğrenen kripto futures trading botu (paper trading, Faz 1)
- **Sunucu:** 143.198.90.104 (Ubuntu 24.04)
- **Branch:** `claude/setup-ax-database-Wjwr6`
- **Python:** `/root/trade-engine/venv/bin/python3`
- **DB:** `/root/trade-engine/ax.db` (SQLite WAL)
- **Loglar:** `/root/trade-engine/logs/ax_bot.log`

## Kurulum Durumu — TAMAMLANDI
Tüm modüller yazıldı, sunucuya deploy edildi, servisler çalışıyor.

### Çalışan Servisler
- `aurvex-bot.service` — scalp_bot.py (market tarama + trading loop)
- `aurvex-dashboard.service` — app.py (Flask dashboard, port 5000)
- `aurvex-n8n-bridge.service` — n8n_bridge.py (raporlama köprüsü, port 5001)

### Temel Dosyalar
| Dosya | Görev |
|---|---|
| `config.py` | .env okur, tüm parametreler |
| `database.py` | 14 tablo, WAL modu, init_db() |
| `market_scan.py` | Binance Futures, filtreler, hot coin |
| `signal_engine.py` | 15m klines, ATR/EMA/RSI, BREAKOUT/PULLBACK |
| `ai_brain.py` | evaluate(), 11 veto, skor 0-100 |
| `execution_engine.py` | Paper trade aç/kapat, TP1/TP2/runner |
| `scalp_bot.py` | Ana döngü, koordinatör |
| `telegram_manager.py` | Bildirimler, komutlar |
| `app.py` | Dashboard API + UI |
| `n8n_bridge.py` | Health/rapor endpoint'leri |
| `coin_library.py` | Coin hafızası, cooldown |

## Önemli Konfigürasyon
- `EXECUTION_MODE=paper` — gerçek emir yok
- `AX_MODE=ax1` — kural tabanlı skor
- `RISK_PCT=1.0` — bakiyenin %1'i risk
- `MAX_OPEN_TRADES=3`
- `PAPER_BALANCE=250 USD`

## Faz 1 Hedefi
100 paper trade topla → performans analiz et → Faz 2'ye geç.

## Sunucu Komutları
```bash
systemctl status aurvex-bot.service
systemctl restart aurvex-bot.service
tail -f /root/trade-engine/logs/ax_bot.log
journalctl -u aurvex-bot.service -n 50 --no-pager
```

## Dashboard
`http://143.198.90.104:5000`

## Notlar
- .env dosyası sunucuda `/root/trade-engine/.env` — repoya commit edilmez
- Termius iOS → URL'leri `<>` ile sarıyor, git remote için Python trick kullan
- Telegram CHAT_ID: 958182551
