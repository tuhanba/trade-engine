"""
tests/test_redis_first_config.py — Faz 1.1 Redis-first dinamik config testleri.

Kabul kriterleri (AURVEX MASTER PLAN 1.1):
  (a) Redis'te değer varsa SQLite'a HİÇ gidilmez,
  (b) yazmada her iki katman (SQLite + Redis) güncellenir,
  (c) Redis kapalıyken SQLite fallback aynen çalışır.

Redis mock'lu ortamda çalışır — gerçek Redis sunucusu gerekmez.
"""

import pytest


class FakeRedis:
    """redis.Redis'in testlerde kullanılan minimal taklidi (decode_responses=True davranışı)."""

    def __init__(self):
        self.store = {}

    def ping(self):
        return True

    def get(self, k):
        return self.store.get(k)

    def set(self, k, v):
        self.store[k] = str(v)
        return True

    def setex(self, k, ttl, v):
        self.store[k] = str(v)
        return True

    def delete(self, *keys):
        for k in keys:
            self.store.pop(k, None)
        return len(keys)

    def exists(self, k):
        return k in self.store

    def scan_iter(self, match=None):
        import fnmatch
        return [k for k in list(self.store) if fnmatch.fnmatch(k, match or "*")]

    def flushdb(self):
        self.store.clear()
        return True


class ExplodingRedis(FakeRedis):
    """Her komutta exception fırlatan Redis taklidi — Redis ölümü senaryosu."""

    def get(self, k):
        raise ConnectionError("Redis bağlantısı koptu")

    def set(self, k, v):
        raise ConnectionError("Redis bağlantısı koptu")

    def delete(self, *keys):
        raise ConnectionError("Redis bağlantısı koptu")


@pytest.fixture()
def fake_redis(monkeypatch):
    """redis_state modülünü sahte (in-memory) Redis client ile aktif eder."""
    from core import redis_state
    fr = FakeRedis()
    monkeypatch.setattr(redis_state, "_client", fr)
    monkeypatch.setattr(redis_state, "_available", True)
    redis_state._pending_cfg_invalidations.clear()
    yield fr
    redis_state._pending_cfg_invalidations.clear()


# ── (a) Redis hit → SQLite'a gidilmez ─────────────────────────────────────────

def test_redis_hit_skips_sqlite(fake_redis, monkeypatch, test_db):
    import config
    import database

    fake_redis.store["ax:cfg:trade_threshold"] = "61.5"

    def _sqlite_yasak(*a, **k):
        raise AssertionError("Redis hit varken SQLite'a gidilmemeliydi")

    monkeypatch.setattr(database, "get_conn", _sqlite_yasak)

    val = config._read_dynamic_param_from_db("TRADE_THRESHOLD")
    assert val == 61.5


def test_redis_hit_bool_param(fake_redis, monkeypatch, test_db):
    """Cast fonksiyonları Redis'ten dönen string değerlerle de çalışmalı."""
    import config
    import database

    fake_redis.store["ax:cfg:tg_human_mode"] = "True"
    monkeypatch.setattr(
        database, "get_conn",
        lambda *a, **k: (_ for _ in ()).throw(AssertionError("SQLite'a gidilmemeliydi")),
    )

    val = config._read_dynamic_param_from_db("HUMAN_MODE")
    assert val is True


# ── (b) Yazma → her iki katman güncellenir ───────────────────────────────────

def test_write_through_updates_both_layers(fake_redis, test_db):
    test_db.update_system_state("trade_threshold", "58.0")

    # SQLite katmanı (SSoT)
    with test_db.get_conn() as conn:
        row = conn.execute(
            "SELECT value FROM system_state WHERE key='trade_threshold'"
        ).fetchone()
    assert row is not None and row["value"] == "58.0"

    # Redis katmanı (write-through cache, kalıcı cfg key)
    assert fake_redis.store.get("ax:cfg:trade_threshold") == "58.0"


def test_set_state_routes_through_update_system_state(fake_redis, test_db):
    """Friday (_apply_param_with_clamp) ve Telegram /set, set_state kullanır —
    set_state da tek yazım noktası üzerinden her iki katmanı güncellemeli."""
    test_db.set_state("trade_threshold", "52.5", actor="friday", reason="test")

    with test_db.get_conn() as conn:
        row = conn.execute(
            "SELECT value FROM system_state WHERE key='trade_threshold'"
        ).fetchone()
        audit = conn.execute(
            "SELECT new_value, actor FROM param_audit WHERE key='trade_threshold' ORDER BY id DESC LIMIT 1"
        ).fetchone()
    assert row is not None and row["value"] == "52.5"
    assert fake_redis.store.get("ax:cfg:trade_threshold") == "52.5"
    # Audit kaydı korunmuş olmalı (denetlenen key)
    assert audit is not None and audit["new_value"] == "52.5" and audit["actor"] == "friday"


def test_read_repair_populates_redis_on_miss(fake_redis, test_db):
    """Redis miss → SQLite'tan okunur ve Redis'e kalıcı geri yazılır (read-repair)."""
    import config

    # Sadece SQLite'a yaz (geçmişten kalan değer senaryosu)
    with test_db.get_conn() as conn:
        conn.execute(
            "INSERT INTO system_state (key, value, updated_at) VALUES ('trade_threshold', '49.0', datetime('now'))"
        )
    assert "ax:cfg:trade_threshold" not in fake_redis.store

    val = config._read_dynamic_param_from_db("TRADE_THRESHOLD")
    assert val == 49.0
    # read-repair: bir sonraki okuma SQLite'a inmesin diye Redis dolduruldu
    assert fake_redis.store.get("ax:cfg:trade_threshold") == "49.0"


# ── (c) Redis kapalı → SQLite fallback ───────────────────────────────────────

def test_redis_down_sqlite_fallback(monkeypatch, test_db):
    from core import redis_state
    import config

    monkeypatch.setattr(redis_state, "_available", False)
    monkeypatch.setattr(redis_state, "_client", None)

    # Yazma: Redis kapalıyken bile SQLite güncellenmeli (kalıcılık önce)
    test_db.update_system_state("trade_threshold", "47.5")

    val = config._read_dynamic_param_from_db("TRADE_THRESHOLD")
    assert val == 47.5


def test_redis_exception_falls_back_to_sqlite(monkeypatch, test_db):
    """Redis 'açık görünüp' her komutta hata fırlatsa bile sistem SQLite ile yaşar."""
    from core import redis_state
    import config

    monkeypatch.setattr(redis_state, "_client", ExplodingRedis())
    monkeypatch.setattr(redis_state, "_available", True)
    redis_state._pending_cfg_invalidations.clear()

    test_db.update_system_state("trade_threshold", "44.0")
    val = config._read_dynamic_param_from_db("TRADE_THRESHOLD")
    assert val == 44.0

    # Başarısız Redis yazımı self-heal kuyruğuna alınmış olmalı
    assert "trade_threshold" in redis_state._pending_cfg_invalidations
    redis_state._pending_cfg_invalidations.clear()


def test_self_heal_clears_stale_key_after_redis_recovery(fake_redis, test_db):
    """Redis kopukken yazılan parametre, Redis dönünce bayat değeri TEMİZLEMELİ."""
    from core import redis_state

    # Redis'te eski değer var; yazım sırasında Redis ölüyor
    fake_redis.store["ax:cfg:trade_threshold"] = "55.0"
    redis_state._pending_cfg_invalidations.add("trade_threshold")

    # Redis geri geldi (fake_redis zaten ayakta) → ilk get_param self-heal yapar
    val = redis_state.get_param("trade_threshold")
    # Bayat key silindi → None döner → çağıran SQLite'a düşer
    assert val is None
    assert "ax:cfg:trade_threshold" not in fake_redis.store
    assert not redis_state._pending_cfg_invalidations
