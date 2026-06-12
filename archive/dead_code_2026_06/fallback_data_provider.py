"""
core/fallback_data_provider.py — CoinGecko Fallback Data Provider
==================================================================
Binance API erişilemezken (TR engeli, timeout, vb.) paper trading için
CoinGecko üzerinden fiyat verisi çeker.

Kullanım:
  provider = FallbackDataProvider()
  price = provider.get_price("BTCUSDT")
  candles = provider.get_candles("ETHUSDT", "5m", 100)
"""
import logging
import time
import requests
from typing import Dict, List, Optional
from datetime import datetime, timezone

logger = logging.getLogger(__name__)

# CoinGecko symbol mapping (USDT pair -> coingecko id)
_SYMBOL_MAP = {
    "BTCUSDT": "bitcoin", "ETHUSDT": "ethereum", "BNBUSDT": "binancecoin",
    "SOLUSDT": "solana", "XRPUSDT": "ripple", "DOGEUSDT": "dogecoin",
    "ADAUSDT": "cardano", "AVAXUSDT": "avalanche-2", "DOTUSDT": "polkadot",
    "LINKUSDT": "chainlink", "MATICUSDT": "matic-network", "UNIUSDT": "uniswap",
    "LTCUSDT": "litecoin", "ATOMUSDT": "cosmos", "NEARUSDT": "near",
    "APTUSDT": "aptos", "OPUSDT": "optimism", "ARBUSDT": "arbitrum",
    "FILUSDT": "filecoin", "INJUSDT": "injective-protocol",
    "SUIUSDT": "sui", "SEIUSDT": "sei-network", "TIAUSDT": "celestia",
    "FETUSDT": "fetch-ai", "RENDERUSDT": "render-token",
    "WIFUSDT": "dogwifcoin", "PEPEUSDT": "pepe", "FLOKIUSDT": "floki",
    "ONDOUSDT": "ondo-finance", "JUPUSDT": "jupiter-exchange-solana",
}

# Reverse map
_ID_TO_SYMBOL = {v: k for k, v in _SYMBOL_MAP.items()}


class FallbackDataProvider:
    """CoinGecko tabanlı fallback veri sağlayıcı."""

    BASE_URL = "https://api.coingecko.com/api/v3"

    def __init__(self):
        self.session = requests.Session()
        self.session.headers.update({
            "Accept": "application/json",
            "User-Agent": "AX-TradeEngine/5.0",
        })
        self._price_cache: Dict[str, tuple] = {}  # symbol -> (price, timestamp)
        self._cache_ttl = 30  # saniye
        self._last_request = 0
        self._rate_limit_delay = 1.5  # CoinGecko free tier: ~30 req/min

    def _rate_limit(self):
        elapsed = time.time() - self._last_request
        if elapsed < self._rate_limit_delay:
            time.sleep(self._rate_limit_delay - elapsed)
        self._last_request = time.time()

    def _get(self, endpoint: str, params: dict = None) -> Optional[dict]:
        self._rate_limit()
        try:
            url = f"{self.BASE_URL}/{endpoint}"
            r = self.session.get(url, params=params, timeout=10)
            if r.status_code == 429:
                logger.warning("[Fallback] CoinGecko rate limit — 60s bekleniyor")
                time.sleep(60)
                r = self.session.get(url, params=params, timeout=10)
            r.raise_for_status()
            return r.json()
        except Exception as e:
            logger.error(f"[Fallback] CoinGecko hatası: {e}")
            return None

    def _symbol_to_id(self, symbol: str) -> Optional[str]:
        return _SYMBOL_MAP.get(symbol.upper())

    def get_price(self, symbol: str) -> Optional[float]:
        """Anlık fiyat al — cache'li."""
        # Cache kontrol
        if symbol in self._price_cache:
            price, ts = self._price_cache[symbol]
            if time.time() - ts < self._cache_ttl:
                return price

        cg_id = self._symbol_to_id(symbol)
        if not cg_id:
            return None

        data = self._get("simple/price", {
            "ids": cg_id,
            "vs_currencies": "usd",
            "include_24hr_change": "true",
            "include_24hr_vol": "true",
        })
        if not data or cg_id not in data:
            return None

        price = data[cg_id].get("usd")
        if price:
            self._price_cache[symbol] = (price, time.time())
        return price

    def get_bulk_prices(self, symbols: List[str] = None) -> Dict[str, dict]:
        """Birden fazla coin fiyatını tek seferde al."""
        if symbols is None:
            symbols = list(_SYMBOL_MAP.keys())

        ids = []
        id_to_sym = {}
        for s in symbols:
            cg_id = self._symbol_to_id(s)
            if cg_id:
                ids.append(cg_id)
                id_to_sym[cg_id] = s

        if not ids:
            return {}

        # CoinGecko batch — max 250 per request
        results = {}
        for i in range(0, len(ids), 50):
            batch = ids[i:i+50]
            data = self._get("simple/price", {
                "ids": ",".join(batch),
                "vs_currencies": "usd",
                "include_24hr_change": "true",
                "include_24hr_vol": "true",
            })
            if not data:
                continue

            for cg_id, info in data.items():
                sym = id_to_sym.get(cg_id, cg_id.upper() + "USDT")
                price = info.get("usd", 0)
                self._price_cache[sym] = (price, time.time())
                results[sym] = {
                    "symbol": sym,
                    "price": price,
                    "volume": info.get("usd_24h_vol", 0),
                    "price_change": info.get("usd_24h_change", 0),
                }

        return results

    def get_market_candidates(self, min_volume: float = 5_000_000) -> List[dict]:
        """
        CoinGecko markets endpoint üzerinden tarama yap.
        Binance scanner yerine fallback olarak kullanılır.
        """
        data = self._get("coins/markets", {
            "vs_currency": "usd",
            "order": "volume_desc",
            "per_page": 100,
            "page": 1,
            "sparkline": "false",
            "price_change_percentage": "24h",
        })
        if not data:
            return []

        candidates = []
        for coin in data:
            cg_id = coin.get("id", "")
            symbol = _ID_TO_SYMBOL.get(cg_id)
            if not symbol:
                continue

            volume = coin.get("total_volume", 0) or 0
            price = coin.get("current_price", 0) or 0
            change_24h = abs(coin.get("price_change_percentage_24h", 0) or 0)

            if volume < min_volume:
                continue

            if change_24h > 30:
                status = "Avoid"
            elif volume > 20_000_000 and 1.0 < change_24h < 20.0:
                score = min(10, (volume / 10_000_000) * 0.4 + change_24h * 0.4 + 2.0)
                status = "Eligible"
            else:
                score = 3
                status = "Watch"

            if status != "Avoid":
                candidates.append({
                    "symbol": symbol,
                    "volume": volume,
                    "price": price,
                    "price_change": change_24h,
                    "status": status,
                    "tradeability_score": round(score, 1),
                    "source": "coingecko",
                })

        candidates.sort(key=lambda x: x["tradeability_score"], reverse=True)
        logger.info(f"[Fallback] CoinGecko scan: {len(candidates)} candidates")
        return candidates

    def is_available(self) -> bool:
        """CoinGecko erişilebilir mi?"""
        try:
            r = self.session.get(f"{self.BASE_URL}/ping", timeout=5)
            return r.status_code == 200
        except Exception:
            return False

    def get_supported_symbols(self) -> List[str]:
        return list(_SYMBOL_MAP.keys())


# ── Binance erişim testi ──
def test_binance_connectivity() -> bool:
    """Binance API'ye erişim var mı?"""
    try:
        r = requests.get("https://fapi.binance.com/fapi/v1/ping", timeout=5)
        return r.status_code == 200
    except Exception:
        return False
