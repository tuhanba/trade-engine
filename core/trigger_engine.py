"""
Trigger Engine — Profesyonel Sürüm
Giriş onayı, setup kalitesi, çoklu timeframe (5m + 1m), RSI, VWAP, MACD ve momentum.
"""
import logging
import pandas as pd
import numpy as np
from datetime import datetime, timezone

logger = logging.getLogger(__name__)

# Config'den filtre parametrelerini al
try:
    import sys, os
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    from config import (
        ALLOWED_QUALITIES, BAD_HOURS_UTC, GOOD_HOURS_UTC,
        SHORT_REQUIRES_BTC_BEARISH, BTC_TREND_INTERVAL,
        ADX_MIN_THRESHOLD, MIN_ADX_5M, FUNDING_LONG_MAX, FUNDING_SHORT_MIN,
        SESSION_FILTER_ENABLED, SESSION_SCORE_BONUS, SESSION_SCORE_PENALTY,
    )
except ImportError:
    ALLOWED_QUALITIES           = ["S", "A+", "A", "B"]
    BAD_HOURS_UTC               = list(range(0, 6))
    GOOD_HOURS_UTC              = list(range(8, 18))
    SHORT_REQUIRES_BTC_BEARISH  = True
    BTC_TREND_INTERVAL          = "4h"
    ADX_MIN_THRESHOLD           = 18
    MIN_ADX_5M                  = 13
    FUNDING_LONG_MAX            = 0.003
    FUNDING_SHORT_MIN           = -0.003
    SESSION_FILTER_ENABLED      = True
    SESSION_SCORE_BONUS         = 10.0
    SESSION_SCORE_PENALTY       = -15.0


def _btc_allows(direction: str, btc_trend: str) -> tuple:
    """(allowed: bool, leverage_multiplier: float)"""
    if btc_trend == "NEUTRAL":
        return True, 0.8    # İzin ver ama leverage kısıtlı
    if direction == "LONG" and btc_trend == "BULLISH":
        return True, 1.0    # En iyi senaryo
    if direction == "SHORT" and btc_trend == "BEARISH":
        return True, 1.0
    if direction == "LONG" and btc_trend == "BEARISH":
        return False, 0.0   # Kontrend — engelle
    if direction == "SHORT" and btc_trend == "BULLISH":
        return False, 0.0   # Kontrend — engelle
    return True, 0.8

class TriggerEngine:
    def __init__(self, client):
        self.client = client
        # Per-instance trackers — instance'ta tutulmazsa _prev_oi her çağrıda sıfırlanır
        self._oi_tracker = None   # OITracker: _prev_oi state'i burada yaşar
        self._cvd_engine = None   # CVDEngine: modül cache'i var ama yine de tek instance yeter
        self._macro_filter = None

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
        rsi_series = (100 - (100 / (1 + rs))).dropna()
        if rsi_series.empty:
            return 50.0
        return float(rsi_series.iloc[-1])

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



    def analyze(self, symbol: str, direction: str, btc_trend: str = "NEUTRAL",
                trend_confluence: int = 1) -> dict:
        if direction == "NO TRADE":
            return {"quality": "D", "score": 0, "entry": 0}

        current_hour = datetime.now(timezone.utc).hour
        if current_hour in BAD_HOURS_UTC:
            return {"quality": "D", "score": 0, "entry": 0}

        # BTC NEUTRAL → geçir (hem LONG hem SHORT açılabilir)
        # Sadece kesin karşı trend'de engelle
        if btc_trend == "BEARISH" and direction == "LONG":
            return {"quality": "D", "score": 0, "entry": 0, "reject_reason": "btc_bearish_no_long"}
        if btc_trend == "BULLISH" and direction == "SHORT":
            return {"quality": "D", "score": 0, "entry": 0, "reject_reason": "btc_bullish_no_short"}
        _, btc_lev_mult = _btc_allows(direction, btc_trend)

        df5 = self.get_candles(symbol, "5m", 150)
        if df5.empty or len(df5) < 50:
            return {"quality": "D", "score": 0, "entry": 0, "adx": 0.0}

        c5 = df5["close"].iloc[-1]
        e9_5 = self._ema(df5["close"], 9)
        e21_5 = self._ema(df5["close"], 21)
        e50_5 = self._ema(df5["close"], 50)
        rsi5 = self._rsi(df5["close"], 14)

        try:
            high = df5["high"]
            low  = df5["low"]
            close = df5["close"]
            tr = pd.concat([high - low, (high - close.shift()).abs(), (low  - close.shift()).abs()], axis=1).max(axis=1)
            atr14 = tr.rolling(14).mean()
            atr_val = float(atr14.iloc[-1])
            plus_dm  = (high.diff()).where((high.diff() > 0) & (high.diff() > -low.diff()), 0)
            minus_dm = (-low.diff()).where((-low.diff() > 0) & (-low.diff() > high.diff()), 0)
            plus_di  = 100 * (plus_dm.rolling(14).mean()  / (atr14 + 1e-10))
            minus_di = 100 * (minus_dm.rolling(14).mean() / (atr14 + 1e-10))
            dx = 100 * (plus_di - minus_di).abs() / (plus_di + minus_di + 1e-10)
            adx_val  = float(dx.rolling(14).mean().iloc[-1])
        except Exception:
            adx_val = 20
            atr_val = c5 * 0.02 # Fallback ATR %2

        if adx_val < MIN_ADX_5M:
            return {"quality": "D", "score": 0, "entry": 0, "adx": round(adx_val, 1), "reject_reason": f"adx_too_low_{adx_val:.1f}"}

        # AI beynine daha fazla veri sağlamak için RSI şartlarını esnettik
        rsi5_val = float(rsi5)
        bull5 = direction == "LONG" and e9_5.iloc[-1] > e21_5.iloc[-1] and 30 < rsi5_val < 80
        bear5 = direction == "SHORT" and e9_5.iloc[-1] < e21_5.iloc[-1] and 20 < rsi5_val < 70

        df1 = self.get_candles(symbol, "1m", 100)
        if df1.empty or len(df1) < 30:
            return {"quality": "D", "score": 0, "entry": 0, "adx": round(adx_val, 1)}

        c1 = df1["close"].iloc[-1]
        rsi1 = self._rsi(df1["close"], 7)
        hist = self._macd_hist(df1["close"])
        rv = self._relative_volume(df1, 20)
        mom3c = self._momentum_3c(df1)
        vwap_val = self._vwap(df1)

        import config
        is_human = getattr(config, "HUMAN_MODE_ENABLED", True)

        is_micro_scalp = False
        if not is_human:
            # SCALP MODU: 1 dakikalık aşırı hızlı kırılımları yakala
            if direction == "LONG" and rv >= 2.5 and mom3c >= 2.0:
                is_micro_scalp = True
            elif direction == "SHORT" and rv >= 2.5 and mom3c <= -2.0:
                is_micro_scalp = True

        if not bull5 and not bear5 and not is_micro_scalp:
            return {"quality": "D", "score": 0, "entry": 0, "adx": round(adx_val, 1), "rsi5": round(float(rsi5_val), 1)}

        if not is_micro_scalp:
            if bull5 and not (25 < rsi1 < 85): return {"quality": "D", "score": 0, "entry": 0}
            if bear5 and not (15 < rsi1 < 75): return {"quality": "D", "score": 0, "entry": 0}

        # Makro Filtre (24h Funding Trend)
        try:
            from core.macro_filter import MacroFilter as _MacroFilter
            if self._macro_filter is None:
                self._macro_filter = _MacroFilter(self.client)
            
            macro_data = self._macro_filter.get_24h_funding_trend(symbol)
            funding = macro_data.get("avg_rate", 0.0)
            bias = macro_data.get("bias", "NEUTRAL")
            
            # Squeeze Kalkanı:
            if direction == "LONG" and bias == "EXTREME_GREED":
                return {"quality": "D", "score": 0, "entry": 0, "reject_reason": f"macro_extreme_greed_squeeze_danger_{funding:.5f}"}
            if direction == "SHORT" and bias == "EXTREME_FEAR":
                return {"quality": "D", "score": 0, "entry": 0, "reject_reason": f"macro_extreme_fear_squeeze_danger_{funding:.5f}"}
            
            # Eski anlık limitlere göre hard-block (config'den)
            if direction == "LONG" and funding > FUNDING_LONG_MAX:
                return {"quality": "D", "score": 0, "entry": 0, "reject_reason": f"funding_too_high_{funding:.5f}"}
            if direction == "SHORT" and funding < FUNDING_SHORT_MIN:
                return {"quality": "D", "score": 0, "entry": 0, "reject_reason": f"funding_too_low_{funding:.5f}"}
                
        except Exception as e:
            logger.debug(f"[Macro] Error: {e}")
            funding = 0.0

        score = 5.0
        quality = "C"

        if rv > 1.2:
            score += 2.0
            quality = "B"
        elif rv > 1.0:
            score += 1.0

        if direction == "LONG" and mom3c > 1.5:
            score += 2.0
            if quality == "B": quality = "A"
        elif direction == "SHORT" and mom3c < -1.5:
            score += 2.0
            if quality == "B": quality = "A"

        if (direction == "LONG" and hist > 0) or (direction == "SHORT" and hist < 0): score += 1.0
        if (direction == "LONG" and c1 > vwap_val) or (direction == "SHORT" and c1 < vwap_val): score += 1.0

        # S Sınıfı Kontrolü
        s_score = 0
        if adx_val >= 35: s_score += 2
        if abs(rsi5 - 55) <= 15: s_score += 2
        if rv >= 2.0: s_score += 2
        if abs(mom3c) >= 1.8: s_score += 2

        if s_score >= 6: quality = "S"
        elif quality == "A" and score >= 7.5: quality = "A+"

        # Mikro-Scalp Override
        if is_micro_scalp:
            quality = "M"
            score = 8.0

        if quality not in ALLOWED_QUALITIES + ["M"]:
            return {"quality": "D", "score": 0, "entry": 0}

        # ── Volatilite (ATR) Kalkanı ─────────────────────────────────────────
        atr_pct = atr_val / c1
        if atr_pct > 0.025:
            return {"quality": "D", "score": 0, "entry": 0, "reject_reason": f"extreme_volatility_{atr_pct*100:.1f}%"}
        elif atr_pct > 0.015:
            score = max(score - 1.5, 0.0)
            if quality in ["S", "A+"]:
                quality = "A"
        # ─────────────────────────────────────────────────────────────────────

        # ── L2 Orderbook (Balina Duvarı) Kalkanı ─────────────────────────────
        try:
            ob = self.client.futures_order_book(symbol=symbol, limit=20)
            bids = ob.get("bids", [])
            asks = ob.get("asks", [])
            
            bid_depth = sum(float(b[0]) * float(b[1]) for b in bids)
            ask_depth = sum(float(a[0]) * float(a[1]) for a in asks)
            
            if direction == "LONG" and ask_depth > bid_depth * 4.0:
                return {"quality": "D", "score": 0, "entry": 0, "reject_reason": "massive_ask_wall_detected"}
            if direction == "SHORT" and bid_depth > ask_depth * 4.0:
                return {"quality": "D", "score": 0, "entry": 0, "reject_reason": "massive_bid_wall_detected"}
        except Exception as e:
            logger.debug(f"[Orderbook] skip: {e}")
            bid_depth, ask_depth = 1.0, 1.0
        # ─────────────────────────────────────────────────────────────────────

        # ── Session skoru ayarlaması ─────────────────────────────────────────
        if SESSION_FILTER_ENABLED:
            if current_hour in GOOD_HOURS_UTC:
                score = min(score + SESSION_SCORE_BONUS / 10, 10.0)
            elif current_hour in BAD_HOURS_UTC:
                score = max(score + SESSION_SCORE_PENALTY / 10, 0.0)
        # ─────────────────────────────────────────────────────────────────────

        # ── Çoklu TF Confluence kalite ayarlaması ────────────────────────────
        # trend_confluence: 15m+1h+4h = 1-3  (trend_engine'den gelir)
        # 5m bu fonksiyonda onaylandı (bull5/bear5) → +1 → toplam 2-4
        confluence_total = trend_confluence + 1  # +1 for 5m
        confluence_score = round(confluence_total / 4.0, 2)

        quality_order = ["B", "A", "A+", "S", "M"]
        if confluence_total >= 3 and quality in quality_order and quality != "M":
            idx = quality_order.index(quality)
            if idx < len(quality_order) - 2: # Don't upgrade to M through confluence
                quality = quality_order[idx + 1]
                score   = min(score + 1.0, 10.0)
        elif confluence_total <= 1 and quality in quality_order and quality != "M":
            idx = quality_order.index(quality)
            if idx > 0:
                quality = quality_order[idx - 1]
                score   = max(score - 1.0, 0.0)

        if quality not in ALLOWED_QUALITIES + ["M"]:
            return {"quality": "D", "score": 0, "entry": 0}

        # ── CVD Analizi (Cumulative Volume Delta) ─────────────────────────────
        cvd_bonus = 0.0
        cvd_data  = {}
        try:
            from core.cvd_engine import CVDEngine as _CVDEngine
            if self._cvd_engine is None:
                self._cvd_engine = _CVDEngine(self.client)
            cvd_data = self._cvd_engine.analyze(symbol, direction)
            cvd_bonus = cvd_data.get("cvd_score_bonus", 0.0)
            score = min(10.0, max(0.0, score + cvd_bonus))
            logger.debug(
                f"[CVD] {symbol}: {cvd_data.get('cvd_signal')} "
                f"bonus={cvd_bonus:+.1f} buy_ratio={cvd_data.get('buy_ratio', 0.5):.2f}"
            )
        except Exception as _cvd_err:
            logger.debug(f"[CVD] skip: {_cvd_err}")
        # ──────────────────────────────────────────────────────────────────────

        # ── OI Analizi (Open Interest Delta) ──────────────────────────────────
        oi_bonus = 0.0
        oi_data  = {}
        try:
            from core.oi_tracker import OITracker as _OITracker
            if self._oi_tracker is None:
                self._oi_tracker = _OITracker(self.client)
            oi_data = self._oi_tracker.analyze(symbol, c1, direction)
            oi_bonus = oi_data.get("oi_score_bonus", 0.0)
            score = min(10.0, max(0.0, score + oi_bonus))
            logger.debug(
                f"[OI] {symbol}: {oi_data.get('oi_signal')} "
                f"bonus={oi_bonus:+.1f} oi_chg={oi_data.get('oi_change_pct', 0):+.1f}%"
            )
        except Exception as _oi_err:
            logger.debug(f"[OI] skip: {_oi_err}")
        # ──────────────────────────────────────────────────────────────────────

        # ── ML Puanlaması (Yapay Zeka Beyni) ──────────────────────────────────
        try:
            from core.ml_signal_scorer import score_signal
            ml_signal = {
                "symbol": symbol,
                "adx15": adx_val,
                "rv": rv,
                "rsi5": rsi5,
                "rsi1": rsi1,
                "funding_favorable": 1 if (direction == "LONG" and funding < 0) or (direction == "SHORT" and funding > 0) else 0,
                "btc_trend": btc_trend,
                "direction": direction,
                "momentum_3c": mom3c,
                "ob_ratio": bid_depth / (ask_depth + 1e-10) if 'bid_depth' in locals() else 1.0,
            }
            ml_score = score_signal(ml_signal)
            
            if ml_score < 35:
                return {"quality": "D", "score": 0, "entry": 0, "reject_reason": f"ml_score_too_low_{ml_score}"}
                
            # ── Gerçek "S" Seviye Kuralı ─────────────────────────────────────
            # ★ Gerçek "S" Seviye Kuralı ★ (Cold start istisnası ile)
            if quality in ["S", "A+"] and ml_score < 70 and ml_score != 50:
                quality = "A" 
                score = max(score - 1.0, 0.0)
                
            elif quality in ["A", "B"] and ml_score >= 75:
                quality = "A+"
                score = min(score + 1.5, 10.0)
                
            # M Kalite (Micro-Scalp) için tolerans
            if quality == "M" and ml_score < 45:
                return {"quality": "D", "score": 0, "entry": 0, "reject_reason": f"ml_score_too_low_for_micro_{ml_score}"}
            # ──────────────────────────────────────────────────────────────────
        except Exception as e:
            logger.debug(f"[ML Scorer] skip: {e}")
            ml_score = 50
        # ──────────────────────────────────────────────────────────────────────

        return {
            "quality": quality,
            "score": min(10.0, max(0.0, score)),
            "ml_score": ml_score,
            "confluence_score": confluence_score,
            "confluence_total": confluence_total,
            "entry": c1,
            "atr": round(atr_val, 6),
            "atr_pct": round(atr_val / c1, 4),
            "rsi5": round(rsi5, 1),
            "rsi1": round(rsi1, 1),
            "rv": rv,
            "momentum_3c": mom3c,
            "macd_hist": round(hist, 6),
            "funding": round(funding * 100, 4),
            "btc_trend": btc_trend,
            "btc_leverage_mult": btc_lev_mult if SHORT_REQUIRES_BTC_BEARISH else 1.0,
            "hour_utc": current_hour,
            "good_hour": current_hour in GOOD_HOURS_UTC,
            "adx": round(adx_val, 1),
            "cvd_signal":    cvd_data.get("cvd_signal", "NEUTRAL"),
            "cvd_bonus":     round(cvd_bonus, 2),
            "cvd_buy_ratio": cvd_data.get("buy_ratio", 0.5),
            "cvd_divergence": cvd_data.get("cvd_divergence", False),
            "oi_signal":     oi_data.get("oi_signal", "NEUTRAL"),
            "oi_bonus":      round(oi_bonus, 2),
            "oi_change_pct": oi_data.get("oi_change_pct", 0.0),
        }
