"""
Trigger Engine — Profesyonel Sürüm
Giriş onayı, setup kalitesi, çoklu timeframe (5m + 1m), RSI, VWAP, MACD ve momentum.
"""
import logging
import pandas as pd
import numpy as np

logger = logging.getLogger(__name__)

# Config'den filtre parametrelerini al
try:
    import sys, os
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    from config import (
        ALLOWED_QUALITIES, BAD_HOURS_UTC, GOOD_HOURS_UTC,
        SHORT_REQUIRES_BTC_BEARISH, BTC_TREND_INTERVAL,
        ADX_MIN_THRESHOLD
    )
except ImportError:
    ALLOWED_QUALITIES        = ["S", "A+"]
    BAD_HOURS_UTC            = [1,4,5,6,10,11,12,13,14,16,19,20,21,22]
    GOOD_HOURS_UTC           = [0,3,7,9,17,23]
    SHORT_REQUIRES_BTC_BEARISH = True
    BTC_TREND_INTERVAL       = "4h"
    ADX_MIN_THRESHOLD        = 28

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

    def _ema(self, series: pd.Series, period: int) -> pd.Series:
        return series.ewm(span=period, adjust=False).mean()

    def _rsi(self, series: pd.Series, period: int = 14) -> float:
        delta = series.diff()
        gain = (delta.where(delta > 0, 0)).rolling(window=period).mean()
        loss = (-delta.where(delta < 0, 0)).rolling(window=period).mean()
        rs = gain / (loss + 1e-10)
        return float(100 - (100 / (1 + rs)).iloc[-1])

    def _macd_hist(self, series: pd.Series) -> float:
        fast = self._ema(series, 12).iloc[-1] - self._ema(series, 26).iloc[-1]
        slow = self._ema(pd.Series((self._ema(series, 12) - self._ema(series, 26)).values), 9).iloc[-1]
        return fast - slow

    def _vwap(self, df: pd.DataFrame) -> float:
        tp = (df["high"] + df["low"] + df["close"]) / 3
        return float((tp * df["volume"]).sum() / (df["volume"].sum() + 1e-10))

    def _relative_volume(self, df: pd.DataFrame, period: int = 20) -> float:
        avg = df["volume"].iloc[-period - 1:-1].mean()
        cur = df["volume"].iloc[-1]
        return round(cur / (avg + 1e-10), 2)

    def _momentum_3c(self, df: pd.DataFrame) -> float:
        """Son 3 mumun yönlü momentum skoru (-3 ile +3)."""
        if len(df) < 4:
            return 0
        score = 0.0
        for i in range(-3, 0):
            o = df["open"].iloc[i]
            c = df["close"].iloc[i]
            body = abs(c - o)
            rng = df["high"].iloc[i] - df["low"].iloc[i]
            strength = body / (rng + 1e-10)
            score += strength if c > o else -strength
        return round(score, 3)

    def get_funding_rate(self, symbol: str) -> float:
        try:
            result = self.client.futures_funding_rate(symbol=symbol, limit=1)
            return float(result[-1]["fundingRate"]) if result else 0.0
        except Exception:
            return 0.0

    def analyze(self, symbol: str, direction: str, btc_trend: str = "NEUTRAL") -> dict:
        """Trigger analizi yapar ve setup kalitesi döner."""
        if direction == "NO TRADE":
            return {"quality": "D", "score": 0, "entry": 0}

        # ── Saat Filtresi (UTC) ────────────────────────────────────────────────
        from datetime import datetime, timezone
        current_hour = datetime.now(timezone.utc).hour
        if current_hour in BAD_HOURS_UTC:
            logger.debug(f"[{symbol}] Kötü saat filtresi: {current_hour}:00 UTC — trade yok")
            return {"quality": "D", "score": 0, "entry": 0}

        # ── BTC Trend Filtresi ─────────────────────────────────────────────────
        if SHORT_REQUIRES_BTC_BEARISH:
            if direction == "SHORT" and btc_trend == "BULLISH":
                logger.debug(f"[{symbol}] SHORT engellendi: BTC 4H BULLISH")
                return {"quality": "D", "score": 0, "entry": 0}
            if direction == "LONG" and btc_trend == "BEARISH":
                logger.debug(f"[{symbol}] LONG engellendi: BTC 4H BEARISH")
                return {"quality": "D", "score": 0, "entry": 0}

        # ── 5m Analizi ────────────────────────────────────────────────────────
        df5 = self.get_candles(symbol, "5m", 150)
        if df5.empty or len(df5) < 50:
            return {"quality": "D", "score": 0, "entry": 0}

        c5 = df5["close"].iloc[-1]
        e9_5 = self._ema(df5["close"], 9)
        e21_5 = self._ema(df5["close"], 21)
        e50_5 = self._ema(df5["close"], 50)
        rsi5 = self._rsi(df5["close"], 14)

        # ── ADX Trend Gücü Filtresi ────────────────────────────────────────────
        # Backtest: ADX < 25 olan sinyallerde WR düşük — trend gücü zayıf
        try:
            high = df5["high"]
            low  = df5["low"]
            close = df5["close"]
            tr = pd.concat([
                high - low,
                (high - close.shift()).abs(),
                (low  - close.shift()).abs()
            ], axis=1).max(axis=1)
            atr14 = tr.rolling(14).mean()
            plus_dm  = (high.diff()).where((high.diff() > 0) & (high.diff() > -low.diff()), 0)
            minus_dm = (-low.diff()).where((-low.diff() > 0) & (-low.diff() > high.diff()), 0)
            plus_di  = 100 * (plus_dm.rolling(14).mean()  / (atr14 + 1e-10))
            minus_di = 100 * (minus_dm.rolling(14).mean() / (atr14 + 1e-10))
            dx = 100 * (plus_di - minus_di).abs() / (plus_di + minus_di + 1e-10)
            adx_val  = float(dx.rolling(14).mean().iloc[-1])
        except Exception:
            adx_val = 30  # Hesaplanamadıysa geç

        if adx_val < ADX_MIN_THRESHOLD:
            logger.debug(f"[{symbol}] ADX filtresi: {adx_val:.1f} < {ADX_MIN_THRESHOLD} — trend zayıf")
            return {"quality": "D", "score": 0, "entry": 0, "adx": round(adx_val, 1)}

        bull5 = direction == "LONG" and e9_5.iloc[-1] > e21_5.iloc[-1] > e50_5.iloc[-1] and 35 < rsi5 < 75
        bear5 = direction == "SHORT" and e9_5.iloc[-1] < e21_5.iloc[-1] < e50_5.iloc[-1] and 25 < rsi5 < 65

        if not bull5 and not bear5:
            return {"quality": "D", "score": 0, "entry": 0}

        # ── 1m Analizi ────────────────────────────────────────────────────────
        df1 = self.get_candles(symbol, "1m", 100)
        if df1.empty or len(df1) < 30:
            return {"quality": "D", "score": 0, "entry": 0}

        c1 = df1["close"].iloc[-1]
        rsi1 = self._rsi(df1["close"], 7)
        hist = self._macd_hist(df1["close"])
        rv = self._relative_volume(df1, 20)
        mom3c = self._momentum_3c(df1)
        vwap_val = self._vwap(df1)

        # RSI 1m giriş onayı
        if bull5 and not (32 < rsi1 < 75):
            return {"quality": "D", "score": 0, "entry": 0}
        if bear5 and not (25 < rsi1 < 68):
            return {"quality": "D", "score": 0, "entry": 0}

        # ── Funding Rate Kontrolü ─────────────────────────────────────────────
        funding = self.get_funding_rate(symbol)
        if direction == "LONG" and funding > 0.001:
            return {"quality": "D", "score": 0, "entry": 0}  # Longs ağır, olumsuz funding
        if direction == "SHORT" and funding < -0.001:
            return {"quality": "D", "score": 0, "entry": 0}

        # ── Skor ve Kalite Hesaplama ──────────────────────────────────────────
        score = 5.0
        quality = "C"

        # Relative Volume
        if rv > 2.0:
            score += 2.0
            quality = "B"
        elif rv > 1.5:
            score += 1.0

        # Momentum Uyumu
        if direction == "LONG" and mom3c > 1.5:
            score += 2.0
            if quality == "B": quality = "A"
        elif direction == "SHORT" and mom3c < -1.5:
            score += 2.0
            if quality == "B": quality = "A"
        elif direction == "LONG" and mom3c > 0.5:
            score += 1.0
        elif direction == "SHORT" and mom3c < -0.5:
            score += 1.0

        # MACD Histogram
        if direction == "LONG" and hist > 0: score += 1.0
        if direction == "SHORT" and hist < 0: score += 1.0

        # VWAP Uyumu
        if direction == "LONG" and c1 > vwap_val: score += 1.0
        if direction == "SHORT" and c1 < vwap_val: score += 1.0

        # RSI Merkeze Yakınlık (Aşırılardan uzak = iyi)
        rsi_mid_dist = abs(rsi5 - 50)
        if rsi_mid_dist > 30: score -= 2.0  # Aşırı alım/satım bölgesi

        # ── Composite S Sınıfı Skoru ─────────────────────────────────────────
        # S sınıfı: ADX, RSI, EMA, hacim, BTC korelasyonu ve coin geçmiş WR
        # birleştiren çok boyutlu skor — en güvenilir setup
        s_score = 0
        # ADX gücü (0-3 puan)
        if adx_val >= 40:    s_score += 3
        elif adx_val >= 35:  s_score += 2
        elif adx_val >= 30:  s_score += 1
        # RSI merkeze yakınlık (0-2 puan) — 48-62 ideal bölge
        rsi_dist = abs(rsi5 - 55)
        if rsi_dist <= 7:    s_score += 2
        elif rsi_dist <= 15: s_score += 1
        # Hacim spike (0-2 puan)
        if rv >= 2.5:        s_score += 2
        elif rv >= 2.0:      s_score += 1
        # MACD histogram yönü (0-1 puan)
        if (direction == "LONG" and hist > 0) or (direction == "SHORT" and hist < 0):
            s_score += 1
        # Momentum güçlü (0-2 puan)
        mom_abs = abs(mom3c)
        if mom_abs >= 2.5:   s_score += 2
        elif mom_abs >= 1.8: s_score += 1
        # Coin geçmiş win rate (0-2 puan)
        try:
            from coin_library import get_coin_params
            cp = get_coin_params(symbol)
            hist_wr = cp.get("win_rate", 0)
            if hist_wr >= 0.60:   s_score += 2
            elif hist_wr >= 0.50: s_score += 1
        except Exception:
            pass
        # BTC trend uyumu (0-1 puan)
        if (direction == "LONG" and btc_trend == "BULLISH") or (direction == "SHORT" and btc_trend == "BEARISH"):
            s_score += 1
        # İyi saat bonusu (0-1 puan)
        if current_hour in GOOD_HOURS_UTC:
            s_score += 1
        # S sınıfı eşiği: 14 üzerinden en az 8 puan (yeni DB'de coin WR=0 → 2 puan eksik)
        if s_score >= 8:
            quality = "S"
        # A+ Kalite Yükseltmesi
        elif quality == "A" and score >= 7.5:
            quality = "A+"

        # ── Kalite Filtresi ────────────────────────────────────────────────────
        if quality not in ALLOWED_QUALITIES:
            logger.debug(f"[{symbol}] Kalite filtresi: {quality} izin verilenler={ALLOWED_QUALITIES}")
            return {"quality": "D", "score": 0, "entry": 0}

        # ── İyi Saat Bonusu ────────────────────────────────────────────────────
        if current_hour in GOOD_HOURS_UTC:
            score = min(10.0, score + 0.5)

        # ML Sinyal Skoru Hesaplama
        ml_score = 50
        try:
            import sys
            import os
            sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
            from ml_signal_scorer import get_scorer
            scorer = get_scorer()
            signal_data = {
                "symbol": symbol,
                "direction": direction,
                "rsi5": rsi5,
                "rsi1": rsi1,
                "rv": rv,
                "momentum_3c": mom3c,
                "funding_favorable": 1 if (direction == "LONG" and funding < 0) or (direction == "SHORT" and funding > 0) else 0,
                "btc_trend": "NEUTRAL", # Trend engine'den gelmeli ama şimdilik nötr
                "session": "OFF", # AI decision engine'den gelmeli
            }
            ml_score = scorer.predict(signal_data)
            
            # ML skoru düşükse kaliteyi düşür
            if ml_score < 35 and quality in ["A+", "A", "B"]:
                quality = "C"
            elif ml_score > 70 and quality == "B":
                quality = "A"
        except Exception as e:
            logger.warning(f"ML Skor hesaplama hatası: {e}")

        return {
            "quality": quality,
            "score": min(10.0, max(0.0, score)),
            "ml_score": ml_score,
            "entry": c1,
            "rsi5": round(rsi5, 1),
            "rsi1": round(rsi1, 1),
            "rv": rv,
            "momentum_3c": mom3c,
            "macd_hist": round(hist, 6),
            "funding": round(funding * 100, 4),
            "btc_trend": btc_trend,
            "hour_utc": current_hour,
            "good_hour": current_hour in GOOD_HOURS_UTC,
        }
