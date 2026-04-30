import logging
import time
from typing import Optional
from math import floor, log10

from binance.client import Client

from config import BINANCE_API_KEY, BINANCE_API_SECRET
from database import get_conn
from coin_library import is_in_cooldown

logger = logging.getLogger(__name__)

# --- Blacklist ---
BLACKLIST = {
    "AAVEUSDT", "ADAUSDT", "PENGUUSDT", "GUNUSDT",
    "ZECUSDT", "ENAUSDT", "TONUSDT", "HIGHUSDT",
}

# --- Minimumlar ---
MIN_VOLUME_M   = 20.0    # milyon USDT / 24h
MIN_CHANGE_PCT = 0.5     # % mutlak hareket
MAX_SPREAD_PCT = 0.15    # % spread

# --- Exchange info cache (1 saat) ---
_exchange_cache: dict = {}
_cache_ts: float = 0.0
_CACHE_TTL = 3600

_client: Optional[Client] = None


def _get_client() -> Client:
    global _client
    if _client is None:
        _client = Client(BINANCE_API_KEY, BINANCE_API_SECRET)
    return _client


def _load_exchange_info() -> dict:
    """Futures exchangeInfo'yu çek, coin başına filtre bilgisini cache'le."""
    global _exchange_cache, _cache_ts
    if _exchange_cache and (time.time() - _cache_ts) < _CACHE_TTL:
        return _exchange_cache

    try:
        info = _get_client().futures_exchange_info()
    except Exception as e:
        logger.error(f"[MarketScan] exchangeInfo hata: {e}")
        return _exchange_cache  # eski cache'i kullan

    result = {}
    for sym in info.get("symbols", []):
        if sym.get("status") != "TRADING":
            continue
        filters = {f["filterType"]: f for f in sym.get("filters", [])}
        lot  = filters.get("LOT_SIZE", {})
        pf   = filters.get("PRICE_FILTER", {})
        mn   = filters.get("MIN_NOTIONAL", {})
        result[sym["symbol"]] = {
            "step_size":    float(lot.get("stepSize", "0.001")),
            "min_qty":      float(lot.get("minQty", "0.001")),
            "tick_size":    float(pf.get("tickSize", "0.01")),
            "min_notional": float(mn.get("notional", "5")),
        }

    _exchange_cache = result
    _cache_ts = time.time()
    logger.info(f"[MarketScan] exchangeInfo cache güncellendi: {len(result)} sembol")
    return result


def get_symbol_filters(symbol: str) -> Optional[dict]:
    info = _load_exchange_info()
    return info.get(symbol)


def round_qty(symbol: str, qty: float) -> float:
    """Miktarı LOT_SIZE step_size'a göre aşağı yuvarla."""
    f = get_symbol_filters(symbol)
    if not f:
        return qty
    step = f["step_size"]
    if step <= 0:
        return qty
    decimals = max(0, -int(floor(log10(step))))
    return round(floor(qty / step) * step, decimals)


def round_price(symbol: str, price: float) -> float:
    """Fiyatı tick_size'a göre yuvarla."""
    f = get_symbol_filters(symbol)
    if not f:
        return price
    tick = f["tick_size"]
    if tick <= 0:
        return price
    decimals = max(0, -int(floor(log10(tick))))
    return round(round(price / tick) * tick, decimals)


def check_min_notional(symbol: str, qty: float, price: float) -> bool:
    f = get_symbol_filters(symbol)
    if not f:
        return True
    return qty * price >= f["min_notional"]


def _has_open_trade(symbol: str) -> bool:
    """Bu coin için zaten açık trade var mı?"""
    conn = get_conn()
    c = conn.cursor()
    c.execute("SELECT id FROM trades WHERE symbol=? AND status='OPEN' LIMIT 1", (symbol,))
    row = c.fetchone()
    conn.close()
    return row is not None


def scan_market() -> list[dict]:
    """
    Binance Futures'ta işlem gören, tüm filtreleri geçen temiz coin listesi döndür.
    Her eleman: {symbol, volume_m, change_24h, step_size, min_qty, tick_size, min_notional}
    """
    exchange_info = _load_exchange_info()
    if not exchange_info:
        logger.warning("[MarketScan] Exchange info boş, tarama atlandı.")
        return []

    try:
        tickers = _get_client().futures_ticker()
    except Exception as e:
        logger.error(f"[MarketScan] futures_ticker hata: {e}")
        return []

    result = []
    for t in tickers:
        symbol = t.get("symbol", "")

        if not symbol.endswith("USDT"):
            continue
        if symbol in BLACKLIST:
            continue
        if symbol not in exchange_info:
            continue

        volume_m  = float(t.get("quoteVolume", 0)) / 1_000_000
        change_24h = float(t.get("priceChangePercent", 0))

        if volume_m < MIN_VOLUME_M:
            continue
        if abs(change_24h) < MIN_CHANGE_PCT:
            continue

        # Spread kontrolü (bid/ask)
        bid = float(t.get("bidPrice", 0))
        ask = float(t.get("askPrice", 0))
        mid = (bid + ask) / 2 if bid and ask else 0
        if mid > 0 and ask > 0:
            spread_pct = (ask - bid) / mid * 100
            if spread_pct > MAX_SPREAD_PCT:
                continue

        if is_in_cooldown(symbol):
            continue
        if _has_open_trade(symbol):
            continue

        filters = exchange_info[symbol]
        result.append({
            "symbol":       symbol,
            "volume_m":     round(volume_m, 2),
            "change_24h":   round(change_24h, 2),
            "step_size":    filters["step_size"],
            "min_qty":      filters["min_qty"],
            "tick_size":    filters["tick_size"],
            "min_notional": filters["min_notional"],
        })

    # Hacim + momentum skoru ile sırala, en iyi 50 coin'i al
    for r in result:
        r["_score"] = _discovery_score(r["volume_m"], r["change_24h"])
    result.sort(key=lambda x: x["_score"], reverse=True)
    result = result[:50]
    for r in result:
        r.pop("_score", None)

    logger.info(f"[MarketScan] {len(result)} temiz coin bulundu.")
    return result


def _discovery_score(volume_m: float, change_24h: float) -> float:
    """Coin tarama öncelik skoru (yüksek = daha iyi setup potansiyeli)."""
    # Hacim skoru — 20M-500M arası ideal (çok yüksek = slippage düşük ama sinyal az)
    if volume_m >= 500:
        vol_score = 38
    elif volume_m >= 200:
        vol_score = 45
    elif volume_m >= 80:
        vol_score = 40
    elif volume_m >= 40:
        vol_score = 28
    else:
        vol_score = 14

    # Momentum skoru — %1.5-8 arası hareket ideal için scalp
    abs_ch = abs(change_24h)
    if 2.0 <= abs_ch <= 7.0:
        mom_score = 35
    elif 1.0 <= abs_ch <= 10.0:
        mom_score = 22
    elif abs_ch > 10.0:
        mom_score = 8   # aşırı hareket = yüksek risk
    else:
        mom_score = 5

    return vol_score + mom_score
