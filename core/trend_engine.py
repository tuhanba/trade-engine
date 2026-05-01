"""
Trend Engine — Profesyonel Sürüm
Market structure, EMA uyumu, ADX trend gücü, Bollinger Bands ve BTC trend onayı.
"""
import time
import logging
import pandas as pd
import numpy as np

logger = logging.getLogger(__name__)

class TrendEngine:
    def __init__(self, client):
        self.client = client
        self._btc_cache = {"trend": "NEUTRAL", "ts": 0}
        self._4h_cache = {}
        self._BTC_TTL = 300  # 5 dk
        self._4H_TTL = 240   # 4 dk

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

    def _adx(self, df: pd.DataFrame, period: int = 14) -> tuple:
        h, l, c = df["high"], df["low"], df["close"]
        plus_dm = h.diff().clip(lower=0)
        minus_dm = (-l.diff()).clip(lower=0)
        plus_dm[plus_dm < minus_dm] = 0
        minus_dm[minus_dm < plus_dm] = 0
        tr = pd.concat([h - l, (h - c.shift()).abs(), (l - c.shift()).abs()], axis=1).max(axis=1)
        atr14 = tr.rolling(period).mean()
        plus_di = 100 * (plus_dm.rolling(period).mean() / (atr14 + 1e-10))
        minus_di = 100 * (minus_dm.rolling(period).mean() / (atr14 + 1e-10))
        dx = 100 * ((plus_di - minus_di).abs() / (plus_di + minus_di + 1e-10))
        adx_val = dx.rolling(period).mean()
        return float(adx_val.iloc[-1]), float(plus_di.iloc[-1]), float(minus_di.iloc[-1])

    def _bollinger_width(self, df: pd.DataFrame, period: int = 20, std: float = 2.0) -> float:
        mid = df["close"].rolling(period).mean()
        std_dev = df["close"].rolling(period).std()
        width = ((mid + std * std_dev) - (mid - std * std_dev)) / (mid + 1e-10) * 100
        return round(float(width.iloc[-1]), 2)

    def _bb_width_change(self, df: pd.DataFrame, period: int = 20, std: float = 2.0, lookback: int = 5) -> float:
        mid = df["close"].rolling(period).mean()
        std_dev = df["close"].rolling(period).std()
        width = ((mid + std * std_dev) - (mid - std * std_dev)) / (mid + 1e-10) * 100
        cur = float(width.iloc[-1])
        past = float(width.iloc[-1 - lookback]) if len(width) > lookback else cur
        return round(cur - past, 3)

    def get_btc_trend(self) -> str:
        """BTC 1H + 4H trend. Cache: 5 dk."""
        now = time.time()
        if now - self._btc_cache["ts"] < self._BTC_TTL:
            return self._btc_cache["trend"]
        try:
            df1h = self.get_candles("BTCUSDT", "1h", 60)
            df4h = self.get_candles("BTCUSDT", "4h", 30)
            if df1h.empty or df4h.empty:
                return "NEUTRAL"

            def _trend(df):
                e21 = self._ema(df["close"], 21).iloc[-1]
                e55 = self._ema(df["close"], 55).iloc[-1]
                c = df["close"].iloc[-1]
                adx_v, pdi, mdi = self._adx(df)
                if e21 > e55 and c > e21 and adx_v > 18 and pdi > mdi:
                    return "BULLISH"
                if e21 < e55 and c < e21 and adx_v > 18 and mdi > pdi:
                    return "BEARISH"
                return "NEUTRAL"

            t1h = _trend(df1h)
            t4h = _trend(df4h)
            result = t1h if t1h == t4h and t1h != "NEUTRAL" else "NEUTRAL"
            self._btc_cache["trend"] = result
            self._btc_cache["ts"] = now
            return result
        except Exception as e:
            logger.debug(f"BTC trend hata: {e}")
            return "NEUTRAL"

    def get_4h_trend(self, symbol: str) -> str:
        """Sembol 4H trend. Cache: 4 dk."""
        now = time.time()
        if symbol in self._4h_cache:
            trend, ts = self._4h_cache[symbol]
            if now - ts < self._4H_TTL:
                return trend
        try:
            df4h = self.get_candles(symbol, "4h", 60)
            if df4h.empty or len(df4h) < 30:
                return "NEUTRAL"
            e21 = self._ema(df4h["close"], 21)
            e55 = self._ema(df4h["close"], 55)
            adx_v, pdi, mdi = self._adx(df4h)
            c = df4h["close"].iloc[-1]
            if e21.iloc[-1] > e55.iloc[-1] and c > e21.iloc[-1] and adx_v > 20 and pdi > mdi:
                trend = "BULLISH"
            elif e21.iloc[-1] < e55.iloc[-1] and c < e21.iloc[-1] and adx_v > 20 and mdi > pdi:
                trend = "BEARISH"
            else:
                trend = "NEUTRAL"
            self._4h_cache[symbol] = (trend, now)
            return trend
        except Exception as e:
            logger.debug(f"4H trend hata {symbol}: {e}")
            return "NEUTRAL"

    def analyze(self, symbol: str) -> dict:
        """Trend analizi yapar ve yön/skor döner."""
        df15 = self.get_candles(symbol, "15m", 100)
        if df15.empty or len(df15) < 50:
            return {"direction": "NO TRADE", "score": 0}

        close = df15["close"]
        e9 = self._ema(close, 9)
        e21 = self._ema(close, 21)
        e50 = self._ema(close, 50)
        
        adx_v, pdi, mdi = self._adx(df15)
        bb_w = self._bollinger_width(df15)
        bb_chg = self._bb_width_change(df15)
        
        c = close.iloc[-1]
        
        # Trend Onayı
        trend_up = (
            e9.iloc[-1] > e21.iloc[-1] > e50.iloc[-1]
            and c > e21.iloc[-1]
            and adx_v > 20
            and pdi > mdi
        )
        
        trend_dn = (
            e9.iloc[-1] < e21.iloc[-1] < e50.iloc[-1]
            and c < e21.iloc[-1]
            and adx_v > 20
            and mdi > pdi
        )

        if not trend_up and not trend_dn:
            return {"direction": "NO TRADE", "score": 0}

        direction = "LONG" if trend_up else "SHORT"
        
        # Skor Hesaplama
        score = 5.0
        
        # ADX Gücü
        if adx_v > 35: score += 3.0
        elif adx_v > 28: score += 2.0
        elif adx_v > 22: score += 1.0
        
        # Bollinger Genişliği (Volatilite)
        if 2.5 < bb_w < 5.0: score += 2.0
        elif 1.8 < bb_w: score += 1.0
        
        # Bollinger Kırılımı
        if bb_chg > 0.5: score += 2.0
        elif bb_chg > 0.2: score += 1.0

        # BTC ve 4H Trend Onayı
        btc_trend = self.get_btc_trend()
        trend_4h = self.get_4h_trend(symbol)
        
        if direction == "LONG" and btc_trend == "BULLISH": score += 1.0
        if direction == "SHORT" and btc_trend == "BEARISH": score += 1.0
        
        if direction == "LONG" and trend_4h == "BULLISH": score += 1.0
        if direction == "SHORT" and trend_4h == "BEARISH": score += 1.0

        return {
            "direction": direction,
            "score": min(10.0, score),
            "adx15": round(adx_v, 1),
            "bb_width": bb_w,
            "bb_width_chg": bb_chg,
            "btc_trend": btc_trend,
            "trend_4h": trend_4h
        }
