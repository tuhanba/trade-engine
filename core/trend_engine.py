"""
Trend Engine — Profesyonel Sürüm
Market structure, EMA uyumu, ADX trend gücü, Bollinger Bands ve BTC trend onayı.
"""
import time
import logging
import pandas as pd
import numpy as np

logger = logging.getLogger(__name__)

_GLOBAL_KLINE_CACHE = {}
_GLOBAL_KLINE_TTL = {"1m": 15, "5m": 30, "15m": 60, "1h": 300, "4h": 300}

class TrendEngine:
    def __init__(self, client):
        self.client = client
        self._btc_cache = {"trend": "NEUTRAL", "ts": 0}
        self._4h_cache = {}
        self._BTC_TTL = 300  # 5 dk
        self._4H_TTL = 240   # 4 dk

    def get_candles(self, symbol: str, interval: str, limit: int) -> pd.DataFrame:
        cache_key = f"{symbol}_{interval}_{limit}"
        now = time.time()
        
        # Check cache
        if cache_key in _GLOBAL_KLINE_CACHE:
            df, ts = _GLOBAL_KLINE_CACHE[cache_key]
            ttl = _GLOBAL_KLINE_TTL.get(interval, 30)
            if now - ts < ttl:
                return df

        try:
            klines = self.client.futures_klines(symbol=symbol, interval=interval, limit=limit)
            df = pd.DataFrame(klines, columns=[
                "time", "open", "high", "low", "close", "volume",
                "ct", "qav", "nt", "tbbav", "tbqav", "ignore"
            ])
            for col in ["open", "high", "low", "close", "volume"]:
                df[col] = df[col].astype(float)
                
            _GLOBAL_KLINE_CACHE[cache_key] = (df, now)
            return df
        except Exception as e:
            logger.error(f"Mum verisi alınamadı {symbol}: {e}")
            return pd.DataFrame()

    def _ema(self, series: pd.Series, period: int) -> pd.Series:
        return series.ewm(span=period, adjust=False).mean()

    def _adx(self, df: pd.DataFrame, period: int = 14) -> tuple:
        import numpy as np
        h, l, c = df["high"], df["low"], df["close"]
        raw_plus  = h.diff().clip(lower=0)
        raw_minus = (-l.diff()).clip(lower=0)
        plus_dm  = raw_plus.where(raw_plus  >= raw_minus, 0.0)
        minus_dm = raw_minus.where(raw_minus >= raw_plus,  0.0)
        tr = pd.concat([h - l, (h - c.shift()).abs(), (l - c.shift()).abs()], axis=1).max(axis=1)
        atr14 = tr.rolling(period).mean()
        plus_di = 100 * (plus_dm.rolling(period).mean() / (atr14 + 1e-10))
        minus_di = 100 * (minus_dm.rolling(period).mean() / (atr14 + 1e-10))
        dx = 100 * ((plus_di - minus_di).abs() / (plus_di + minus_di + 1e-10))
        adx_val = dx.rolling(period).mean().dropna()
        if adx_val.empty:
            return 0.0, 0.0, 0.0
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
            if e21.iloc[-1] > e55.iloc[-1] and c > e21.iloc[-1] and adx_v > 15 and pdi > mdi:
                trend = "BULLISH"
            elif e21.iloc[-1] < e55.iloc[-1] and c < e21.iloc[-1] and adx_v > 15 and mdi > pdi:
                trend = "BEARISH"
            else:
                trend = "NEUTRAL"
            self._4h_cache[symbol] = (trend, now)
            return trend
        except Exception as e:
            logger.debug(f"4H trend hata {symbol}: {e}")
            return "NEUTRAL"

    def get_1h_trend(self, symbol: str) -> str:
        """Sembol 1H trend. Cache: 4h cache ile paylaşımlı slot kullanır."""
        cache_key = f"1h_{symbol}"
        now = time.time()
        if cache_key in self._4h_cache:
            trend, ts = self._4h_cache[cache_key]
            if now - ts < self._4H_TTL:
                return trend
        try:
            df1h = self.get_candles(symbol, "1h", 60)
            if df1h.empty or len(df1h) < 30:
                return "NEUTRAL"
            e21 = self._ema(df1h["close"], 21)
            e55 = self._ema(df1h["close"], 55)
            adx_v, pdi, mdi = self._adx(df1h)
            c = df1h["close"].iloc[-1]
            if e21.iloc[-1] > e55.iloc[-1] and c > e21.iloc[-1] and adx_v > 15 and pdi > mdi:
                trend = "BULLISH"
            elif e21.iloc[-1] < e55.iloc[-1] and c < e21.iloc[-1] and adx_v > 15 and mdi > pdi:
                trend = "BEARISH"
            else:
                trend = "NEUTRAL"
            self._4h_cache[cache_key] = (trend, now)
            return trend
        except Exception as e:
            logger.debug(f"1H trend hata {symbol}: {e}")
            return "NEUTRAL"

    def analyze(self, symbol: str) -> dict:
        """Trend analizi yapar ve yön/skor döner."""
        df15 = self.get_candles(symbol, "15m", 100)
        if df15.empty or len(df15) < 50:
            return {"direction": "NO TRADE", "score": 0, "adx15": 0.0}

        close = df15["close"]
        e9 = self._ema(close, 9)
        e21 = self._ema(close, 21)
        e50 = self._ema(close, 50)
        
        adx_v, pdi, mdi = 0.0, 0.0, 0.0
        try:
            res = self._adx(df15)
            if res and len(res) == 3:
                adx_v, pdi, mdi = res
        except Exception:
            pass
            
        bb_w = self._bollinger_width(df15)
        bb_chg = self._bb_width_change(df15)
        
        c = close.iloc[-1]
        
        try:
            from config import MIN_ADX_15M as _MIN_ADX
        except Exception:
            _MIN_ADX = 18

        # Trend Onayı
        trend_up = (
            e9.iloc[-1] > e21.iloc[-1]
            and c > e50.iloc[-1]
            and adx_v > _MIN_ADX
            and pdi > mdi
        )

        trend_dn = (
            e9.iloc[-1] < e21.iloc[-1]
            and c < e50.iloc[-1]
            and adx_v > _MIN_ADX
            and mdi > pdi
        )

        if not trend_up and not trend_dn:
            return {"direction": "NO TRADE", "score": 0, "adx15": round(adx_v, 1)}

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

        # BTC, 4H ve 1H Trend Onayı
        btc_trend = self.get_btc_trend()
        trend_4h  = self.get_4h_trend(symbol)
        trend_1h  = self.get_1h_trend(symbol)

        if direction == "LONG" and btc_trend == "BULLISH": score += 1.0
        if direction == "SHORT" and btc_trend == "BEARISH": score += 1.0

        if direction == "LONG" and trend_4h == "BULLISH": score += 1.0
        if direction == "SHORT" and trend_4h == "BEARISH": score += 1.0

        if direction == "LONG" and trend_1h == "BULLISH": score += 1.0
        if direction == "SHORT" and trend_1h == "BEARISH": score += 1.0

        # Confluence: 15m(this function) + 1h + 4h — kaç TF yönü onaylıyor?
        # 5m trigger_engine tarafından eklenir → toplam 4 TF
        confluence_raw = 1  # 15m zaten doğrulandı (trend_up / trend_dn geçti)
        if (direction == "LONG" and trend_1h == "BULLISH") or (direction == "SHORT" and trend_1h == "BEARISH"):
            confluence_raw += 1
        if (direction == "LONG" and trend_4h == "BULLISH") or (direction == "SHORT" and trend_4h == "BEARISH"):
            confluence_raw += 1

        try:
            adx_val = float(adx_v) if adx_v is not None else 0.0
            if np.isnan(adx_val): adx_val = 0.0
        except Exception:
            adx_val = 0.0

        return {
            "direction":       direction,
            "score":           min(10.0, score),
            "adx15":           round(adx_val, 1),
            "bb_width":        bb_w,
            "bb_width_chg":    bb_chg,
            "btc_trend":       btc_trend,
            "trend_4h":        trend_4h,
            "trend_1h":        trend_1h,
            "confluence_raw":  confluence_raw,   # 1-3 (15m/1h/4h); trigger_engine 5m ekler → 1-4
        }

    def _get_trend_direction(self, symbol: str, interval: str) -> str:
        """EMA21/55 + ADX ile tek TF yön: LONG / SHORT / NEUTRAL"""
        try:
            limit = 80 if interval in ("1m", "5m") else 60
            df = self.get_candles(symbol, interval, limit)
            if df.empty or len(df) < 30:
                return "NEUTRAL"
            e21 = self._ema(df["close"], 21)
            e55 = self._ema(df["close"], 55)
            adx_v, pdi, mdi = self._adx(df)
            c = df["close"].iloc[-1]
            if e21.iloc[-1] > e55.iloc[-1] and c > e21.iloc[-1] and adx_v > 15 and pdi > mdi:
                return "LONG"
            if e21.iloc[-1] < e55.iloc[-1] and c < e21.iloc[-1] and adx_v > 15 and mdi > pdi:
                return "SHORT"
            return "NEUTRAL"
        except Exception:
            return "NEUTRAL"

    def get_confluence_score(self, symbol: str, side: str) -> dict:
        """
        1m + 5m + 1h + 4h kaçı verilen yönü destekliyor? (0-4)
        side: 'LONG' veya 'SHORT'
        Returns: {score: int(0-4), details: dict, label: str}
        """
        target = side.upper()
        h1_raw = self.get_1h_trend(symbol)
        h4_raw = self.get_4h_trend(symbol)

        def _norm(t: str) -> str:
            t = t.upper()
            if t in ("BULLISH", "LONG", "UP", "BULL"):
                return "LONG"
            if t in ("BEARISH", "SHORT", "DOWN", "BEAR"):
                return "SHORT"
            return "NEUTRAL"

        timeframes = {
            "1m":  self._get_trend_direction(symbol, "1m"),
            "5m":  self._get_trend_direction(symbol, "5m"),
            "1h":  _norm(h1_raw),
            "4h":  _norm(h4_raw),
        }
        aligned = sum(1 for d in timeframes.values() if d == target)
        label = {4: "STRONG", 3: "GOOD", 2: "WEAK"}.get(aligned, "AGAINST")
        return {"score": aligned, "details": timeframes, "label": label}


class MLMarketRegimeClassifier:
    """
    Lightweight ML Market Regime Classifier using K-Means clustering.
    Classifies the market into one of four regimes:
    (1) TRENDING_HIGH_VOL
    (2) TRENDING_LOW_VOL
    (3) CHOPPY_HIGH_VOL
    (4) CHOPPY_LOW_VOL
    """
    def __init__(self, client):
        self.client = client

    def get_regime_features(self, symbol: str = "BTCUSDT", limit: int = 150) -> pd.DataFrame:
        try:
            from core.trend_engine import TrendEngine
            engine = TrendEngine(self.client)
            df = engine.get_candles(symbol, "1h", limit)
            if df.empty or len(df) < 100:
                return pd.DataFrame()

            # 1. ATR (14)
            h, l, c = df["high"], df["low"], df["close"]
            tr = pd.concat([h - l, (h - c.shift()).abs(), (l - c.shift()).abs()], axis=1).max(axis=1)
            df["atr"] = tr.rolling(14).mean()
            df["atr_pct"] = df["atr"] / df["close"]

            # 2. Relative Volume (20)
            df["vol_sma"] = df["volume"].rolling(20).mean()
            df["rel_vol"] = df["volume"] / (df["vol_sma"] + 1e-10)

            # 3. RSI (14)
            delta = df["close"].diff()
            gain = (delta.where(delta > 0, 0.0)).rolling(14).mean()
            loss = (-delta.where(delta < 0, 0.0)).rolling(14).mean()
            rs = gain / (loss + 1e-10)
            df["rsi"] = 100 - (100 / (1 + rs))

            # 4. ADX (14)
            raw_plus = h.diff().clip(lower=0)
            raw_minus = (-l.diff()).clip(lower=0)
            plus_dm = raw_plus.where(raw_plus >= raw_minus, 0.0)
            minus_dm = raw_minus.where(raw_minus >= plus_dm, 0.0)

            plus_di = 100 * (plus_dm.rolling(14).mean() / (df["atr"] + 1e-10))
            minus_di = 100 * (minus_dm.rolling(14).mean() / (df["atr"] + 1e-10))
            dx = 100 * ((plus_di - minus_di).abs() / (plus_di + minus_di + 1e-10))
            df["adx"] = dx.rolling(14).mean()

            df_clean = df[["atr_pct", "rel_vol", "rsi", "adx"]].dropna()
            return df_clean.tail(100)
        except Exception as e:
            logger.error(f"[MLRegime] Feature generation failed: {e}")
            return pd.DataFrame()

    def classify(self, symbol: str = "BTCUSDT") -> str:
        df_features = self.get_regime_features(symbol)
        
        # Rule-based fallback if features are insufficient
        if df_features.empty or len(df_features) < 30:
            logger.warning("[MLRegime] Insufficient features for clustering. Using rule-based fallback.")
            return self._fallback_rule_based(symbol)

        try:
            from sklearn.cluster import KMeans
            import numpy as np

            X = df_features.values
            kmeans = KMeans(n_clusters=4, random_state=42, n_init=10)
            kmeans.fit(X)
            
            last_label = kmeans.labels_[-1]
            centroids = kmeans.cluster_centers_  # (4, 4)

            # Features index: 0: atr_pct, 1: rel_vol, 2: rsi, 3: adx
            cluster_metrics = []
            for j in range(4):
                cluster_metrics.append({
                    "cluster": j,
                    "mean_adx": centroids[j][3],
                    "mean_vol": centroids[j][0]  # atr_pct
                })

            # Sort by mean_adx descending
            sorted_by_adx = sorted(cluster_metrics, key=lambda x: x["mean_adx"], reverse=True)

            # Top 2 are TRENDING, bottom 2 are CHOPPY
            trending_clusters = sorted_by_adx[:2]
            choppy_clusters = sorted_by_adx[2:]

            # Within trending, sort by volume/volatility
            trending_clusters = sorted(trending_clusters, key=lambda x: x["mean_vol"], reverse=True)
            t_high_vol = trending_clusters[0]["cluster"]
            t_low_vol = trending_clusters[1]["cluster"]

            # Within choppy, sort by volume/volatility
            choppy_clusters = sorted(choppy_clusters, key=lambda x: x["mean_vol"], reverse=True)
            c_high_vol = choppy_clusters[0]["cluster"]
            c_low_vol = choppy_clusters[1]["cluster"]

            mapping = {
                t_high_vol: "TRENDING_HIGH_VOL",
                t_low_vol: "TRENDING_LOW_VOL",
                c_high_vol: "CHOPPY_HIGH_VOL",
                c_low_vol: "CHOPPY_LOW_VOL"
            }

            regime = mapping[last_label]
            logger.info(f"[MLRegime] Clustering successful. Current regime: {regime}")
            return regime
        except Exception as e:
            logger.error(f"[MLRegime] KMeans clustering failed: {e}. Using rule-based fallback.")
            return self._fallback_rule_based(symbol)

    def _fallback_rule_based(self, symbol: str) -> str:
        try:
            from core.trend_engine import TrendEngine
            engine = TrendEngine(self.client)
            df = engine.get_candles(symbol, "1h", 30)
            if df.empty:
                return "CHOPPY_LOW_VOL"
            
            btc_trend = engine.get_btc_trend()
            is_trending = btc_trend in ("BULLISH", "BEARISH")
            
            h, l, c = df["high"], df["low"], df["close"]
            tr = pd.concat([h - l, (h - c.shift()).abs(), (l - c.shift()).abs()], axis=1).max(axis=1)
            atr = tr.rolling(14).mean().iloc[-1]
            atr_pct = atr / c.iloc[-1]
            
            is_high_vol = atr_pct > 0.012
            
            if is_trending and is_high_vol:
                return "TRENDING_HIGH_VOL"
            elif is_trending and not is_high_vol:
                return "TRENDING_LOW_VOL"
            elif not is_trending and is_high_vol:
                return "CHOPPY_HIGH_VOL"
            else:
                return "CHOPPY_LOW_VOL"
        except Exception as e:
            logger.error(f"[MLRegime] Fallback failed: {e}")
            return "CHOPPY_LOW_VOL"

