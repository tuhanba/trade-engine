"""
Async Market Scanner v3.0
Asenkron veri çekme ve işleme ile yüksek performanslı tarama.
"""
import asyncio
import aiohttp
import time
import logging
import sqlite3
from typing import List, Dict

logger = logging.getLogger(__name__)

try:
    from config import COIN_UNIVERSE, BINANCE_API_KEY
    from database import save_market_snapshot, save_scanned_coin
except ImportError:
    COIN_UNIVERSE = []
    save_market_snapshot = None
    save_scanned_coin = None

class AsyncMarketScanner:
    def __init__(self, db_path="trade_engine.db"):
        self.db_path = db_path
        self.min_volume = 5_000_000
        self.min_price = 0.001
        self.coin_universe = set(COIN_UNIVERSE)
        self.base_url = "https://fapi.binance.com"

    async def fetch_tickers(self, session):
        async with session.get(f"{self.base_url}/fapi/v1/ticker/24hr") as response:
            return await response.json()

    async def fetch_exchange_info(self, session):
        async with session.get(f"{self.base_url}/fapi/v1/exchangeInfo") as response:
            return await response.json()

    def _get_cooldown_coins(self) -> set:
        try:
            with sqlite3.connect(self.db_path) as conn:
                conn.row_factory = sqlite3.Row
                rows = conn.execute(
                    "SELECT symbol FROM coin_profiles WHERE danger_score > 0.8"
                ).fetchall()
                return {r["symbol"] for r in rows}
        except Exception:
            return set()

    async def scan(self) -> List[Dict]:
        start_time = time.time()
        async with aiohttp.ClientSession() as session:
            try:
                tickers_task = self.fetch_tickers(session)
                info_task = self.fetch_exchange_info(session)
                
                tickers, exchange_info = await asyncio.gather(tickers_task, info_task)
                
                valid_symbols = {
                    s["symbol"]: s for s in exchange_info["symbols"] 
                    if s["quoteAsset"] == "USDT" and s["status"] == "TRADING"
                }
                
                cooldown_coins = self._get_cooldown_coins()
                candidates = []
                ts = time.time()

                for t in tickers:
                    symbol = t["symbol"]
                    if symbol not in valid_symbols: continue
                    if self.coin_universe and symbol not in self.coin_universe: continue
                    if symbol in cooldown_coins: continue
                    
                    volume = float(t["quoteVolume"])
                    price = float(t["lastPrice"])
                    price_change = abs(float(t["priceChangePercent"]))
                    
                    if volume < self.min_volume or price < self.min_price: continue
                    
                    if price_change > 30.0:
                        status, score, reason = "Avoid", 0, "pump_dump_risk"
                    elif volume > 20_000_000 and 1.0 < price_change < 20.0:
                        status, score, reason = "Eligible", min(10, (volume / 10_000_000) * 0.4 + price_change * 0.4 + 2.0), "eligible"
                    else:
                        status, score, reason = "Watch", 3, "watch_low_energy"
                        
                    if status != "Avoid":
                        candidates.append({
                            "symbol": symbol,
                            "volume": volume,
                            "price": price,
                            "price_change": price_change,
                            "status": status,
                            "tradeability_score": round(score, 1),
                        })

                candidates.sort(key=lambda x: x["tradeability_score"], reverse=True)
                logger.info(f"Async Scan completed in {time.time() - start_time:.2f}s. Found {len(candidates)} candidates.")
                return candidates
            except Exception as e:
                logger.error(f"Async Market scan error: {e}")
                return []
