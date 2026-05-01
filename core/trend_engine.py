"""
Trend Engine
Market structure, EMA uyumu ve momentum analizi yapar.
"""
import logging
import pandas as pd
import numpy as np

logger = logging.getLogger(__name__)

class TrendEngine:
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

    def _ema(self, series: pd.Series, period: int) -> pd.Series:
        return series.ewm(span=period, adjust=False).mean()

    def analyze(self, symbol: str) -> dict:
        """Trend analizi yapar ve yön/skor döner."""
        df15 = self.get_candles(symbol, "15m", 100)
        if df15.empty:
            return {"direction": "NO TRADE", "score": 0}

        close = df15["close"]
        ema20 = self._ema(close, 20)
        ema50 = self._ema(close, 50)
        ema200 = self._ema(close, 200)

        c = close.iloc[-1]
        e20 = ema20.iloc[-1]
        e50 = ema50.iloc[-1]
        e200 = ema200.iloc[-1]

        # Basit Market Structure & EMA Uyumu
        if e20 > e50 > e200 and c > e20:
            direction = "LONG"
            score = 8.0
            if c > close.iloc[-20:].max() * 0.99: # Yeni yüksek
                score += 2.0
        elif e20 < e50 < e200 and c < e20:
            direction = "SHORT"
            score = 8.0
            if c < close.iloc[-20:].min() * 1.01: # Yeni düşük
                score += 2.0
        else:
            direction = "NO TRADE"
            score = 0.0

        return {
            "direction": direction,
            "score": min(10.0, score)
        }
