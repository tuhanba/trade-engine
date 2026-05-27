"""
core/market_data.py – Binance public market data modülü.

Sadece public endpoint kullanır. Private API yok.
Hata durumunda crash olmaz, boş sonuç döner.
"""

from __future__ import annotations

import logging
import time
import functools
import requests
from typing import Any, Optional

logger = logging.getLogger("ax.market_data")

_BASE_URL = "https://fapi.binance.com"
_TIMEOUT = 15

# ── Exponential Backoff Decorator ───────────────────────────────────

def with_exponential_backoff(max_retries=3, base_delay=1.0):
    def decorator(func):
        @functools.wraps(func)
        def wrapper(*args, **kwargs):
            delay = base_delay
            for attempt in range(max_retries + 1):
                try:
                    return func(*args, **kwargs)
                except requests.exceptions.HTTPError as e:
                    if e.response is not None and e.response.status_code == 429:
                        if attempt < max_retries:
                            logger.warning("Binance API Ban (429)! Exponential backoff aktif: %d sn bekle (Deneme %d/%d)", delay, attempt+1, max_retries)
                            time.sleep(delay)
                            delay *= 2
                            continue
                    # Diğer hatalarda (veya 429'un son denemesinde) hatayı fırlat
                    raise
        return wrapper
    return decorator


# ── Public Tickers ──────────────────────────────────────────────────

@with_exponential_backoff(max_retries=3, base_delay=2.0)
def get_public_tickers() -> list[dict]:
    """
    Binance Futures 24hr ticker bilgisini çeker.
    Hata varsa boş liste döner.
    """
    try:
        resp = requests.get(
            f"{_BASE_URL}/fapi/v1/ticker/24hr",
            timeout=_TIMEOUT,
        )
        resp.raise_for_status()
        return resp.json()
    except Exception as exc:
        logger.error("Ticker verisi alınamadı: %s", exc)
        return []


# ── Klines (mum verileri) ──────────────────────────────────────────

@with_exponential_backoff(max_retries=3, base_delay=1.0)
def get_klines(
    symbol: str,
    interval: str = "5m",
    limit: int = 100,
) -> list[list]:
    """
    Belirli sembol için mum verilerini çeker.
    Hata varsa boş liste döner.
    """
    try:
        resp = requests.get(
            f"{_BASE_URL}/fapi/v1/klines",
            params={"symbol": symbol, "interval": interval, "limit": limit},
            timeout=_TIMEOUT,
        )
        resp.raise_for_status()
        return resp.json()
    except Exception as exc:
        logger.error("Kline verisi alınamadı [%s]: %s", symbol, exc)
        return []


# ── Güncel fiyat ────────────────────────────────────────────────────

@with_exponential_backoff(max_retries=2, base_delay=1.0)
def get_current_price(symbol: str) -> Optional[float]:
    """
    Sembolün güncel fiyatını döner.
    Hata varsa None döner.
    """
    try:
        resp = requests.get(
            f"{_BASE_URL}/fapi/v1/ticker/price",
            params={"symbol": symbol},
            timeout=_TIMEOUT,
        )
        resp.raise_for_status()
        data = resp.json()
        return float(data.get("price", 0))
    except Exception as exc:
        logger.error("Güncel fiyat alınamadı [%s]: %s", symbol, exc)
        return None


# ── Filtreleme ──────────────────────────────────────────────────────

def filter_usdt_symbols(
    tickers: list[dict],
    min_volume_usdt: float = 5_000_000.0,
    min_move_pct: float = 1.0,
) -> list[dict]:
    """
    USDT perpetual sembollerini hacim ve hareket yüzdesine göre filtreler.

    Args:
        tickers: get_public_tickers() çıktısı
        min_volume_usdt: minimum 24h USDT hacmi
        min_move_pct: minimum |priceChangePercent|

    Returns:
        filtrelenmiş ticker listesi
    """
    filtered = []
    for t in tickers:
        symbol = t.get("symbol", "")
        if not symbol.endswith("USDT"):
            continue

        try:
            volume_usdt = float(t.get("quoteVolume", 0))
            change_pct = abs(float(t.get("priceChangePercent", 0)))
        except (ValueError, TypeError):
            continue

        if volume_usdt >= min_volume_usdt and change_pct >= min_move_pct:
            filtered.append(t)

    logger.info(
        "Filtre: %d/%d sembol geçti (vol>=%s, move>=%s%%)",
        len(filtered), len(tickers), min_volume_usdt, min_move_pct,
    )
    return filtered
