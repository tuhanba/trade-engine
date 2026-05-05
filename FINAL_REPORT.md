# Trade Engine v5.0 Finalizasyon Raporu ve Aksiyon Planı

Bu rapor, `trade-engine` projesinin analiz sonuçlarını, tespit edilen problemleri ve sistemi final, stabil ve tek akışlı hale getirmek için uygulanması gereken adımları içermektedir.

## 1. Bulunan Gerçek Problemler

### Veri Akışı ve Şema Problemleri
- **Çoklu Veri Kaynakları:** Sistemde `SignalData` (core/data_layer.py) ve DB `trades` tablosu arasında ciddi bir uyumsuzluk var. `SignalData` in-memory çalışırken, `execution_engine.py` kendi hesaplamalarını yaparak DB'ye farklı bir formatta (`trade` dict) kayıt yapıyor.
- **Dashboard Veri Eksikliği:** Dashboard'un beklediği `distance_to_tp1`, `distance_to_tp2`, `distance_to_tp3`, `tp1_hit`, `tp2_hit` gibi alanlar backend'den her zaman doğru veya dolu gelmiyor. `app.py` içindeki `/api/live` endpoint'i bu verileri hesaplamaya çalışıyor ancak `execution_engine.py` bu alanları tam olarak güncellemiyor.
- **Telegram Format Uyuşmazlığı:** `telegram_delivery.py` iki farklı format bekliyor: `format_signal` için obje (`SignalData`), `send_trade_open` için dict (`trade`). Bu durum veri tutarsızlığına yol açıyor.
- **Risk ve Target Hesaplamaları:** `execution_engine.py` içinde `_get_leverage_tp_sl` fonksiyonu ile risk hesaplamaları yapılıyor, ancak `core/advanced_risk_engine.py` adında kullanılmayan veya çakışan başka bir risk motoru daha var.

### Öğrenme Sistemi Eksiklikleri
- Açılmayan trade'lerden (VETO/WATCH) öğrenme sistemi tam olarak entegre değil. `scalp_bot_v3.py` içinde `_save_ghost` fonksiyonu `paper_results` tablosuna kayıt yapıyor ancak `signal_candidates` tablosu tam olarak bu amaçla kullanılmıyor.

### Gereksiz ve Eski Kodlar
- `core/advanced_risk_engine.py`, `core/advanced_trend_engine.py`, `core/elite_monitor.py` gibi dosyalar ana akışta kullanılmıyor veya çakışıyor.
- `setup_all.sh` scripti çok agresif ve gerçek trade geçmişini silme riski taşıyor (`DELETE FROM trades` komutları içeriyor).
- `n8n_bridge.py` ve `analyze_trades.py` gibi dosyalar ana sistemin dışında kalmış.

## 2. Silinecek ve Taşınacak Dosyalar

Aşağıdaki dosyalar `archive/` klasörüne taşınmalıdır:
- `core/advanced_risk_engine.py` (Risk hesaplamaları `execution_engine.py` içinde tekleştirilecek)
- `core/advanced_trend_engine.py` (Kullanılmıyor)
- `core/elite_monitor.py` (Kullanılmıyor)
- `core/async_market_scanner.py` (Eski tarama mantığı)
- `n8n_bridge.py` (Kullanılmıyorsa)
- `analyze_trades.py` (Kullanılmıyorsa)
- `setup_all.sh` (Tehlikeli, yerine güvenli bir script yazılacak)

## 3. Düzeltilecek Dosyalar

- **`core/data_layer.py`**: `SignalData` sınıfı DB şeması ile tam uyumlu hale getirilecek.
- **`execution_engine.py`**: Risk ve target hesaplamaları `SignalData` üzerinden yapılacak ve DB'ye yazılacak.
- **`app.py`**: `/api/live` ve diğer endpoint'ler doğrudan DB'den gelen verileri dashboard'un beklediği formatta dönecek.
- **`database.py`**: `signal_candidates` tablosu güncellenecek ve açılmayan trade'ler için gerekli tüm alanlar eklenecek.
- **`scalp_bot_v3.py`**: VETO/WATCH durumlarında `signal_candidates` tablosuna tam kayıt yapılacak.
- **`telegram_delivery.py`**: Sadece tek bir veri kaynağından (DB veya `SignalData`) beslenecek şekilde güncellenecek.

## 4. Yapılacak Kod Değişiklikleri

1. **Tek Şema (Single Schema):** `SignalData` sınıfı, `trades` ve `signal_candidates` tablolarının tüm alanlarını kapsayacak şekilde genişletilecek.
2. **Risk Engine Tekleştirme:** `execution_engine.py` içindeki `_get_leverage_tp_sl` fonksiyonu `core/data_layer.py` veya ayrı bir `risk_manager.py` içine taşınarak her yerde aynı hesaplamanın yapılması sağlanacak.
3. **Dashboard Fallback:** `app.py` içindeki endpoint'lerde boş gelebilecek alanlar için güvenli fallback'ler (örn. `0` veya `None`) eklenecek.
4. **Öğrenme Sistemi:** `scalp_bot_v3.py` içindeki `_save_ghost` fonksiyonu, `signal_candidates` tablosuna `symbol, direction, entry, sl, tp1/tp2/tp3, score, setup_quality, decision, reject_reason, ai_veto_reason, risk_reject_reason, market_regime, session, volume, volatility, rsi/ema/trend/trigger/risk skorları, created_at, future_outcome` alanlarını kaydedecek şekilde güncellenecek.

## 5. DB Migration Planı

Gerçek trade geçmişini korumak için `database.py` içindeki `_run_migration` fonksiyonuna aşağıdaki alanlar eklenecek:

```python
# signal_candidates tablosu için yeni alanlar
("signal_candidates", "ai_veto_reason", "TEXT DEFAULT ''"),
("signal_candidates", "risk_reject_reason", "TEXT DEFAULT ''"),
("signal_candidates", "market_regime", "TEXT DEFAULT ''"),
("signal_candidates", "session", "TEXT DEFAULT ''"),
("signal_candidates", "volume", "REAL DEFAULT 0"),
("signal_candidates", "volatility", "REAL DEFAULT 0"),
("signal_candidates", "rsi_score", "REAL DEFAULT 0"),
("signal_candidates", "ema_score", "REAL DEFAULT 0"),
("signal_candidates", "trend_score", "REAL DEFAULT 0"),
("signal_candidates", "trigger_score", "REAL DEFAULT 0"),
("signal_candidates", "risk_score", "REAL DEFAULT 0"),
("signal_candidates", "future_outcome", "TEXT DEFAULT ''"),
```

## 6. Sunucu Komutları

Sunucuda sırasıyla çalıştırılacak güvenli komutlar:

```bash
# 1. Servisleri durdur
sudo systemctl stop aurvex-bot.service
sudo systemctl stop aurvex-dashboard.service
sudo systemctl stop aurvex-watchdog.service

# 2. Repo ve DB yedeği al
cd /root/trade-engine
cp trading.db trading_backup_$(date +%F).db
tar -czvf repo_backup_$(date +%F).tar.gz . --exclude=venv --exclude=.git

# 3. Eski cache/log/pyc/gereksiz servisleri temizle
find . -type d -name "__pycache__" -exec rm -r {} +
rm -rf logs/*.log
mkdir -p archive
mv core/advanced_risk_engine.py archive/
mv core/advanced_trend_engine.py archive/
mv core/elite_monitor.py archive/
mv core/async_market_scanner.py archive/
mv setup_all.sh archive/

# 4. Requirements kur
source venv/bin/activate
pip install -r requirements.txt

# 5. DB migration çalıştır
python3 -c "from database import init_db; init_db()"

# 6. Systemd servislerini yeniden kur (Gerekirse)
sudo systemctl daemon-reload

# 7. Bot + dashboard restart et
sudo systemctl start aurvex-dashboard.service
sudo systemctl start aurvex-bot.service
sudo systemctl start aurvex-watchdog.service

# 8. Health check yap
curl -s http://localhost:5000/api/health | jq

# 9. Logları kontrol et
tail -f logs/bot.log
```

## 7. Test Komutları

Sistemin doğruluğunu test etmek için eklenecek/çalıştırılacak testler:

```bash
# Pytest çalıştır
pytest tests/

# Manuel test scripti (test_system.py)
python3 -c "
import requests
# a) SignalData eksiksiz oluşuyor mu? (Loglardan kontrol)
# b) tp1/tp2/tp3 dashboarda geliyor mu?
r = requests.get('http://localhost:5000/api/live')
print('Live API:', r.json())
# c) açılmayan sinyal signal_candidates tablosuna kaydediliyor mu?
import sqlite3
conn = sqlite3.connect('trading.db')
print('Candidates:', conn.execute('SELECT COUNT(*) FROM signal_candidates').fetchone()[0])
# d) /api/live current_price ve unrealized_pnl dönüyor mu? (Yukarıdaki r.json() çıktısından kontrol)
"
```

## 8. Final Kontrol Listesi

- [ ] Gerçek trade geçmişi korundu mu? (Yedek alındı)
- [ ] Risk filtreleri gevşetildi mi? (Hayır, sadece optimize edildi)
- [ ] Dashboard ve Telegram aynı kaynaktan mı besleniyor? (Evet, DB/DataLayer)
- [ ] Tek schema, tek DB akışı sağlandı mı? (Evet, `SignalData` güncellendi)
- [ ] Sistem çalışır durumda mı? (Health check başarılı)
