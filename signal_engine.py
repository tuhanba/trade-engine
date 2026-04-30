"""
signal_engine.py — Setup bul, SL/TP hesapla, candidate dict döndür.

Her sembol için:
  1. 15m kline çek (100 mum)
  2. ATR, EMA20, EMA50, RSI hesapla
  3. Setup tespit et: BREAKOUT / PULLBACK
  4. SL = ATR × SL_ATR_MULT (yapıya da bak)
  5. TP1, TP2, runner = RR çarpanlarına göre
  6. RR ve expected_mfe_r hesapla
  7. MIN_RR ve MIN_EXPECTED_MFE_R geçemezse None döndür
"""

import logging
import time
from typing import Optional
from datetime import datetime, timezone

from binance.client import Client

import config
from market_scan import _get_client

logger = logging.getLogger(__name__)

# Kline indeksleri
_O, _H, _L, _C, _V = 1, 2, 3, 4, 5

# Hangi session'dayız?
_SESSIONS = [
    ("ASIA",   0,  8),
    ("LONDON", 8,  13),
    ("NY",     13, 17),
    ("OVERLAP",17, 21),
    ("OFF",    21, 24),
]


def _session() -> str:
    h = datetime.now(timezone.utc).hour
    for name, start, end in _SESSIONS:
        if start <= h < end:
            return name
    return "OFF"


# ---------- İndikatörler ----------

def _atr(klines: list, period: int = 14) -> float:
    trs = []
    for i in range(1, len(klines)):
        h  = float(klines[i][_H])
        l  = float(klines[i][_L])
        pc = float(klines[i - 1][_C])
        trs.append(max(h - l, abs(h - pc), abs(l - pc)))
    if not trs:
        return 0.0
    subset = trs[-period:]
    return sum(subset) / len(subset)


def _ema(values: list, period: int) -> float:
    if len(values) < period:
        return values[-1] if values else 0.0
    k = 2 / (period + 1)
    ema = sum(values[:period]) / period
    for v in values[period:]:
        ema = v * k + ema * (1 - k)
    return ema


def _rsi(closes: list, period: int = 14) -> float:
    if len(closes) < period + 1:
        return 50.0
    gains, losses = [], []
    for i in range(1, period + 1):
        d = closes[-period - 1 + i] - closes[-period - 2 + i]
        (gains if d > 0 else losses).append(abs(d))
    ag = sum(gains) / period if gains else 0.0
    al = sum(losses) / period if losses else 1e-10
    rs = ag / al
    return 100 - (100 / (1 + rs))


def _swing_low(lows: list, lookback: int = 10) -> float:
    return min(lows[-lookback:])


def _swing_high(highs: list, lookback: int = 10) -> float:
    return max(highs[-lookback:])


def _avg_volume(klines: list, period: int = 20) -> float:
    vols = [float(k[_V]) for k in klines[-period:]]
    return sum(vols) / len(vols) if vols else 0.0


# ---------- Setup tespiti ----------

def _find_setup(klines: list) -> Optional[dict]:
    """
    BREAKOUT / PULLBACK / MOMENTUM setup döndürür (öncelik sırasıyla).
    Returns: {setup_type, direction} veya None
    """
    closes = [float(k[_C]) for k in klines]
    highs  = [float(k[_H]) for k in klines]
    lows   = [float(k[_L]) for k in klines]

    ema20 = _ema(closes, 20)
    ema50 = _ema(closes, 50)
    rsi   = _rsi(closes, 14)
    price = closes[-1]

    # Son mumun hacim gücü
    last_vol = float(klines[-1][_V])
    avg_vol  = _avg_volume(klines, 20)
    vol_ok   = last_vol >= avg_vol * 1.1

    # Önceki 20 mum yüksek/düşük (son mum hariç)
    prev_high = max(highs[-21:-1])
    prev_low  = min(lows[-21:-1])

    trend_up   = ema20 > ema50
    trend_down = ema20 < ema50

    # BREAKOUT LONG: fiyat önceki yüksekten yukarı kırdı, trend yukarı, hacim var
    if price > prev_high and trend_up and 45 <= rsi <= 75 and vol_ok:
        return {"setup_type": "BREAKOUT", "direction": "LONG"}

    # BREAKOUT SHORT: fiyat önceki düşükten aşağı kırdı, trend aşağı, hacim var
    if price < prev_low and trend_down and 25 <= rsi <= 55 and vol_ok:
        return {"setup_type": "BREAKOUT", "direction": "SHORT"}

    # PULLBACK LONG: trend yukarı, fiyat EMA20'ye yakın (%1.5 içinde), RSI nötr
    ema20_dist = abs(price - ema20) / ema20 * 100
    if trend_up and ema20_dist <= 1.5 and price >= ema20 * 0.988 and 33 <= rsi <= 58:
        return {"setup_type": "PULLBACK", "direction": "LONG"}

    # PULLBACK SHORT: trend aşağı, fiyat EMA20'ye yakın, RSI nötr
    if trend_down and ema20_dist <= 1.5 and price <= ema20 * 1.012 and 42 <= rsi <= 67:
        return {"setup_type": "PULLBACK", "direction": "SHORT"}

    # MOMENTUM LONG: güçlü yukarı trend, RSI ivmeli ama aşırı değil, hacim var
    if trend_up and 55 <= rsi <= 70 and vol_ok and price > ema20 * 1.002:
        return {"setup_type": "MOMENTUM", "direction": "LONG"}

    # MOMENTUM SHORT: güçlü aşağı trend, RSI ivmeli ama aşırı değil, hacim var
    if trend_down and 30 <= rsi <= 45 and vol_ok and price < ema20 * 0.998:
        return {"setup_type": "MOMENTUM", "direction": "SHORT"}

    return None


# ---------- Ana fonksiyon ----------

def analyze_symbol(symbol: str) -> Optional[dict]:
    """
    Sembolü analiz et; geçerli setup varsa candidate dict döndür, yoksa None.
    """
    try:
        client = _get_client()
        klines = client.futures_klines(
            symbol=symbol, interval=Client.KLINE_INTERVAL_15MINUTE, limit=100
        )
    except Exception as e:
        logger.error(f"[SignalEngine] {symbol} kline hata: {e}")
        return None

    if len(klines) < 55:
        return None

    setup = _find_setup(klines)
    if not setup:
        return None

    closes = [float(k[_C]) for k in klines]
    highs  = [float(k[_H]) for k in klines]
    lows   = [float(k[_L]) for k in klines]

    direction = setup["direction"]
    entry     = closes[-1]
    atr       = _atr(klines, 14)

    if atr <= 0:
        return None

    # --- SL hesapla ---
    sl_atr = atr * config.SL_ATR_MULT

    if direction == "LONG":
        sl_struct = _swing_low(lows, 10)
        # SL: ATR bazlı ile yapı bazlıdan büyüğünü al (en uzak olanı)
        sl = min(entry - sl_atr, sl_struct - atr * 0.1)
        sl = min(sl, entry * 0.995)   # en az %0.5 uzakta
    else:
        sl_struct = _swing_high(highs, 10)
        sl = max(entry + sl_atr, sl_struct + atr * 0.1)
        sl = max(sl, entry * 1.005)

    sl_dist = abs(entry - sl)
    if sl_dist <= 0:
        return None

    # --- TP hesapla ---
    sign = 1 if direction == "LONG" else -1
    tp1          = entry + sign * sl_dist * config.TP1_R
    tp2          = entry + sign * sl_dist * config.TP2_R
    runner_target = entry + sign * sl_dist * 3.0

    # --- RR ve expected MFE ---
    rr = sl_dist * config.TP2_R / sl_dist   # = TP2_R (sabit oran)

    # expected_mfe_r: son 20 mumun max hareket / sl_dist — setup tipine göre çarpan
    recent_moves = [float(k[_H]) - float(k[_L]) for k in klines[-20:]]
    avg_candle_range = sum(recent_moves) / len(recent_moves) if recent_moves else atr
    mfe_mult = {"BREAKOUT": 3.5, "MOMENTUM": 3.0, "PULLBACK": 2.5}.get(
        setup["setup_type"], 2.5
    )
    expected_mfe_r = round(avg_candle_range * mfe_mult / sl_dist, 2)

    # --- Filtreler ---
    if rr < config.MIN_RR:
        logger.debug(f"[SignalEngine] {symbol} RR={rr:.2f} < MIN_RR={config.MIN_RR}")
        return None

    if expected_mfe_r < config.MIN_EXPECTED_MFE_R:
        logger.debug(f"[SignalEngine] {symbol} MFE={expected_mfe_r} < {config.MIN_EXPECTED_MFE_R}")
        return None

    return {
        "symbol":          symbol,
        "direction":       direction,
        "entry":           round(entry, 8),
        "sl":              round(sl, 8),
        "tp1":             round(tp1, 8),
        "tp2":             round(tp2, 8),
        "runner_target":   round(runner_target, 8),
        "rr":              round(rr, 4),
        "expected_mfe_r":  expected_mfe_r,
        "setup_type":      setup["setup_type"],
        "session":         _session(),
        "atr":             round(atr, 8),
        "score":           0,          # ai_brain dolduracak
        "confidence":      0.0,        # ai_brain dolduracak
        "decision":        "PENDING",
        "ax_mode":         config.AX_MODE,
        "execution_mode":  config.EXECUTION_MODE,
        "created_at":      datetime.now(timezone.utc).isoformat(),
    }


def scan_signals(symbols: list[dict]) -> list[dict]:
    """
    market_scan.scan_market() çıktısını al, her sembol için analyze_symbol çalıştır.
    Candidate listesi döndürür.
    """
    candidates = []
    for sym_info in symbols:
        symbol = sym_info["symbol"]
        candidate = analyze_symbol(symbol)
        if candidate:
            candidates.append(candidate)
            logger.info(f"[SignalEngine] {symbol} {candidate['direction']} "
                        f"RR={candidate['rr']} MFE={candidate['expected_mfe_r']}")
        time.sleep(0.05)   # rate limit
    return candidates
