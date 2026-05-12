"""
Coin Library — Dinamik Sembol Yönetimi ve Kalite Filtreleri
"""
import logging
import sqlite3
import time
from typing import List, Dict, Set

logger = logging.getLogger(__name__)

class CoinLibrary:
    def __init__(self, client, db_path="trade_engine.db"):
        self.client = client
        self.db_path = db_path
        # Kalite Eşikleri
        self.MIN_VOLUME_24H = 15_000_000  # 15M USD altı 'ölü' kabul edilir
        self.MAX_VOLATILITY_24H = 40.0    # %40+ hareket manipülasyon riskidir
        self.MIN_VOLATILITY_24H = 1.5     # %1.5 altı 'hareketsiz' kabul edilir
        self.MAX_SPREAD_PCT = 0.15        # %0.15+ spread likidite sorunudur
        self.TOP_N_COINS = 100            # En iyi 100 coin'i takip et

    def refresh_universe(self) -> List[str]:
        """Binance'ten güncel verileri çeker, filtreler ve evreni günceller."""
        try:
            logger.info("Coin evreni güncelleniyor...")
            tickers = self.client.futures_ticker()
            exchange_info = self.client.futures_exchange_info()
            
            # Geçerli semboller (USDT Perpetual)
            valid_symbols = {
                s["symbol"]: s for s in exchange_info["symbols"]
                if s["quoteAsset"] == "USDT"
                and s["status"] == "TRADING"
                and s.get("contractType") == "PERPETUAL"
            }

            candidates = []
            for t in tickers:
                symbol = t["symbol"]
                if symbol not in valid_symbols:
                    continue

                volume = float(t["quoteVolume"])
                price_change = abs(float(t["priceChangePercent"]))
                
                # 1. Hacim Filtresi
                if volume < self.MIN_VOLUME_24H:
                    continue
                
                # 2. Volatilite Filtresi (Aşırı pump/dump veya ölü tahta)
                if price_change > self.MAX_VOLATILITY_24H or price_change < self.MIN_VOLATILITY_24H:
                    continue

                # 3. Skorlama (Hacim ve Volatilite ağırlıklı)
                score = (volume / 100_000_000) * 5.0 + (price_change / 10.0) * 5.0
                
                candidates.append({
                    "symbol": symbol,
                    "score": score,
                    "volume": volume,
                    "volatility": price_change
                })

            # Skora göre sırala ve ilk N tanesini al
            candidates.sort(key=lambda x: x["score"], reverse=True)
            top_coins = [c["symbol"] for c in candidates[:self.TOP_N_COINS]]
            
            # BTC ve ETH her zaman dahil olmalı
            for essential in ["BTCUSDT", "ETHUSDT"]:
                if essential not in top_coins:
                    top_coins.append(essential)

            logger.info(f"Yeni coin evreni oluşturuldu: {len(top_coins)} sembol.")
            self._update_db_universe(top_coins)
            return top_coins

        except Exception as e:
            logger.error(f"Coin evreni yenileme hatası: {e}")
            return []

    def _update_db_universe(self, symbols: List[str]):
        """Veritabanındaki aktif coin listesini günceller."""
        try:
            with sqlite3.connect(self.db_path) as conn:
                conn.execute("CREATE TABLE IF NOT EXISTS active_universe (symbol TEXT PRIMARY KEY, last_updated REAL)")
                conn.execute("DELETE FROM active_universe")
                ts = time.time()
                conn.executemany(
                    "INSERT INTO active_universe (symbol, last_updated) VALUES (?, ?)",
                    [(s, ts) for s in symbols]
                )
        except Exception as e:
            logger.error(f"DB evren güncelleme hatası: {e}")

    def get_active_universe(self) -> List[str]:
        """Veritabanından veya önbellekten aktif evreni döner."""
        try:
            with sqlite3.connect(self.db_path) as conn:
                rows = conn.execute("SELECT symbol FROM active_universe").fetchall()
                return [r[0] for r in rows]
        except Exception:
            return []

def get_coin_params(symbol: str) -> Dict:
    """Coin'e özel hassasiyet ve geçmiş performans verilerini döner."""
    # Varsayılan değerler
    return {
        "win_rate": 0.55,
        "avg_profit": 0.012,
        "volatility_mult": 1.0,
        "cooldown_minutes": 30
    }
