"""
Market Scanner — Coin Discovery
Sabit listeye bağlı kalmadan Binance'den USDT paritelerini çeker ve filtreler.
"""
import logging
from typing import List, Dict

logger = logging.getLogger(__name__)

class MarketScanner:
    def __init__(self, client):
        self.client = client
        self.min_volume = 10_000_000  # 10M USD
        self.min_price = 0.001
        
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
            
            candidates = []
            for t in tickers:
                symbol = t["symbol"]
                if symbol not in valid_symbols:
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
