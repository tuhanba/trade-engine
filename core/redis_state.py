"""
core/redis_state.py — Redis hot-state katmanı (SQLite fallback ile).

SQLite write-lock baskısını azaltmak için yüksek frekanslı
okuma/yazma işlemleri Redis'te tutulur. Redis erişilemez olursa
tüm işlemler sessizce SQLite'a fallback yapar — sıfır kesinti.

Kullanım:
    from core import redis_state
    redis_state.init(host="127.0.0.1", port=6379)
    redis_state.set("market_regime", "BULLISH")
    val = redis_state.get("market_regime", default="NEUTRAL")
"""
from __future__ import annotations

import json
import logging
from typing import Any, Optional

logger = logging.getLogger("ax.redis")

_client = None
_available = False
_PREFIX = "ax:"


def init(
    host: str = "127.0.0.1",
    port: int = 6379,
    db: int = 0,
    password: Optional[str] = None,
) -> bool:
    """Redis bağlantısını başlatır. Başarısızsa SQLite fallback devreye girer."""
    global _client, _available
    try:
        import redis as _redis
        _client = _redis.Redis(
            host=host,
            port=port,
            db=db,
            password=password or None,
            socket_connect_timeout=2,
            socket_timeout=2,
            decode_responses=True,
        )
        _client.ping()
        _available = True
        logger.info("[Redis] Bağlantı başarılı: %s:%d db=%d", host, port, db)
        return True
    except Exception as exc:
        _available = False
        logger.warning("[Redis] Bağlanamadı (%s) — SQLite fallback aktif", exc)
        return False


def available() -> bool:
    return _available


def _k(key: str) -> str:
    return f"{_PREFIX}{key}"


def set(key: str, value: Any, ttl: Optional[int] = None) -> bool:
    """Redis'e yazar. ttl saniye cinsinden (None → sonsuz)."""
    if not _available:
        return False
    try:
        raw = value if isinstance(value, str) else json.dumps(value, default=str)
        if ttl:
            _client.setex(_k(key), ttl, raw)
        else:
            _client.set(_k(key), raw)
        return True
    except Exception as exc:
        logger.debug("[Redis] set hatası %s: %s", key, exc)
        return False


def get(key: str, default: Any = None) -> Any:
    """Redis'ten okur. Bulunamazsa veya hata olursa default döner."""
    if not _available:
        return default
    try:
        raw = _client.get(_k(key))
        if raw is None:
            return default
        try:
            return json.loads(raw)
        except Exception:
            return raw
    except Exception as exc:
        logger.debug("[Redis] get hatası %s: %s", key, exc)
        return default


def delete(key: str) -> bool:
    """Redis anahtarını siler (cache invalidation için)."""
    if not _available:
        return False
    try:
        _client.delete(_k(key))
        return True
    except Exception:
        return False


def exists(key: str) -> bool:
    """Anahtar var mı?"""
    if not _available:
        return False
    try:
        return bool(_client.exists(_k(key)))
    except Exception:
        return False


def invalidate_open_trades() -> None:
    """Açık trade cache'ini geçersiz kılar — trade açılınca/kapanınca çağrılır."""
    delete("open_trades_cache")


def flush_db() -> bool:
    """Redis veritabanındaki tüm anahtarları temizler."""
    global _client, _available
    if not _available or _client is None:
        return False
    try:
        _client.flushdb()
        logger.info("[Redis] Veritabanı başarıyla temizlendi (flushdb)")
        return True
    except Exception as exc:
        logger.error("[Redis] flushdb hatası: %s", exc)
        return False
