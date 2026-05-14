# src/data/data_feed.py

import time
import ccxt
from utils.logger import setup_logger


class DataFeed:
    """
    ccxt üzerinden borsa verisi çeker ve EventSystem'e yayar.
    Desteklenen: Binance, Alpaca (ccxt destekleyen her borsa)
    """

    def __init__(self, config: dict, event_system=None):
        self.config = config
        self.event_system = event_system
        self.logger = setup_logger(__name__)
        self.running = False
        self.exchange = self._init_exchange()

    def _init_exchange(self):
        source = self.config.get("source", "binance")
        api_key = self.config.get("api_key", "")
        api_secret = self.config.get("api_secret", "")

        exchange_class = getattr(ccxt, source, None)
        if exchange_class is None:
            raise ValueError(f"Unsupported exchange: {source}")

        exchange = exchange_class({
            "apiKey": api_key,
            "secret": api_secret,
            "enableRateLimit": True,
        })
        self.logger.info(f"Exchange initialized: {source}")
        return exchange

    def fetch_ohlcv(self, symbol: str, timeframe: str, limit: int = 100) -> list:
        """OHLCV mum verisi çeker."""
        try:
            raw = self.exchange.fetch_ohlcv(symbol, timeframe, limit=limit)
            candles = [
                {
                    "timestamp": c[0],
                    "open": c[1],
                    "high": c[2],
                    "low": c[3],
                    "close": c[4],
                    "volume": c[5],
                }
                for c in raw
            ]
            return candles
        except ccxt.NetworkError as e:
            self.logger.error(f"Network error fetching {symbol}: {e}")
            return []
        except ccxt.ExchangeError as e:
            self.logger.error(f"Exchange error fetching {symbol}: {e}")
            return []

    def run(self):
        self.running = True
        symbols = self.config.get("symbols", [])
        timeframe = self.config.get("timeframe", "1m")
        interval = self.config.get("poll_interval", 60)  # saniye

        self.logger.info(f"DataFeed started. Symbols: {symbols}, TF: {timeframe}")

        while self.running:
            for symbol in symbols:
                candles = self.fetch_ohlcv(symbol, timeframe)
                if candles and self.event_system:
                    self.event_system.publish("market_data", {
                        "symbol": symbol,
                        "candles": candles
                    })
                    self.logger.debug(f"Published {len(candles)} candles for {symbol}")
            time.sleep(interval)

    def stop(self):
        self.running = False
        self.logger.info("DataFeed stopped.")
