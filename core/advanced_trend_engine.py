"""
Advanced Trend Engine v3.1 (ELITE)
Mean Reversion, Volume Profile ve Çoklu Zaman Dilimi Analizi.
"""
import pandas as pd
import numpy as np
import logging

logger = logging.getLogger(__name__)

class AdvancedTrendEngine:
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
            logger.error(f"Trend mum verisi alınamadı {symbol}: {e}")
            return pd.DataFrame()

    def _ema(self, series: pd.Series, period: int) -> pd.Series:
        return series.ewm(span=period, adjust=False).mean()

    def _calculate_rsi(self, series, period=14):
        delta = series.diff()
        gain = (delta.where(delta > 0, 0)).rolling(window=period).mean()
        loss = (-delta.where(delta < 0, 0)).rolling(window=period).mean()
        rs = gain / (loss + 1e-10)
        return 100 - (100 / (1 + rs))

    def analyze_mean_reversion(self, df: pd.DataFrame) -> dict:
        close = df["close"]
        sma20 = close.rolling(20).mean()
        std20 = close.rolling(20).std()
        z_score = (close - sma20) / (std20 + 1e-10)
        rsi = self._calculate_rsi(close)
        
        last_z = z_score.iloc[-1]
        last_rsi = rsi.iloc[-1]
        
        oversold = last_z < -2.5 and last_rsi < 30
        overbought = last_z > 2.5 and last_rsi > 70
        
        return {
            "z_score": round(last_z, 2),
            "rsi": round(last_rsi, 2),
            "is_mean_reversion_candidate": oversold or overbought,
            "reversion_direction": "LONG" if oversold else "SHORT" if overbought else "NONE"
        }

    def analyze(self, symbol: str) -> dict:
        df = self.get_candles(symbol, "1h", 100)
        if df.empty: return {"direction": "NO TRADE", "score": 0}
        
        close = df["close"]
        ema9 = self._ema(close, 9).iloc[-1]
        ema21 = self._ema(close, 21).iloc[-1]
        
        direction = "LONG" if ema9 > ema21 else "SHORT"
        score = 7.0
        
        # Mean Reversion Kontrolü
        mr = self.analyze_mean_reversion(df)
        if mr["is_mean_reversion_candidate"]:
            direction = mr["reversion_direction"]
            score = 8.0
            
        return {
            "symbol": symbol,
            "direction": direction,
            "score": score,
            "btc_trend": "NEUTRAL"
        }
