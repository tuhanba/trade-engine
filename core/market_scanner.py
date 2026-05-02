"""
Market Scanner — Profesyonel Sürüm
Coin Discovery, cooldown kontrolü, hacim ve volatilite filtreleri.
"""
import logging
import sqlite3
from typing import List, Dict

logger = logging.getLogger(__name__)

# Config'den coin evrenini al
try:
    import sys as _sys, os as _os
    _sys.path.insert(0, _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))))
    from config import COIN_UNIVERSE as _COIN_UNIVERSE
except ImportError:
    _COIN_UNIVERSE = []

class MarketScanner:
    def __init__(self, client, db_path="trade_engine.db"):
        self.client = client
        self.db_path = db_path
        self.min_volume = 10_000_000  # 10M USD
        self.min_price = 0.001
        self.coin_universe = set(_COIN_UNIVERSE)  # 92 coin filtresi

    def _get_cooldown_coins(self) -> set:
        """Cooldown'da olan coinleri getirir."""
        try:
            with sqlite3.connect(self.db_path) as conn:
                rows = conn.execute("""
                    SELECT symbol FROM coin_profiles 
                    WHERE danger_score > 0.8
                """).fetchall()
                return {row[0] for row in rows}
        except Exception as e:
            logger.error(f"Cooldown okuma hatası: {e}")
            return set()

    def scan(self) -> List[Dict]:
        """Tüm USDT paritelerini tarar ve filtrelenmiş listeyi döner."""
        try:
            tickers = self.client.futures_ticker()
            exchange_info = self.client.futures_exchange_info()
            
            # Sadece USDT paritelerini ve trading'e açık olanları al
            valid_symbols = {
                s["symbol"]: s for s in exchange_info["symbols"] 
                if s["quoteAsset"] == "USDT" and s["status"] == "TRADING"
            }
            
            cooldown_coins = self._get_cooldown_coins()
            candidates = []
            
            for t in tickers:
                symbol = t["symbol"]
                if symbol not in valid_symbols:
                    continue
                # ── 92 Coin Evreni Filtresi ───────────────────────────────────
                if self.coin_universe and symbol not in self.coin_universe:
                    continue
                if symbol in cooldown_coins:
                    continue
                    
                volume = float(t["quoteVolume"])
                price = float(t["lastPrice"])
                price_change = abs(float(t["priceChangePercent"]))
                
                # Temel filtreler
                if volume < self.min_volume or price < self.min_price:
                    continue
                    
                # Pump/Dump filtresi (çok yüksek değişim)
                if price_change > 30.0:
                    status = "Avoid"
                    score = 0
                elif volume > 50_000_000 and 2.0 < price_change < 15.0:
                    status = "Eligible"
                    score = min(10, (volume / 10_000_000) * 0.5 + price_change * 0.5)
                else:
                    status = "Watch"
                    score = 5
                    
                if status != "Avoid":
                    candidates.append({
                        "symbol": symbol,
                        "volume": volume,
                        "price": price,
                        "price_change": price_change,
                        "status": status,
                        "tradeability_score": round(score, 1)
                    })
                    
            # Skora göre sırala
            candidates.sort(key=lambda x: x["tradeability_score"], reverse=True)
            return candidates
            
        except Exception as e:
            logger.error(f"Market scan hatası: {e}")
            return []
