"""
tests/test_chaos_scenarios.py — Faz 1.4 Chaos / Dayanıklılık Senaryoları.

Mock'larla simüle edilen 4 felaket senaryosu:
  1. Binance WS 10 dk kopması — reconnect + fiyat bayatlığı guard'ı
  2. Redis ölümü — sistem SQLite ile çalışmaya devam eder
  3. Disk dolu / DB locked — 5 ardışık update_bot_status hatasında KRİTİK Telegram uyarısı
  4. Telegram API 429 — exponential backoff + kuyruk kaybolmaz
"""

import asyncio
import threading
import time as _time

import pytest


# ══════════════════════════════════════════════════════════════════════════════
# Senaryo 1 — Binance WS kopması
# ══════════════════════════════════════════════════════════════════════════════

class _FlakyExchange:
    """İlk N çağrıda kopan (exception), sonra veri döndüren sahte ccxt exchange."""

    def __init__(self, fail_times: int = 2):
        self.calls = 0
        self.fail_times = fail_times

    async def watch_tickers(self, symbols=None):
        self.calls += 1
        if self.calls <= self.fail_times:
            raise ConnectionError("WS bağlantısı koptu (simülasyon)")
        return {
            "BTC/USDT:USDT": {
                "last": 50000.0, "bid": 49999.0, "ask": 50001.0,
                "bidVolume": 1.0, "askVolume": 1.0,
            }
        }

    async def close(self):
        pass


async def test_ws_reconnect_after_disconnect(monkeypatch):
    """WS art arda kopsa bile loop denemeye devam etmeli ve veri akışı geri gelmeli."""
    from core.async_market_data import AsyncMarketDataService

    # Gerçek ccxt nesnesi yaratma — ağ erişimi yok
    service = AsyncMarketDataService.__new__(AsyncMarketDataService)
    service.exchange = _FlakyExchange(fail_times=2)
    service.ticker_callbacks = []
    service.kline_callbacks = []
    service.running = True
    service._tasks = []

    received = []

    def on_ticker(data):
        received.append(data)
        # Veri geldi — döngüyü durdur
        service.running = False

    service.ticker_callbacks.append(on_ticker)

    # Hata sonrası 2 sn bekleme yerine anında devam (test hızı)
    async def _no_sleep(_secs):
        return None

    monkeypatch.setattr(asyncio, "sleep", _no_sleep)

    await asyncio.wait_for(service._watch_tickers_loop(["BTC/USDT:USDT"]), timeout=10)

    # 2 kopma + 1 başarılı çağrı = en az 3 deneme → reconnect çalışıyor
    assert service.exchange.calls >= 3
    assert received, "Reconnect sonrası ticker callback'i veri almalıydı"
    assert received[0]["s"] == "BTCUSDT"
    assert float(received[0]["c"]) == 50000.0


def test_price_age_tracking():
    """set_cached_price yaş damgası bırakmalı; get_price_age bayatlığı ölçmeli."""
    from core import market_data

    market_data.set_cached_price("CHAOSUSDT", 1.234)
    age = market_data.get_price_age("CHAOSUSDT")
    assert age is not None and age < 5.0

    # 10 dk önce güncellenmiş gibi geriye çek
    market_data._PRICE_CACHE_TS["CHAOSUSDT"] = _time.time() - 600
    age = market_data.get_price_age("CHAOSUSDT")
    assert age is not None and age > 120

    # Cache'te hiç olmayan sembol → None (REST yolu, engelleme yok)
    assert market_data.get_price_age("HICYOKUSDT") is None

    # Temizlik
    market_data._PRICE_CACHE.pop("CHAOSUSDT", None)
    market_data._PRICE_CACHE_TS.pop("CHAOSUSDT", None)


def test_stale_price_blocks_paper_trade():
    """>120 sn bayat fiyatla open_paper_trade trade AÇMAMALI (None dönmeli)."""
    from core import market_data
    from core.data_layer import SignalData
    from execution_engine import ExecutionEngine

    sym = "STALEUSDT"
    market_data.set_cached_price(sym, 100.0)
    market_data._PRICE_CACHE_TS[sym] = _time.time() - 600  # 10 dk bayat

    signal = SignalData(
        symbol=sym, side="LONG", entry_price=100.0, stop_loss=99.0,
        tp1=102.0, risk_pct=1.0, leverage=10, final_score=80.0,
    )

    engine = ExecutionEngine()
    try:
        result = engine.open_paper_trade(signal)
        assert result is None, "Bayat fiyatla trade açılmamalıydı"
    finally:
        market_data._PRICE_CACHE.pop(sym, None)
        market_data._PRICE_CACHE_TS.pop(sym, None)


def test_fresh_price_passes_freshness_guard(monkeypatch):
    """Taze fiyat guard'ı geçmeli (red, guard'dan SONRAKİ aşamalardan gelebilir)."""
    from core import market_data
    from core.data_layer import SignalData
    from execution_engine import ExecutionEngine
    import execution_engine as ee_mod

    sym = "FRESHUSDT"
    market_data.set_cached_price(sym, 100.0)  # şimdi → taze

    # Guard'ı geçtiğini kanıtla: bir sonraki adım (get_open_trades) çağrılıyor mu?
    guard_passed = {"value": False}

    def _sentinel(*a, **k):
        guard_passed["value"] = True
        raise RuntimeError("sentinel — guard geçildi, DB aşamasına ulaşıldı")

    monkeypatch.setattr(ee_mod.database, "get_open_trades", _sentinel)

    signal = SignalData(
        symbol=sym, side="LONG", entry_price=100.0, stop_loss=99.0,
        tp1=102.0, risk_pct=1.0, leverage=10, final_score=80.0,
    )

    engine = ExecutionEngine()
    try:
        with pytest.raises(RuntimeError, match="sentinel"):
            engine.open_paper_trade(signal)
        assert guard_passed["value"], "Taze fiyat guard'ı geçmeliydi"
    finally:
        market_data._PRICE_CACHE.pop(sym, None)
        market_data._PRICE_CACHE_TS.pop(sym, None)


# ══════════════════════════════════════════════════════════════════════════════
# Senaryo 2 — Redis ölümü: sistem SQLite ile yaşamaya devam eder
# ══════════════════════════════════════════════════════════════════════════════

@pytest.fixture()
def dead_redis(monkeypatch):
    """redis_state'in TÜM giriş noktaları exception fırlatır (Redis tamamen ölü)."""
    from core import redis_state

    def _boom(*a, **k):
        raise ConnectionError("Redis öldü (simülasyon)")

    for fn in ("set", "get", "delete", "exists", "get_param", "set_param"):
        monkeypatch.setattr(redis_state, fn, _boom)
    yield


def test_redis_death_config_and_state_survive(dead_redis, test_db):
    """Redis tamamen ölüyken: config okuma, system_state yazma/okuma çalışmalı."""
    import config

    # Yazma: update_system_state Redis senkronunu try/except ile yutmalı
    test_db.update_system_state("trade_threshold", "42.0")

    # Okuma: config Redis-first yolu patlar → SQLite fallback değeri getirir
    val = config._read_dynamic_param_from_db("TRADE_THRESHOLD")
    assert val == 42.0

    # get_system_state / get_market_regime de çalışmalı
    test_db.update_system_state("market_regime", "NEUTRAL")
    assert test_db.get_system_state("market_regime") == "NEUTRAL"
    assert test_db.get_market_regime() == "NEUTRAL"


def test_redis_death_bot_status_survives(dead_redis, test_db):
    """Redis ölüyken update_bot_status SQLite'a yazmalı, get_bot_status okumalı."""
    test_db.update_bot_status("engine_status", "running")
    status = test_db.get_bot_status("engine_status")
    assert status.get("value") == "running"


# ══════════════════════════════════════════════════════════════════════════════
# Senaryo 3 — Disk dolu / DB locked: 5 ardışık hata → KRİTİK Telegram uyarısı
# ══════════════════════════════════════════════════════════════════════════════

@pytest.fixture()
def watchdog_counter_reset():
    """Watchdog modül-seviyesi sayaç durumunu test öncesi/sonrası sıfırlar."""
    from core import watchdog
    watchdog._DB_WRITE_FAILURES = 0
    watchdog._DB_FAILURE_ALERT_SENT = False
    yield watchdog
    watchdog._DB_WRITE_FAILURES = 0
    watchdog._DB_FAILURE_ALERT_SENT = False


def test_db_write_failure_storm_triggers_critical_alert(watchdog_counter_reset, test_db, monkeypatch):
    """update_bot_status art arda 5 kez hata alırsa Telegram'a KRİTİK uyarı gitmeli."""
    import sqlite3
    import telegram_delivery

    sent_messages = []
    monkeypatch.setattr(telegram_delivery, "send_message",
                        lambda msg, *a, **k: sent_messages.append(msg) or True)

    # Disk dolu simülasyonu: bağlantı açılışı patlar
    def _disk_full(*a, **k):
        raise sqlite3.OperationalError("database or disk is full")

    monkeypatch.setattr(test_db, "get_connection", _disk_full)

    # 4 hata → henüz uyarı YOK
    for _ in range(4):
        test_db.update_bot_status("heartbeat_test", "x")
    assert not sent_messages, "Eşik (5) altında uyarı gönderilmemeliydi"

    # 5. hata → KRİTİK uyarı
    test_db.update_bot_status("heartbeat_test", "x")
    assert len(sent_messages) == 1
    assert "KRİTİK" in sent_messages[0]

    # 6.-7. hata → spam YOK (tek seferlik uyarı)
    test_db.update_bot_status("heartbeat_test", "x")
    test_db.update_bot_status("heartbeat_test", "x")
    assert len(sent_messages) == 1


def test_db_write_recovery_resets_counter(watchdog_counter_reset, test_db, monkeypatch):
    """Başarılı yazım sayaç ve uyarı bayrağını sıfırlamalı (yeni fırtına yeniden uyarır)."""
    from core import watchdog
    import telegram_delivery

    sent_messages = []
    monkeypatch.setattr(telegram_delivery, "send_message",
                        lambda msg, *a, **k: sent_messages.append(msg) or True)

    # 3 hata biriktir
    for _ in range(3):
        watchdog.report_db_write_failure("test")
    assert watchdog._DB_WRITE_FAILURES == 3

    # Başarılı yazım → sıfırla (gerçek DB'ye yazar — test_db fixture'ı geçici DB sağlar)
    test_db.update_bot_status("recovery_test", "ok")
    assert watchdog._DB_WRITE_FAILURES == 0
    assert watchdog._DB_FAILURE_ALERT_SENT is False
    assert not sent_messages


# ══════════════════════════════════════════════════════════════════════════════
# Senaryo 4 — Telegram API 429: exponential backoff + kuyruk korunur
# ══════════════════════════════════════════════════════════════════════════════

def test_telegram_429_exponential_backoff_and_no_message_loss(monkeypatch):
    """429 alan mesaj exponential backoff ile yeniden denenmeli, kuyruktan DÜŞMEMELİ."""
    import telegram_delivery

    done = threading.Event()
    calls = []
    sleeps = []

    def _fake_send(text, parse_mode="HTML", reply_markup=None):
        calls.append(text)
        if len(calls) <= 2:
            return False, 429  # Telegram rate limit
        done.set()
        return True, 200

    monkeypatch.setattr(telegram_delivery, "_send_raw_detailed", _fake_send)

    # Backoff uykularını kaydet ama gerçekten bekleme (test hızı).
    # NEDEN: worker thread'i telegram_delivery.time.sleep kullanıyor.
    real_sleep = _time.sleep
    def _fake_sleep(secs):
        sleeps.append(secs)
        real_sleep(0.01)
    monkeypatch.setattr(telegram_delivery.time, "sleep", _fake_sleep)

    # İzole taze kuyruk (global singleton'a dokunma)
    q = telegram_delivery._Queue()
    q.push("chaos-429-testi", dedupe_key="chaos:429")

    assert done.wait(timeout=15), "Mesaj 2x429 sonrası başarıyla gönderilmeliydi"
    # Mesaj kaybolmadı: 2 başarısız + 1 başarılı = 3 deneme
    assert len(calls) == 3
    assert all(c == "chaos-429-testi" for c in calls)

    # Exponential backoff: deneme 1 → 2 sn, deneme 2 → 4 sn (min(30, 2**n))
    backoffs = [s for s in sleeps if s >= 2]
    assert backoffs[:2] == [2, 4], f"Backoff exponential olmalıydı, görülen: {backoffs[:2]}"
