# src/signals/technical_indicators.py

import numpy as np
import pandas as pd
from utils.logger import setup_logger


class TechnicalIndicators:
    """
    Saf numpy/pandas ile temel teknik indikatörler.
    Harici TA kütüphanesine bağımlılık yok.
    """

    def __init__(self):
        self.logger = setup_logger(__name__)

    @staticmethod
    def moving_average(data: list, period: int) -> np.ndarray:
        """Basit Hareketli Ortalama (SMA)."""
        closes = np.array([c["close"] for c in data], dtype=float)
        if len(closes) < period:
            return np.array([np.nan] * len(closes))
        result = np.full(len(closes), np.nan)
        for i in range(period - 1, len(closes)):
            result[i] = closes[i - period + 1: i + 1].mean()
        return result

    @staticmethod
    def ema(data: list, period: int) -> np.ndarray:
        """Üstel Hareketli Ortalama (EMA)."""
        closes = np.array([c["close"] for c in data], dtype=float)
        result = np.full(len(closes), np.nan)
        if len(closes) < period:
            return result
        k = 2 / (period + 1)
        result[period - 1] = closes[:period].mean()
        for i in range(period, len(closes)):
            result[i] = closes[i] * k + result[i - 1] * (1 - k)
        return result

    @staticmethod
    def rsi(data: list, period: int = 14) -> np.ndarray:
        """Göreceli Güç Endeksi (RSI)."""
        closes = np.array([c["close"] for c in data], dtype=float)
        result = np.full(len(closes), np.nan)
        if len(closes) < period + 1:
            return result
        deltas = np.diff(closes)
        gains = np.where(deltas > 0, deltas, 0.0)
        losses = np.where(deltas < 0, -deltas, 0.0)
        avg_gain = gains[:period].mean()
        avg_loss = losses[:period].mean()
        for i in range(period, len(closes)):
            avg_gain = (avg_gain * (period - 1) + gains[i - 1]) / period
            avg_loss = (avg_loss * (period - 1) + losses[i - 1]) / period
            rs = avg_gain / avg_loss if avg_loss != 0 else np.inf
            result[i] = 100 - (100 / (1 + rs))
        return result

    @staticmethod
    def macd(data: list, fast: int = 12, slow: int = 26, signal: int = 9):
        """
        MACD hesaplar.
        Döner: (macd_line, signal_line, histogram) — hepsi np.ndarray
        """
        closes = [{"close": c["close"]} for c in data]
        ema_fast = TechnicalIndicators.ema(closes, fast)
        ema_slow = TechnicalIndicators.ema(closes, slow)
        macd_line = ema_fast - ema_slow

        # Signal line: macd_line üzerinden EMA
        macd_as_data = [{"close": v} for v in macd_line]
        signal_line = TechnicalIndicators.ema(macd_as_data, signal)
        histogram = macd_line - signal_line
        return macd_line, signal_line, histogram

    @staticmethod
    def bollinger_bands(data: list, period: int = 20, std_dev: float = 2.0):
        """
        Bollinger Bantları.
        Döner: (upper, middle, lower) — hepsi np.ndarray
        """
        closes = np.array([c["close"] for c in data], dtype=float)
        middle = np.full(len(closes), np.nan)
        upper = np.full(len(closes), np.nan)
        lower = np.full(len(closes), np.nan)
        for i in range(period - 1, len(closes)):
            window = closes[i - period + 1: i + 1]
            m = window.mean()
            s = window.std(ddof=0)
            middle[i] = m
            upper[i] = m + std_dev * s
            lower[i] = m - std_dev * s
        return upper, middle, lower
