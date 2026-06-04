"""
core/redis_feature_store.py - Phase G Redis Feature Store Module
================================================================
Provides ultra-low latency feature storage for technical indicators.
"""

import json
import logging
import redis
import config

logger = logging.getLogger("ax.redis_feature_store")

_client = None

def _get_redis_client():
    global _client
    if _client is not None:
        return _client
    if not getattr(config, "REDIS_ENABLED", True):
        return None
    try:
        _client = redis.Redis(
            host=getattr(config, "REDIS_HOST", "127.0.0.1"),
            port=getattr(config, "REDIS_PORT", 6379),
            db=getattr(config, "REDIS_DB", 0),
            password=getattr(config, "REDIS_PASSWORD", None),
            decode_responses=True
        )
        # Test connection
        _client.ping()
        return _client
    except Exception as e:
        logger.warning(f"Failed to initialize Redis Feature Store: {e}")
        _client = None
        return None


def set_features(symbol: str, features: dict, ttl: int = 86400) -> bool:
    """Stores a feature dictionary in Redis for the given symbol."""
    client = _get_redis_client()
    if client is None:
        return False
    try:
        key = f"features:{symbol}"
        client.set(key, json.dumps(features), ex=ttl)
        return True
    except Exception as e:
        logger.warning(f"Error writing features to Redis for {symbol}: {e}")
        return False


def get_features(symbol: str) -> dict:
    """Retrieves features dictionary from Redis for the given symbol."""
    client = _get_redis_client()
    if client is None:
        return {}
    try:
        key = f"features:{symbol}"
        data = client.get(key)
        if data:
            return json.loads(data)
    except Exception as e:
        logger.warning(f"Error reading features from Redis for {symbol}: {e}")
    return {}
