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
# NEDEN: Dinamik config parametreleri ayrı bir namespace'te tutulur (ax:cfg:*).
# SQLite system_state tek doğruluk kaynağıdır (SSoT); Redis sadece write-through
# cache'tir. Bu key'ler KALICIDIR (TTL yok) — yazma anında senkronlanır,
# miss durumunda okuma sırasında SQLite'tan onarılır (read-repair).
_CFG_NS = "cfg:"

# NEDEN: Redis geçici olarak öldüğünde yapılan param yazımları Redis'e işlenemez
# ve kalıcı cfg key'i bayat kalabilir. Başarısız yazımların key'leri burada
# biriktirilir; Redis geri geldiğinde ilk param işleminde silinerek (lazy
# self-heal) okuyucular SQLite'taki güncel değere düşürülür.
_pending_cfg_invalidations: set = set()


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
        # NEDEN: Süreç yeniden başladığında Redis'teki cfg:* key'leri, süreç
        # kapalıyken SQLite'a yazılmış değerlerin gerisinde kalmış olabilir.
        # SSoT SQLite olduğu için cfg cache'i temizlenir; read-repair ile
        # ilk okumada güncel değerler geri dolar.
        try:
            cfg_keys = list(_client.scan_iter(match=f"{_PREFIX}{_CFG_NS}*"))
            if cfg_keys:
                _client.delete(*cfg_keys)
                logger.info("[Redis] %d adet cfg cache key'i temizlendi (warm-start)", len(cfg_keys))
        except Exception as exc:
            logger.debug("[Redis] cfg warm-start temizliği atlandı: %s", exc)
        return True
    except Exception as exc:
        _available = False
        logger.warning("[Redis] Bağlanamadı (%s) — SQLite fallback aktif", exc)
        return False


_local_cache = {}  # maps key -> (value, expiry_timestamp_or_none)


def available() -> bool:
    return _available


def _k(key: str) -> str:
    return f"{_PREFIX}{key}"


def set(key: str, value: Any, ttl: Optional[int] = None) -> bool:
    """Redis'e yazar. Redis yoksa local in-memory cache'e yazar. ttl saniye cinsinden."""
    import time
    expiry = (time.time() + ttl) if ttl else None
    
    # Her koşulda local cache'e de yazalım (en hızlı okuma ve fallback için)
    _local_cache[key] = (value, expiry)
    
    if not _available:
        return True
        
    try:
        raw = value if isinstance(value, str) else json.dumps(value, default=str)
        if ttl:
            _client.setex(_k(key), ttl, raw)
        else:
            _client.set(_k(key), raw)
        return True
    except Exception as exc:
        logger.debug("[Redis] set hatası %s: %s", key, exc)
        return True  # Local cache'e başarıyla yazıldığı için True dönüyoruz


def get(key: str, default: Any = None) -> Any:
    """Redis'ten okur. Redis yoksa veya bulamazsa local in-memory cache'ten okur."""
    import time
    
    # Önce local cache kontrolü (TTL aşılmadıysa direkt dön)
    if key in _local_cache:
        val, expiry = _local_cache[key]
        if expiry is None or expiry > time.time():
            return val
        else:
            del _local_cache[key]
            
    if not _available:
        return default
        
    try:
        raw = _client.get(_k(key))
        if raw is None:
            return default
        try:
            val = json.loads(raw)
        except Exception:
            val = raw
        # Local cache'i güncelle
        _local_cache[key] = (val, None)
        return val
    except Exception as exc:
        logger.debug("[Redis] get hatası %s: %s", key, exc)
        return default


def delete(key: str) -> bool:
    """Redis anahtarını ve local cache'i siler."""
    if key in _local_cache:
        del _local_cache[key]
        
    if not _available:
        return True
        
    try:
        _client.delete(_k(key))
        return True
    except Exception:
        return False


def exists(key: str) -> bool:
    """Anahtar var mı?"""
    import time
    if key in _local_cache:
        val, expiry = _local_cache[key]
        if expiry is None or expiry > time.time():
            return True
        else:
            del _local_cache[key]
            
    if not _available:
        return False
        
    try:
        return bool(_client.exists(_k(key)))
    except Exception:
        return False


# ── Dinamik Config Parametreleri (Faz 1.1 — Redis-first write-through cache) ──

def _heal_pending_cfg_invalidations() -> None:
    """Redis kopukken başarısız olan param yazımlarının bayat key'lerini siler.

    NEDEN: cfg:* key'leri kalıcıdır (TTL yok). Yazma anında Redis ölüyse SQLite
    güncellenir ama Redis'te eski değer kalır; Redis dönünce okuyucular bayat
    değeri görür. Bu fonksiyon Redis'e erişilen ilk fırsatta o key'leri silerek
    okuyucuları SQLite fallback'ine (güncel değere) yönlendirir.
    """
    global _pending_cfg_invalidations
    if not _pending_cfg_invalidations or not _available or _client is None:
        return
    pending = list(_pending_cfg_invalidations)
    try:
        _client.delete(*[_k(_CFG_NS + key) for key in pending])
        _pending_cfg_invalidations.clear()
        logger.info("[Redis] %d bayat cfg key'i self-heal ile temizlendi", len(pending))
    except Exception:
        pass  # Redis hâlâ erişilemez — bir sonraki çağrıda tekrar denenir


def get_param(key: str, default: Any = None) -> Any:
    """Dinamik config parametresini Redis'ten okur (cfg:{key}).

    NEDEN: config.__getattr__ her dinamik parametre okumasında SQLite bağlantısı
    açıyordu (ghost loop 12 sn'de bir, execution monitoring 1 sn'de bir) —
    SQLite lock baskısının ana kaynağı. Redis-first okuma bu baskıyı kaldırır.

    DİKKAT: Bilinçli olarak _local_cache KULLANILMAZ — parametreler birden çok
    süreç (engine + dashboard) tarafından yazılabilir; süreç-içi cache süreçler
    arası bayatlamaya yol açar. Redis miss/erişilemez → None (çağıran SQLite'a
    düşer; SSoT SQLite'tır).
    """
    if not _available or _client is None:
        return default
    _heal_pending_cfg_invalidations()
    if key in _pending_cfg_invalidations:
        # Bayat olabilecek key temizlenemedi — Redis değerine güvenme
        return default
    try:
        raw = _client.get(_k(_CFG_NS + key))
        return raw if raw is not None else default
    except Exception as exc:
        logger.debug("[Redis] get_param hatası %s: %s", key, exc)
        return default


def set_param(key: str, value: Any) -> bool:
    """Dinamik config parametresini Redis'e KALICI olarak yazar (cfg:{key}, TTL yok).

    NEDEN: Yazma sırası daima ÖNCE SQLite (kalıcılık) → SONRA bu fonksiyon
    (cache senkronu). Tek çağrı noktası database.update_system_state()'tir —
    başka yerden çağrılması cache tutarlılığını bozar.
    """
    if not _available or _client is None:
        return False
    _heal_pending_cfg_invalidations()
    try:
        _client.set(_k(_CFG_NS + key), str(value))
        _pending_cfg_invalidations.discard(key)
        return True
    except Exception as exc:
        # NEDEN: Yazım başarısız → Redis'teki eski değer bayat kaldı; key
        # işaretlenir ki Redis dönünce silinip SQLite'tan onarılsın.
        _pending_cfg_invalidations.add(key)
        logger.warning("[Redis] set_param başarısız (%s) — key self-heal kuyruğunda: %s", key, exc)
        return False


def invalidate_open_trades() -> None:
    """Açık trade cache'ini geçersiz kılar — trade açılınca/kapanınca çağrılır."""
    delete("open_trades_cache")
    delete("open_trades_cache_paper")
    delete("open_trades_cache_live")


def flush_db() -> bool:
    """Redis veritabanını ve local cache'i temizler."""
    global _client, _available
    _local_cache.clear()
    if not _available or _client is None:
        return True
    try:
        _client.flushdb()
        logger.info("[Redis] Veritabanı başarıyla temizlendi (flushdb)")
        return True
    except Exception as exc:
        logger.error("[Redis] flushdb hatası: %s", exc)
        return False
