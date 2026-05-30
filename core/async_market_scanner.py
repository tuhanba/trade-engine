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
    from config import COIN_UNIVERSE, BINANCE_API_KEY, DB_PATH, COIN_UNIVERSE_LIMIT
    from config import MIN_VOLUME_USDT as MIN_VOLUME_USD
    from database import save_coin_library, disable_coin
except ImportError:
    COIN_UNIVERSE = []
    COIN_UNIVERSE_LIMIT = 150
    MIN_VOLUME_USD = 3_000_000
    DB_PATH = "trading.db"
    save_coin_library = None
    disable_coin = None

class AsyncMarketScanner:
    def __init__(self, db_path=None):
        db_path = db_path or DB_PATH
        self.db_path = db_path
        self.min_volume = MIN_VOLUME_USD
        self.min_price = 0.001
        self.coin_universe = set(COIN_UNIVERSE) if COIN_UNIVERSE else set()
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

    def _save_coin_filters(self, symbol: str, sym_info: dict):
        """Coin filtrelerini coin_library tablosuna kaydet."""
        if save_coin_library is None:
            return
        try:
            filters_map = {f["filterType"]: f for f in sym_info.get("filters", [])}
            lot = filters_map.get("LOT_SIZE", {})
            price_f = filters_map.get("PRICE_FILTER", {})
            save_coin_library(symbol, {
                "min_qty": float(lot.get("minQty", "0.001")),
                "step_size": float(lot.get("stepSize", "0.001")),
                "tick_size": float(price_f.get("tickSize", "0.01")),
                "min_notional": float(filters_map.get("MIN_NOTIONAL", {}).get("notional", "5.0")),
            })
        except Exception as e:
            logger.debug(f"Coin filter kayıt hatası {symbol}: {e}")

    async def scan(self) -> List[Dict]:
        start_time = time.time()
        async with aiohttp.ClientSession() as session:
            try:
                tickers_task = self.fetch_tickers(session)
                info_task = self.fetch_exchange_info(session)

                tickers, exchange_info = await asyncio.gather(tickers_task, info_task)

                valid_symbols = {}
                for s in exchange_info["symbols"]:
                    if s["quoteAsset"] == "USDT" and s["status"] == "TRADING":
                        valid_symbols[s["symbol"]] = s
                        # Coin filtrelerini DB'ye kaydet
                        self._save_coin_filters(s["symbol"], s)

                # Delist/suspended coinleri disable et
                if disable_coin:
                    for s in exchange_info["symbols"]:
                        if s["quoteAsset"] == "USDT" and s["status"] != "TRADING":
                            try:
                                disable_coin(s["symbol"], reason=f"status_{s['status']}")
                            except Exception:
                                pass

                cooldown_coins = self._get_cooldown_coins()
                candidates = []

                for t in tickers:
                    symbol = t["symbol"]
                    if symbol not in valid_symbols:
                        continue
                    # COIN_UNIVERSE boşsa tüm coinleri tara (top limit yok)
                    if self.coin_universe and symbol not in self.coin_universe:
                        continue
                    if symbol in cooldown_coins:
                        continue

                    volume = float(t["quoteVolume"])
                    price = float(t["lastPrice"])
                    price_change = abs(float(t["priceChangePercent"]))

                    if volume < self.min_volume or price < self.min_price:
                        continue

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
                if COIN_UNIVERSE_LIMIT > 0:
                    candidates = candidates[:COIN_UNIVERSE_LIMIT]
                logger.info(f"Async Scan completed in {time.time() - start_time:.2f}s. "
                            f"Found {len(candidates)} candidates (limit={COIN_UNIVERSE_LIMIT}) from {len(valid_symbols)} USDT perps.")
                
                # Update pipeline stats in DB for dashboard
                try:
                    from database import update_bot_status
                    update_bot_status("pipeline_scanned", str(len(valid_symbols)))
                    update_bot_status("pipeline_candidate", str(len(candidates)))
                except Exception:
                    pass

                return candidates
            except Exception as e:
                logger.error(f"Async Market scan error: {e}")
                return []
