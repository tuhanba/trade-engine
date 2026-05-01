"""
Trigger Engine
Giriş onayı, setup kalitesi ve anlık fiyatı belirler.
"""
import logging
import pandas as pd

logger = logging.getLogger(__name__)

class TriggerEngine:
    def __init__(self, client):
        self.client = client

    def get_candles(self, symbol: str, interval: str, limit: int) -> pd.DataFrame:
        try:
            klines = self.client.futures_klines(symbol=symbol, interval=interval, limit=limit)
            df = pd.DataFrame(klines, columns=[
                "time", "open", "high", "low", "close", "volume",
                "ct", "qav", "nt", "tbbav", "tbqav", "ignore"
            ])
            for col in ["open", "high", "low", "close", "volume"]:
                df[col] = df[col].astype(float)
            return df
        except Exception as e:
            logger.error(f"Mum verisi alınamadı {symbol}: {e}")
            return pd.DataFrame()

    def analyze(self, symbol: str, direction: str) -> dict:
        """Trigger analizi yapar ve setup kalitesi döner."""
        if direction == "NO TRADE":
            return {"quality": "D", "score": 0, "entry": 0}

        df5 = self.get_candles(symbol, "5m", 50)
        if df5.empty:
            return {"quality": "D", "score": 0, "entry": 0}

        c = df5["close"].iloc[-1]
        v = df5["volume"].iloc[-1]
        avg_v = df5["volume"].iloc[-20:-1].mean()

        score = 5.0
        quality = "C"

        # Hacim onayı
        if v > avg_v * 1.5:
            score += 2.0
            quality = "B"
        if v > avg_v * 2.5:
            score += 3.0
            quality = "A"

        # Basit mum formasyonu onayı
        body = abs(df5["close"].iloc[-1] - df5["open"].iloc[-1])
        wick_up = df5["high"].iloc[-1] - max(df5["close"].iloc[-1], df5["open"].iloc[-1])
        wick_dn = min(df5["close"].iloc[-1], df5["open"].iloc[-1]) - df5["low"].iloc[-1]

        if direction == "LONG" and body > wick_up and wick_dn > body:
            score += 2.0
            if quality == "A": quality = "A+"
            elif quality == "B": quality = "A"
        elif direction == "SHORT" and body > wick_dn and wick_up > body:
            score += 2.0
            if quality == "A": quality = "A+"
            elif quality == "B": quality = "A"

        return {
            "quality": quality,
            "score": min(10.0, score),
            "entry": c
        }
