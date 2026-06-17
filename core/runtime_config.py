"""
core/runtime_config.py — Process-içi Config Cache (Faz 8.1)
============================================================
config.__getattr__ her okumada Redis/SQLite round-trip yapıyor.
Bu modül process-içi 5 saniyelik cache katmanı ekler:

  RuntimeConfig.get("TRADE_THRESHOLD") 
      → önce process cache (5s TTL)
      → sonra config.__getattr__ (Redis → SQLite)

Kullanım:
  from core.runtime_config import RuntimeConfig
  val = RuntimeConfig.get("TRADE_THRESHOLD", default=55.0)

Invalidasyon:
  RuntimeConfig.invalidate("TRADE_THRESHOLD")  # tek key
  RuntimeConfig.invalidate()                   # tümü
"""
from __future__ import annotations

import time
import logging
import threading
from typing import Any, Optional

logger = logging.getLogger("ax.runtime_config")


class RuntimeConfig:
    """Thread-safe, process-içi config cache. 5 saniyelik TTL."""

    CACHE_TTL: float = 5.0  # saniye

    _cache: dict[str, tuple[Any, float]] = {}  # key → (value, expires_at)
    _lock = threading.Lock()

    @classmethod
    def get(cls, name: str, default: Any = None) -> Any:
        """
        Önce process cache'e bakar. Miss durumunda config.__getattr__ çağırır,
        sonucu cache'e yazar.
        """
        now = time.monotonic()
        with cls._lock:
            if name in cls._cache:
                val, expires = cls._cache[name]
                if now < expires:
                    return val
                del cls._cache[name]

        # Cache miss — config'den al
        try:
            import config as _cfg
            val = _cfg.__getattr__(name)
        except AttributeError:
            val = default
        except Exception as e:
            logger.debug("[RuntimeConfig] %s okuma hatası: %s", name, e)
            val = default

        if val is not None:
            with cls._lock:
                cls._cache[name] = (val, now + cls.CACHE_TTL)

        return val if val is not None else default

    @classmethod
    def invalidate(cls, name: Optional[str] = None) -> None:
        """
        Cache'i geçersiz kıl.
        name=None → tüm cache temizlenir.
        name='KEY' → sadece o key temizlenir.
        """
        with cls._lock:
            if name is None:
                cls._cache.clear()
                logger.debug("[RuntimeConfig] Tüm cache temizlendi")
            elif name in cls._cache:
                del cls._cache[name]
                logger.debug("[RuntimeConfig] Cache temizlendi: %s", name)

    @classmethod
    def warm_up(cls, keys: list[str]) -> None:
        """
        Engine başlangıcında sık kullanılan key'leri önceden yükler.
        Örnek: RuntimeConfig.warm_up(["TRADE_THRESHOLD", "RISK_PCT", ...])
        """
        for key in keys:
            cls.get(key)
        logger.info("[RuntimeConfig] %d key ön-yüklendi", len(keys))

    @classmethod
    def stats(cls) -> dict:
        """Mevcut cache durumunu döner (debug için)."""
        now = time.monotonic()
        with cls._lock:
            total = len(cls._cache)
            live  = sum(1 for _, (_, exp) in cls._cache.items() if exp > now)
        return {"total_keys": total, "live_keys": live, "ttl_sec": cls.CACHE_TTL}
