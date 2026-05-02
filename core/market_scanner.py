"""
Market Scanner — Profesyonel Sürüm
Coin Discovery, cooldown kontrolü, hacim ve volatilite filtreleri.
"""
import logging
import sqlite3
from typing import List, Dict
import time

logger = logging.getLogger(__name__)

# Config'den coin evrenini al
try:
    import sys as _sys, os as _os
    _sys.path.insert(0, _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))))
    from config import COIN_UNIVERSE as _COIN_UNIVERSE
    from database import save_market_snapshot, save_scanned_coin
except ImportError:
    _COIN_UNIVERSE = []
    save_market_snapshot = None
    save_scanned_coin = None

class MarketScanner:
    def __init__(self, client, db_path="trade_engine.db"):
        self.client = client
        self.db_path = db_path
        self.min_volume = 10_000_000  # 10M USD
        self.min_price = 0.001
        self.coin_universe = set(_COIN_UNIVERSE)  # 92 coin filtresi

    def _get_cooldown_coins(self) -> set:
        """Cooldown'da olan coinleri getirir (danger_score kayıtları `coin_profile` tablosunda)."""
        try:
            with sqlite3.connect(self.db_path) as conn:
                conn.row_factory = sqlite3.Row
                try:
                    rows = conn.execute(
                        "SELECT symbol FROM coin_profile WHERE danger_score > 0.8"
                    ).fetchall()
                except Exception:
                    try:
                        rows = conn.execute(
                            "SELECT symbol FROM coin_profiles WHERE danger_score > 0.8"
                        ).fetchall()
                    except Exception:
                        rows = []
                return {r["symbol"] if isinstance(r, sqlite3.Row) else r[0] for r in rows}
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
            
            ts = time.time()
            for t in tickers:
                symbol = t["symbol"]
                if symbol not in valid_symbols:
                    continue
                # ── 92 Coin Evreni Filtresi ───────────────────────────────────
                if self.coin_universe and symbol not in self.coin_universe:
                    continue
                if symbol in cooldown_coins:
                    self._persist_scan(symbol, ts, "Rejected", "cooldown")
                    continue
                    
                volume = float(t["quoteVolume"])
                price = float(t["lastPrice"])
                price_change = abs(float(t["priceChangePercent"]))
                
                # Temel filtreler
                if volume < self.min_volume:
                    self._persist_scan(symbol, ts, "Rejected", "low_volume", volume=volume, price=price, price_change=price_change)
                    continue
                if price < self.min_price:
                    self._persist_scan(symbol, ts, "Rejected", "bad_price", volume=volume, price=price, price_change=price_change)
                    continue
                    
                # Pump/Dump filtresi (çok yüksek değişim)
                if price_change > 30.0:
                    status = "Avoid"
                    score = 0
                    reason = "pump_dump_risk"
                elif volume > 20_000_000 and 1.0 < price_change < 20.0:
                    # Eligible: hacim >20M USDT ve anlamlı hareket
                    status = "Eligible"
                    score = min(10, (volume / 10_000_000) * 0.4 + price_change * 0.4 + 2.0)
                    reason = "eligible"
                elif volume > 10_000_000 and price_change > 0.5:
                    # Watch: orta hacim, küçük hareket
                    status = "Watch"
                    score = min(7, (volume / 10_000_000) * 0.3 + price_change * 0.3 + 1.0)
                    reason = "watch"
                else:
                    status = "Watch"
                    score = 3
                    reason = "watch_low_energy"
                    
                if status != "Avoid":
                    metrics = self._tradeability_components(volume, price_change)
                    candidates.append({
                        "symbol": symbol,
                        "volume": volume,
                        "price": price,
                        "price_change": price_change,
                        "spread_percent": 0.0,
                        "atr_percent": min(20.0, price_change / 2.0),
                        "funding_rate": 0.0,
                        "open_interest_change": 0.0,
                        "status": status,
                        "tradeability_score": round(score, 1),
                        **metrics,
                    })
                    self._persist_scan(symbol, ts, status, reason, volume=volume, price=price, price_change=price_change, score=score, metrics=metrics)
                else:
                    self._persist_scan(symbol, ts, "Rejected", reason, volume=volume, price=price, price_change=price_change, score=score)
                    
            # Skora göre sırala
            candidates.sort(key=lambda x: x["tradeability_score"], reverse=True)
            return candidates
            
        except Exception as e:
            logger.error(f"Market scan hatası: {e}")
            return []

    def _tradeability_components(self, volume: float, price_change: float) -> Dict:
        volume_score = min(10.0, (volume / 20_000_000) * 10.0)
        volatility_score = min(10.0, max(0.0, abs(price_change) / 2.0))
        spread_score = 8.0
        orderbook_depth_score = min(10.0, volume_score * 0.9)
        open_interest_score = min(10.0, volume_score * 0.7)
        funding_score = 6.0
        trend_cleanliness_score = min(10.0, volatility_score * 0.8)
        pump_dump_penalty = 4.0 if abs(price_change) > 20 else 0.0
        correlation_penalty = 0.0
        return {
            "volume_score": round(volume_score, 2),
            "volatility_score": round(volatility_score, 2),
            "spread_score": round(spread_score, 2),
            "orderbook_depth_score": round(orderbook_depth_score, 2),
            "open_interest_score": round(open_interest_score, 2),
            "funding_score": round(funding_score, 2),
            "trend_cleanliness_score": round(trend_cleanliness_score, 2),
            "pump_dump_penalty": round(pump_dump_penalty, 2),
            "correlation_penalty": round(correlation_penalty, 2),
        }

    def _persist_scan(self, symbol: str, ts: float, status: str, reason: str, volume: float = 0.0, price: float = 0.0, price_change: float = 0.0, score: float = 0.0, metrics: Dict | None = None):
        metrics = metrics or self._tradeability_components(volume, price_change)
        if save_market_snapshot:
            save_market_snapshot({
                "symbol": symbol,
                "timestamp": ts,
                "price": price,
                "volume_24h": volume,
                "price_change_24h": price_change,
                "atr_percent": min(20.0, abs(price_change) / 2.0),
                "spread_percent": 0.0,
                "funding_rate": 0.0,
                "open_interest_change": 0.0,
                "tradeability_score": score,
                "scanner_status": status,
                "scanner_reason": reason,
            })
        if save_scanned_coin:
            save_scanned_coin({
                "symbol": symbol,
                "timestamp": ts,
                "scanner_status": status,
                "scanner_reason": reason,
                "volume": volume,
                "volatility": abs(price_change),
                "spread": 0.0,
                "price_change": price_change,
                "funding": 0.0,
                "open_interest": 0.0,
                "trend_cleanliness": metrics.get("trend_cleanliness_score", 0),
                "tradeability_score": score,
                **metrics,
            })
