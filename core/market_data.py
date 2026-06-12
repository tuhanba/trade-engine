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


# ── Güncel fiyat ve ticker önbelleği ────────────────────────────────
_PRICE_CACHE = {}
# NEDEN (Faz 1.4): WebSocket koptuğunda _PRICE_CACHE donar ama get_current_price
# cache'i tercih ettiği için bayat fiyat sessizce kullanılırdı. Her fiyat
# güncellemesinin zamanı tutulur ki bayatlık (örn. >120 sn) tespit edilebilsin.
_PRICE_CACHE_TS = {}
_TICKER_CACHE = {}

def set_cached_price(symbol: str, price: float):
    _PRICE_CACHE[symbol] = price
    _PRICE_CACHE_TS[symbol] = time.time()

def get_price_age(symbol: str) -> Optional[float]:
    """Sembolün cache'teki fiyatının yaşını saniye cinsinden döner.

    None = sembol cache'te hiç yok (WS verisi gelmemiş — REST yolu kullanılır,
    bayatlık riski yok). Sayı = son WS güncellemesinden bu yana geçen saniye.
    """
    ts = _PRICE_CACHE_TS.get(symbol)
    if ts is None:
        return None
    return max(0.0, time.time() - ts)

def set_cached_ticker(symbol: str, ticker_data: dict):
    _TICKER_CACHE[symbol] = ticker_data

def get_cached_ticker(symbol: str) -> Optional[dict]:
    return _TICKER_CACHE.get(symbol)

@with_exponential_backoff(max_retries=2, base_delay=1.0)
def get_current_price(symbol: str) -> Optional[float]:
    """
    Sembolün güncel fiyatını döner.
    Öncelikle RAM (WebSocket) önbelleğini kullanır.
    Yoksa REST API ile çeker.
    """
    if symbol in _PRICE_CACHE:
        return _PRICE_CACHE[symbol]
        
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


@with_exponential_backoff(max_retries=2, base_delay=1.0)
def get_book_ticker(symbol: str) -> Optional[dict]:
    """
    Sembolün en iyi bid/ask fiyatını döner (makas hesabı için).
    Hata durumunda None döner.
    """
    try:
        resp = requests.get(
            f"{_BASE_URL}/fapi/v1/ticker/bookTicker",
            params={"symbol": symbol},
            timeout=_TIMEOUT,
        )
        resp.raise_for_status()
        return resp.json()
    except Exception as exc:
        logger.error("Book ticker alınamadı [%s]: %s", symbol, exc)
        return None
