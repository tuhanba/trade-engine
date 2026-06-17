import logging
from core import redis_state

class RedisRingBufferHandler(logging.Handler):
    """
    Log satırlarını Redis üzerinde tutulan bir List'e yazar (LIFO / Ring Buffer).
    Eski loglar otomatik kırpılır. Dashboard'un loglara anında ulaşmasını sağlar.
    """
    def __init__(self, key="log:ring_buffer", max_lines=200):
        super().__init__()
        self.key = key
        self.max_lines = max_lines

    def emit(self, record):
        try:
            if redis_state._available and getattr(redis_state, '_client', None):
                msg = self.format(record)
                r_client = redis_state._client
                r_key = redis_state._k(self.key)
                r_client.lpush(r_key, msg)
                r_client.ltrim(r_key, 0, self.max_lines - 1)
        except Exception:
            pass

def setup_redis_logger(max_lines=200):
    """Kök (root) logger'a Redis handler'ı ekler."""
    root_logger = logging.getLogger()
    # Check if already added to avoid duplicates
    for handler in root_logger.handlers:
        if isinstance(handler, RedisRingBufferHandler):
            return

    handler = RedisRingBufferHandler(max_lines=max_lines)
    formatter = logging.Formatter("%(asctime)s | %(levelname)s | %(name)s | %(message)s")
    handler.setFormatter(formatter)
    root_logger.addHandler(handler)
    
    # Send a startup log
    logger = logging.getLogger("ax.redis_logger")
    logger.info("Redis Ring Buffer Logger initialized.")
