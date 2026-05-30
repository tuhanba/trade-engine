"""
core/async_market_data.py – CCXT Pro Async WebSocket Veri Katmanı
======================================================================
Binance (veya diğer CCXT destekli borsalar) üzerinden anlık WebSocket
fiyat güncellemeleri ve mum kapanışlarını dinleyen servis.
"""

import asyncio
import logging
from typing import Callable, Dict, Any, List

import ccxt.pro

import config

logger = logging.getLogger("ax.async_market_data")


class AsyncMarketDataService:
    def __init__(self, api_key: str = "", api_secret: str = ""):
        self.exchange = ccxt.pro.binance({
            'apiKey': api_key,
            'secret': api_secret,
            'enableRateLimit': True,
            'options': {
                'defaultType': 'future'
            }
        })
        
        # Callback listeleri
        self.ticker_callbacks: List[Callable[[Dict[str, Any]], None]] = []
        self.kline_callbacks: List[Callable[[Dict[str, Any]], None]] = []
        
        self.running = False
        self._tasks = []

    async def initialize(self):
        """Exchange'i yükler."""
        await self.exchange.load_markets()
        logger.info("CCXT Pro AsyncMarketDataService initialized (Futures).")

    def on_ticker(self, callback: Callable[[Dict[str, Any]], None]):
        """Ticker fiyat güncellemelerini dinlemek için callback ekler."""
        self.ticker_callbacks.append(callback)

    def on_kline(self, callback: Callable[[Dict[str, Any]], None]):
        """Mum kapanış/güncellemelerini dinlemek için callback ekler."""
        self.kline_callbacks.append(callback)

    async def _safe_call(self, cb, data):
        """Callback'leri asenkron ve güvenli bir şekilde çağırır."""
        try:
            if asyncio.iscoroutinefunction(cb):
                await cb(data)
            else:
                cb(data)
        except Exception as e:
            logger.error(f"Error in market data callback: {e}")

    async def start_futures_multiplex(self, symbols: List[str]):
        """
        Belirtilen semboller için ticker izler.
        (CCXT Pro'da çoklu izleme watch_tickers ile yapılır)
        """
        self.running = True
        logger.info(f"Starting watch_tickers for {len(symbols)} symbols...")
        
        task = asyncio.create_task(self._watch_tickers_loop(symbols))
        self._tasks.append(task)
        
    async def start_all_tickers(self):
        """
        Tüm piyasa için mini ticker stream (bütün semboller).
        Binance Futures'da tüm ticker'ları almak için boş watch_tickers çalıştırılır.
        """
        self.running = True
        logger.info("Starting All-Market Futures Ticker Stream via CCXT Pro...")
        
        task = asyncio.create_task(self._watch_all_tickers_loop())
        self._tasks.append(task)
        
    async def _watch_tickers_loop(self, symbols: List[str]):
        while self.running:
            try:
                tickers = await self.exchange.watch_tickers(symbols)
                for symbol, data in tickers.items():
                    # Format to match existing event bus expectations if needed
                    event_data = {
                        'e': '24hrTicker',
                        's': symbol.replace('/', '').replace(':USDT', ''), # e.g. BTC/USDT:USDT -> BTCUSDT
                        'c': str(data.get('last', 0))
                    }
                    for cb in self.ticker_callbacks:
                        asyncio.create_task(self._safe_call(cb, event_data))
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"CCXT Watch Tickers Error: {e}")
                await asyncio.sleep(2)

    async def _watch_all_tickers_loop(self):
        """Tüm ticker'ları dinleme döngüsü."""
        # Binance tüm ticker'ları destekler. Parametre vermezsek hepsini çeker.
        while self.running:
            try:
                tickers = await self.exchange.watch_tickers()
                for symbol, data in tickers.items():
                    # USDT Futures sembollerini ayır
                    if not symbol.endswith('USDT'):
                        continue
                    clean_sym = symbol.replace('/', '').replace(':USDT', '')
                    event_data = {
                        'e': '24hrTicker',
                        's': clean_sym,
                        'c': str(data.get('last', 0))
                    }
                    for cb in self.ticker_callbacks:
                        asyncio.create_task(self._safe_call(cb, event_data))
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"CCXT Watch All Tickers Error: {e}")
                await asyncio.sleep(2)

    async def stop(self):
        """Bağlantıları kapatır."""
        self.running = False
        for t in self._tasks:
            t.cancel()
        await self.exchange.close()
        logger.info("AsyncMarketDataService (CCXT) stopped.")
