"""
signal_engine.py — AX Sinyal Üretici
=======================================
Teknik analiz + sinyal üretimi. Hiçbir DB yazma işlemi yok — saf hesaplama.

Çıktı formatı:
  direction      — LONG | SHORT | None
  entry          — Giriş fiyatı
  sl             — Stop loss (ATR × SL_ATR_MULT, yapı dışı)
  tp1            — TP1 = entry ± sl_dist × TP1_R
  tp2            — TP2 = entry ± sl_dist × TP2_R
  runner_target  — TP2 + %50 ek mesafe
  rr             — Risk/Reward (tp2 bazlı)
  expected_mfe_r — Tahmin edilen max kazanç (R cinsinden)
  score          — Teknik sinyal kalitesi (0-100)
  confidence     — Güven skoru (0-1)
"""

import time
import logging
import pandas as pd

from config import (
    SL_ATR_MULT, TP1_R, TP2_R, TRAIL_ATR_MULT,
    MIN_RR, MIN_EXPECTED_MFE_R,
)
from coin_library import get_coin_params

logger = logging.getLogger(__name__)

# ─────────────────────────────────────────────────────────────────────────────
# CACHE
# ─────────────────────────────────────────────────────────────────────────────

_btc_cache: dict = {"trend": "NEUTRAL", "ts": 0}
_4h_cache:  dict = {}
_BTC_TTL = 300   # 5 dk
_4H_TTL  = 240   # 4 dk


# ─────────────────────────────────────────────────────────────────────────────
# MUMLAR
# ─────────────────────────────────────────────────────────────────────────────

def get_candles(client, symbol: str, interval: str, limit: int = 200) -> pd.DataFrame:
    try:
        klines = client.futures_klines(symbol=symbol, interval=interval, limit=limit)
        df = pd.DataFrame(klines, columns=[
            "time","open","high","low","close","volume",
            "ct","qav","nt","tbbav","tbqav","ignore"
        ])
        for col in ("open","high","low","close","volume"):
            df[col] = df[col].astype(float)
        return df
    except Exception as e:
        logger.debug(f"[SignalEngine] Mum alınamadı {symbol}/{interval}: {e}")
        return pd.DataFrame()


# ─────────────────────────────────────────────────────────────────────────────
# TEKNİK İNDİKATÖRLER
# ─────────────────────────────────────────────────────────────────────────────

def ema(s: pd.Series, period: int) -> pd.Series:
    return s.ewm(span=period, adjust=False).mean()

def rsi(s: pd.Series, period: int = 14) -> float:
    d = s.diff()
    g = d.clip(lower=0).rolling(period).mean()
    l = (-d.clip(upper=0)).rolling(period).mean()
    return round(100 - (100 / (1 + g.iloc[-1] / (l.iloc[-1] + 1e-10))), 1)

def atr(df: pd.DataFrame, period: int = 14) -> float:
    tr = pd.concat([
        df["high"] - df["low"],
        (df["high"] - df["close"].shift()).abs(),
        (df["low"]  - df["close"].shift()).abs(),
    ], axis=1).max(axis=1)
    return float(tr.rolling(period).mean().iloc[-1])

def adx(df: pd.DataFrame, period: int = 14) -> tuple:
    h, l, c = df["high"], df["low"], df["close"]
    plus_dm  = h.diff().clip(lower=0)
    minus_dm = (-l.diff()).clip(lower=0)
    plus_dm[plus_dm  < minus_dm] = 0
    minus_dm[minus_dm < plus_dm] = 0
    tr = pd.concat([h - l, (h - c.shift()).abs(), (l - c.shift()).abs()], axis=1).max(axis=1)
    atr14    = tr.rolling(period).mean()
    plus_di  = 100 * (plus_dm.rolling(period).mean()  / (atr14 + 1e-10))
    minus_di = 100 * (minus_dm.rolling(period).mean() / (atr14 + 1e-10))
    dx       = 100 * ((plus_di - minus_di).abs() / (plus_di + minus_di + 1e-10))
    adx_val  = dx.rolling(period).mean()
    return float(adx_val.iloc[-1]), float(plus_di.iloc[-1]), float(minus_di.iloc[-1])

def bollinger_width(df: pd.DataFrame, period: int = 20, std: float = 2.0) -> float:
    mid = df["close"].rolling(period).mean()
    std_dev = df["close"].rolling(period).std()
    width = ((mid + std * std_dev) - (mid - std * std_dev)) / (mid + 1e-10) * 100
    return round(float(width.iloc[-1]), 2)

def bb_width_change(df: pd.DataFrame, period: int = 20, std: float = 2.0, lookback: int = 5) -> float:
    mid = df["close"].rolling(period).mean()
    std_dev = df["close"].rolling(period).std()
    width = ((mid + std * std_dev) - (mid - std * std_dev)) / (mid + 1e-10) * 100
    cur  = float(width.iloc[-1])
    past = float(width.iloc[-1 - lookback]) if len(width) > lookback else cur
    return round(cur - past, 3)

def macd_hist(s: pd.Series) -> float:
    fast = ema(s, 12).iloc[-1] - ema(s, 26).iloc[-1]
    slow = ema(pd.Series((ema(s, 12) - ema(s, 26)).values), 9).iloc[-1]
    return fast - slow

def vwap(df: pd.DataFrame) -> float:
    tp = (df["high"] + df["low"] + df["close"]) / 3
    return float((tp * df["volume"]).sum() / (df["volume"].sum() + 1e-10))

def relative_volume(df: pd.DataFrame, period: int = 20) -> float:
    avg = df["volume"].iloc[-period - 1:-1].mean()
    cur = df["volume"].iloc[-1]
    return round(cur / (avg + 1e-10), 2)

def momentum_3c(df: pd.DataFrame) -> float:
    """Son 3 mumun yönlü momentum skoru (-3 ile +3)."""
    if len(df) < 4:
        return 0
    score = 0.0
    for i in range(-3, 0):
        o = df["open"].iloc[i]
        c = df["close"].iloc[i]
        body = abs(c - o)
        rng  = df["high"].iloc[i] - df["low"].iloc[i]
        strength = body / (rng + 1e-10)
        score += strength if c > o else -strength
    return round(score, 3)


# ─────────────────────────────────────────────────────────────────────────────
# BAĞLAM ANALİZLERİ (cache'li)
# ─────────────────────────────────────────────────────────────────────────────

def get_btc_trend(client) -> str:
    """BTC 1H + 4H trend. Cache: 5 dk."""
    now = time.time()
    if now - _btc_cache["ts"] < _BTC_TTL:
        return _btc_cache["trend"]
    try:
        df1h = get_candles(client, "BTCUSDT", "1h", 60)
        df4h = get_candles(client, "BTCUSDT", "4h", 30)
        if df1h.empty or df4h.empty:
            return "NEUTRAL"

        def _trend(df):
            e21 = ema(df["close"], 21).iloc[-1]
            e55 = ema(df["close"], 55).iloc[-1]
            c   = df["close"].iloc[-1]
            adx_v, pdi, mdi = adx(df)
            if e21 > e55 and c > e21 and adx_v > 18 and pdi > mdi:
                return "BULLISH"
            if e21 < e55 and c < e21 and adx_v > 18 and mdi > pdi:
                return "BEARISH"
            return "NEUTRAL"

        t1h = _trend(df1h)
        t4h = _trend(df4h)
        result = t1h if t1h == t4h and t1h != "NEUTRAL" else "NEUTRAL"
        _btc_cache["trend"] = result
        _btc_cache["ts"]    = now
        return result
    except Exception as e:
        logger.debug(f"[SignalEngine] BTC trend hata: {e}")
        return "NEUTRAL"

def get_4h_trend(client, symbol: str) -> str:
    """Sembol 4H trend. Cache: 4 dk."""
    now = time.time()
    if symbol in _4h_cache:
        trend, ts = _4h_cache[symbol]
        if now - ts < _4H_TTL:
            return trend
    try:
        df4h = get_candles(client, symbol, "4h", 60)
        if df4h.empty or len(df4h) < 30:
            return "NEUTRAL"
        e21 = ema(df4h["close"], 21)
        e55 = ema(df4h["close"], 55)
        adx_v, pdi, mdi = adx(df4h)
        c = df4h["close"].iloc[-1]
        if e21.iloc[-1] > e55.iloc[-1] and c > e21.iloc[-1] and adx_v > 20 and pdi > mdi:
            trend = "BULLISH"
        elif e21.iloc[-1] < e55.iloc[-1] and c < e21.iloc[-1] and adx_v > 20 and mdi > pdi:
            trend = "BEARISH"
        else:
            trend = "NEUTRAL"
        _4h_cache[symbol] = (trend, now)
        return trend
    except Exception as e:
        logger.debug(f"[SignalEngine] 4H trend hata {symbol}: {e}")
        return "NEUTRAL"

def get_funding_rate(client, symbol: str) -> float:
    try:
        result = client.futures_funding_rate(symbol=symbol, limit=1)
        return float(result[-1]["fundingRate"]) if result else 0.0
    except Exception:
        return 0.0


# ─────────────────────────────────────────────────────────────────────────────
# SL / TP HESABI
# ─────────────────────────────────────────────────────────────────────────────

def _calc_levels(direction: str, entry: float, atr_val: float,
                 coin_p: dict) -> dict:
    """
    SL = ATR × SL_ATR_MULT (coin profiline göre override edilebilir)
    TP1 = entry ± sl_dist × TP1_R  (0.9R)
    TP2 = entry ± sl_dist × TP2_R  (1.5R)
    Runner = TP2 + %50 ek
    """
    sl_mult = coin_p.get("sl_atr_mult", SL_ATR_MULT)
    sl_dist = atr_val * sl_mult

    if direction == "LONG":
        sl  = entry - sl_dist
        tp1 = entry + sl_dist * TP1_R
        tp2 = entry + sl_dist * TP2_R
        runner = tp2 + sl_dist * 0.5
    else:
        sl  = entry + sl_dist
        tp1 = entry - sl_dist * TP1_R
        tp2 = entry - sl_dist * TP2_R
        runner = tp2 - sl_dist * 0.5

    rr = abs(tp2 - entry) / (sl_dist + 1e-10)

    return {
        "sl": round(sl, 8), "tp1": round(tp1, 8),
        "tp2": round(tp2, 8), "runner_target": round(runner, 8),
        "rr": round(rr, 3), "sl_dist": sl_dist,
    }

def _estimate_mfe_r(bb_w: float, adx_v: float, mom3c: float,
                    direction: str, coin_p: dict) -> float:
    """
    Beklenen MFE tahmini (R cinsinden).
    BB genişliği + ADX + momentum kombinasyonu.
    """
    profile = coin_p.get("volatility_profile", "normal")
    base = {"stable": 1.2, "normal": 1.4, "volatile": 1.6, "dangerous": 1.2}.get(profile, 1.4)

    if adx_v > 30:
        base += 0.3
    elif adx_v > 25:
        base += 0.15

    if bb_w > 3.0:
        base += 0.2
    elif bb_w > 2.0:
        base += 0.1

    if direction == "LONG" and mom3c > 1.5:
        base += 0.15
    elif direction == "SHORT" and mom3c < -1.5:
        base += 0.15

    hist_mfe = coin_p.get("avg_mfe", 0)
    if hist_mfe and hist_mfe > 0:
        base = base * 0.6 + hist_mfe * 0.4

    return round(base, 2)


def _estimate_mae_r(bb_w: float, adx_v: float, coin_p: dict) -> float:
    """
    Beklenen MAE tahmini (R cinsinden).
    Düşük ADX + dar BB = daha fazla geri çekilme riski.
    """
    profile = coin_p.get("volatility_profile", "normal")
    base = {"stable": 0.3, "normal": 0.45, "volatile": 0.6, "dangerous": 0.8}.get(profile, 0.45)

    # Zayıf trend → daha fazla geri çekilme
    if adx_v < 20:
        base += 0.2
    elif adx_v < 25:
        base += 0.1

    # Dar BB → chop riski
    if bb_w < 1.5:
        base += 0.15

    # Yüksek fakeout riski
    fakeout_rate = coin_p.get("fakeout_rate", 0)
    base += fakeout_rate * 0.3

    hist_mae = coin_p.get("avg_mae", 0)
    if hist_mae and hist_mae > 0:
        base = base * 0.6 + hist_mae * 0.4

    return round(min(base, 1.5), 2)

def _calc_score(adx_v: float, bb_w: float, bb_chg: float, rv: float,
                mom3c: float, direction: str, rsi5: float, rsi1: float) -> tuple:
    """
    Teknik sinyal kalite skoru (0-100) ve confidence (0-1).
    """
    score = 50.0  # Temel skor

    # ADX güç katkısı
    if adx_v > 35:    score += 15
    elif adx_v > 28:  score += 10
    elif adx_v > 22:  score += 5

    # BB genişliği — volatilite elverişli mi?
    if 2.5 < bb_w < 5.0:  score += 10
    elif 1.8 < bb_w:      score += 5

    # BB büyüme — kırılım yakın mı?
    if bb_chg > 0.5:   score += 8
    elif bb_chg > 0.2: score += 4

    # Relative volume
    if rv > 2.0:   score += 8
    elif rv > 1.5: score += 4

    # Momentum uyumu
    if direction == "LONG"  and mom3c > 1.5:   score += 8
    elif direction == "SHORT" and mom3c < -1.5: score += 8
    elif direction == "LONG"  and mom3c > 0.5:  score += 4
    elif direction == "SHORT" and mom3c < -0.5: score += 4

    # RSI merkeze yakınlık (aşırılardan uzak = iyi)
    rsi_mid_dist = abs(rsi5 - 50)
    if rsi_mid_dist > 30: score -= 5   # Aşırı alım/satım bölgesi

    score = max(0, min(100, score))
    confidence = round(score / 100, 3)
    return round(score, 1), confidence


# ─────────────────────────────────────────────────────────────────────────────
# ANA SİNYAL FONKSİYONU
# ─────────────────────────────────────────────────────────────────────────────

def generate_signal(client, symbol: str, coin_info: dict = None) -> dict:
    """
    Bir sembol için teknik analiz yapar ve sinyal üretir.

    Returns:
        dict — direction None ise sinyal yok.
        direction, entry, sl, tp1, tp2, runner_target, rr,
        expected_mfe_r, score, confidence + debug alanları
    """
    NULL = {"symbol": symbol, "direction": None}
    coin_p = get_coin_params(symbol)

    # ── 15m Ana Trend ────────────────────────────────────────────────────────
    df15 = get_candles(client, symbol, "15m", 100)
    if df15.empty or len(df15) < 50:
        return NULL

    e9_15, e21_15, e50_15 = ema(df15["close"], 9), ema(df15["close"], 21), ema(df15["close"], 50)
    adx15, pdi15, mdi15   = adx(df15)
    c15    = df15["close"].iloc[-1]
    bb_w   = bollinger_width(df15)
    bb_chg = bb_width_change(df15)

    # Coin profiline göre filtre eşikleri
    min_bb  = coin_p.get("min_bb_width", 1.3)
    min_adx = coin_p.get("min_adx", 20)

    if bb_w < min_bb:
        return NULL

    trend_up15 = (
        e9_15.iloc[-1] > e21_15.iloc[-1] > e50_15.iloc[-1]
        and c15 > e21_15.iloc[-1]
        and adx15 > min_adx
        and pdi15 > mdi15
    )
    trend_dn15 = (
        e9_15.iloc[-1] < e21_15.iloc[-1] < e50_15.iloc[-1]
        and c15 < e21_15.iloc[-1]
        and adx15 > min_adx
        and mdi15 > pdi15
    )

    if not trend_up15 and not trend_dn15:
        return NULL

    # ── 5m Giriş Sinyali ────────────────────────────────────────────────────
    df5 = get_candles(client, symbol, "5m", 150)
    if df5.empty or len(df5) < 50:
        return NULL

    e9_5, e21_5, e50_5 = ema(df5["close"], 9), ema(df5["close"], 21), ema(df5["close"], 50)
    rsi5   = rsi(df5["close"], 14)
    c5     = df5["close"].iloc[-1]
    atr5   = atr(df5, 14)

    if (atr5 / (c5 + 1e-10) * 100) < 0.03:
        return NULL

    bull5 = trend_up15 and e9_5.iloc[-1] > e21_5.iloc[-1] and 35 < rsi5 < 75
    bear5 = trend_dn15 and e9_5.iloc[-1] < e21_5.iloc[-1] and 25 < rsi5 < 65

    if not bull5 and not bear5:
        return NULL

    # ── 1m Kesin Giriş ──────────────────────────────────────────────────────
    df1 = get_candles(client, symbol, "1m", 100)
    if df1.empty or len(df1) < 30:
        return NULL

    rsi1   = rsi(df1["close"], 7)
    c1     = df1["close"].iloc[-1]
    atr1   = atr(df1, 14)
    hist   = macd_hist(df1["close"])
    rv     = relative_volume(df1, 20)
    mom3c  = momentum_3c(df1)

    # RSI 1m giriş onayı
    if bull5 and not (32 < rsi1 < 75):
        return NULL
    if bear5 and not (25 < rsi1 < 68):
        return NULL

    # ── Yön Belirle ─────────────────────────────────────────────────────────
    if bull5:
        direction = "LONG"
    elif bear5:
        direction = "SHORT"
    else:
        return NULL

    # ── Funding Rate ─────────────────────────────────────────────────────────
    funding = get_funding_rate(client, symbol)
    if direction == "LONG"  and funding >  0.003:
        return NULL   # Longs ağır, olumsuz funding (>0.3%)
    if direction == "SHORT" and funding < -0.003:
        return NULL

    # ── BTC Trend (bilgi amaçlı, ENGELLEME YOK) ─────────────────────────────
    btc_trend = get_btc_trend(client)
    trend_4h  = get_4h_trend(client, symbol)

    # ── Seviyeler Hesapla ────────────────────────────────────────────────────
    entry  = c1
    levels = _calc_levels(direction, entry, atr1, coin_p)

    if levels["rr"] < MIN_RR:
        return NULL

    # ── MFE / MAE Tahmini ────────────────────────────────────────────────────
    expected_mfe_r = _estimate_mfe_r(bb_w, adx15, mom3c, direction, coin_p)
    expected_mae_r = _estimate_mae_r(bb_w, adx15, coin_p)

    if expected_mfe_r < MIN_EXPECTED_MFE_R:
        return NULL

    # ── Skor Hesapla ─────────────────────────────────────────────────────────
    score, confidence = _calc_score(adx15, bb_w, bb_chg, rv, mom3c, direction, rsi5, rsi1)

    return {
        "symbol":          symbol,
        "direction":       direction,
        "entry":           round(entry, 8),
        "sl":              levels["sl"],
        "tp1":             levels["tp1"],
        "tp2":             levels["tp2"],
        "runner_target":   levels["runner_target"],
        "rr":              levels["rr"],
        "expected_mfe_r":  expected_mfe_r,
        "expected_mae_r":  expected_mae_r,
        "score":           score,
        "confidence":      confidence,
        # debug / loglama
        "atr":             round(atr1, 8),
        "atr5":            round(atr5, 8),
        "adx15":           round(adx15, 1),
        "bb_width":        bb_w,
        "bb_width_chg":    bb_chg,
        "rsi5":            rsi5,
        "rsi1":            rsi1,
        "rv":              rv,
        "momentum_3c":     mom3c,
        "macd_hist":       round(hist, 6),
        "funding":         round(funding * 100, 4),
        "btc_trend":       btc_trend,
        "trend_4h":        trend_4h,
        "volume_m":        coin_info["volume"] if coin_info else 0,
    }
