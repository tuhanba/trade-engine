"""
core/cvd_engine.py — Cumulative Volume Delta (CVD) Engine v1.0
==============================================================
Binance Futures kline verisindeki taker buy/sell volumeyi kullanarak
CVD hesaplar ve scalp sinyali üretir.

CVD = Σ(taker_buy_volume - taker_sell_volume)
  taker_buy_volume  = tbbav  (taker buy base asset volume — kline col 9)
  taker_sell_volume = volume - tbbav

Sinyal Mantığı:
  CVD_BULLISH: fiyat düşerken CVD yükseliyor → hidden buying → LONG bias
  CVD_BEARISH: fiyat yükselirken CVD düşüyor → hidden selling → SHORT bias
  CVD_CONFIRM: fiyat + CVD aynı yönde → trend confirmed
  CVD_NEUTRAL: net sinyal yok

Win rate katkısı: ~%8-12 (order flow akademik çalışmalarından)
"""
import logging
import time
from typing import Optional
import pandas as pd

logger = logging.getLogger(__name__)

# Cache: sembol → {ts, cvd_signal, cvd_delta, cvd_slope}
_cvd_cache: dict = {}
_CACHE_TTL = 60  # 60 saniye

class CVDEngine:
    """
    Cumulative Volume Delta hesaplayıcı.
    TriggerEngine ile entegre kullanım için tasarlandı.
    """

    def __init__(self, client):
        self.client = client

    def get_candles_with_cvd(self, symbol: str, interval: str = "5m", limit: int = 100) -> pd.DataFrame:
        """
        Kline verisini çeker ve CVD sütununu ekler.
        tbbav = taker buy base asset volume (kline index 9)
        """
        try:
            klines = self.client.futures_klines(symbol=symbol, interval=interval, limit=limit)
            df = pd.DataFrame(klines, columns=[
                "time", "open", "high", "low", "close", "volume",
                "ct", "qav", "nt", "tbbav", "tbqav", "ignore"
            ])
            for col in ["open", "high", "low", "close", "volume", "tbbav", "tbqav"]:
                df[col] = df[col].astype(float)

            # Delta: taker_buy - taker_sell (per candle)
            df["taker_sell_vol"] = df["volume"] - df["tbbav"]
            df["delta"] = df["tbbav"] - df["taker_sell_vol"]  # positive = net buying

            # Cumulative Volume Delta
            df["cvd"] = df["delta"].cumsum()

            # Buy/Sell ratio (0-1, >0.5 = more buyers)
            df["buy_ratio"] = df["tbbav"] / (df["volume"] + 1e-10)

            return df
        except Exception as e:
            logger.error(f"CVD kline hatası {symbol}: {e}")
            return pd.DataFrame()

    def analyze(self, symbol: str, direction: str) -> dict:
        """
        CVD sinyalini hesaplar ve döner.

        Returns dict:
            cvd_signal: "BULLISH" | "BEARISH" | "CONFIRM_LONG" | "CONFIRM_SHORT" | "NEUTRAL"
            cvd_score_bonus: float (-2.0 to +2.0) — trigger_engine score'una eklenir
            cvd_divergence: bool — fiyat/CVD uyumsuzluğu var mı
            cvd_slope: float — CVD'nin son 5 mum eğimi
            buy_ratio: float — son mumdaki buy oranı
        """
        global _cvd_cache
        now = time.time()
        cached = _cvd_cache.get(symbol)
        if cached and (now - cached["ts"]) < _CACHE_TTL:
            return cached["result"]

        try:
            df = self.get_candles_with_cvd(symbol, "5m", 60)
            if df.empty or len(df) < 20:
                return _neutral_result()

            # Son mumlar
            last_cvd = df["cvd"].iloc[-1]
            cvd_5ago = df["cvd"].iloc[-6]
            # std ile normalize et: abs(cvd_5ago) sıfıra yakınsa patlama yapar
            # cvd_std ≈ 0 da olabilir (flat piyasa) → 1e-10 fallback yeterli
            cvd_std = float(df["cvd"].std())
            cvd_slope = (last_cvd - cvd_5ago) / (cvd_std + 1e-10)  # z-score benzeri slope

            # Fiyat hareketi (son 10 mum)
            price_change = (df["close"].iloc[-1] - df["close"].iloc[-11]) / df["close"].iloc[-11]

            # Son mum buy ratio
            last_buy_ratio = float(df["buy_ratio"].iloc[-1])

            # 5 mum ortalama buy ratio
            avg_buy_ratio = float(df["buy_ratio"].tail(5).mean())

            # CVD Divergence tespiti
            # Fiyat yükseliyor ama CVD düşüyor = BEARISH divergence
            # Fiyat düşüyor ama CVD yükseliyor = BULLISH divergence
            price_up = price_change > 0.002   # %0.2+ yukarı
            price_dn = price_change < -0.002  # %0.2+ aşağı
            cvd_up   = cvd_slope > 0.05
            cvd_dn   = cvd_slope < -0.05

            bullish_div = price_dn and cvd_up   # Hidden buying
            bearish_div = price_up and cvd_dn   # Hidden selling
            divergence  = bullish_div or bearish_div

            # Sinyal Belirleme
            if bullish_div:
                signal = "BULLISH"
                if direction == "LONG":
                    bonus = 1.5   # Trend'le aynı → güçlü bonus
                else:
                    bonus = -1.0  # Karşı trend → ceza
            elif bearish_div:
                signal = "BEARISH"
                if direction == "SHORT":
                    bonus = 1.5
                else:
                    bonus = -1.0
            elif cvd_up and price_up and direction == "LONG":
                signal = "CONFIRM_LONG"
                bonus = 1.0
            elif cvd_dn and price_dn and direction == "SHORT":
                signal = "CONFIRM_SHORT"
                bonus = 1.0
            elif not cvd_up and not cvd_dn:
                signal = "NEUTRAL"
                bonus = 0.0
            else:
                signal = "NEUTRAL"
                bonus = 0.0

            # Buy pressure bonus (>0.6 = 60% taker buy = güçlü)
            if avg_buy_ratio > 0.60 and direction == "LONG":
                bonus += 0.5
            elif avg_buy_ratio < 0.40 and direction == "SHORT":
                bonus += 0.5
            elif avg_buy_ratio > 0.60 and direction == "SHORT":
                bonus -= 0.5
            elif avg_buy_ratio < 0.40 and direction == "LONG":
                bonus -= 0.5

            result = {
                "cvd_signal":    signal,
                "cvd_score_bonus": round(max(-2.0, min(2.0, bonus)), 2),
                "cvd_divergence": divergence,
                "cvd_slope":     round(cvd_slope, 4),
                "buy_ratio":     round(last_buy_ratio, 3),
                "avg_buy_ratio": round(avg_buy_ratio, 3),
                "cvd_value":     round(last_cvd, 4),
            }

            _cvd_cache[symbol] = {"ts": now, "result": result}
            logger.debug(f"[CVD] {symbol} {direction}: {signal} bonus={bonus:+.1f} buy_ratio={last_buy_ratio:.2f}")
            return result

        except Exception as e:
            logger.error(f"[CVD] Analiz hatası {symbol}: {e}")
            return _neutral_result()


def _neutral_result() -> dict:
    return {
        "cvd_signal":    "NEUTRAL",
        "cvd_score_bonus": 0.0,
        "cvd_divergence": False,
        "cvd_slope":     0.0,
        "buy_ratio":     0.5,
        "avg_buy_ratio": 0.5,
        "cvd_value":     0.0,
    }
