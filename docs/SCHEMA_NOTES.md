# SCHEMA_NOTES.md — Veritabanı Şema Notları

> Faz 1.3 (Ölü Kod Temizliği) kapsamında oluşturuldu — 2026-06.
> Bu dosya, şemada var olan ama runtime'da OKUNMAYAN tabloların envanteridir.
> Kural: Tablolar ŞİMDİLİK SİLİNMEZ — durumları aşağıda işaretlenir.

## Ölü / Aday Tablolar

| Tablo | Durum | Not |
|---|---|---|
| `adaptive_stats` | ÖLÜ (aday) | Runtime'da okuyan kod yok. Silme kararı ileriki faza bırakıldı. |
| `coin_library` | ÖLÜ (aday) | `core/coin_library.py` modülü `coin_configs` + `active_universe` kullanıyor; bu TABLO okunmuyor. |
| `daily_summary` | UYKUDA — Faz 3'te CANLANDIRILACAK | Faz 3.1: gece 00:05 UTC `_daily_summary_loop` günün expectancy + funnel sayılarını yazacak; dashboard trend grafiklerinin veri kaynağı olacak. SİLME. |
| `market_snapshots` | UYKUDA (aday) | Faz 6 "Setup Replay" fikri bu tabloyu canlandırabilir (giriş anı indikatör anlık görüntüsü). |
| `weekly_summary` | UYKUDA — Faz 3'te CANLANDIRILACAK | `daily_summary` ile birlikte değerlendirilecek. SİLME. |

## Arşivlenen Modüller (archive/dead_code_2026_06/)

Taşıma öncesi her modül için `grep -rn "<modül_adı>" --include="*.py" .` ile
runtime referansı olmadığı TEK TEK doğrulandı (yalnız yorum satırı eşleşmeleri
ve `async_market_scanner` substring yanlış-pozitifleri bulundu):

- `core/market_scanner.py` — yerine `core/async_market_scanner.py` kullanılıyor
- `core/signal_engine.py` — pipeline `trigger_engine`/`trend_engine` üzerinden çalışıyor
- `core/signal_intelligence.py`
- `core/coin_personality.py` — yerine `core/coin_library.py` (coin_configs)
- `core/elite_monitor.py`
- `core/fallback_data_provider.py`
- `core/json_logger.py`
- `core/backtester.py` — aktif backtest altyapısı `scripts/backtest_system.py` + `scripts/backtest_engine.py`
- `core/signal_replay.py`
- `fix_db.py`, `patch.py`, `w_diff.txt` — tek seferlik onarım artıkları

Geri dönüş her zaman mümkün: dosyalar silinmedi, `git mv` ile taşındı.
