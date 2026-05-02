"""
Risk Engine v3 — Exchange Compliance + Fee/Slippage
=====================================================
- ATR-based SL/TP (config'den)
- Binance tick_size, step_size, min_notional, lot_size uyumu
- Fee + slippage → net_RR hesabı
- Liquidation distance kontrolü
- Risk guard: valid=False olmadan işlem açılmaz

Her candidate sinyal için hesap yapılır.
Sadece TRADE_THRESHOLD üstü sinyaller işlem açabilir — bu kontrol scalp_bot'ta yapılır.
"""
import logging
import math
import pandas as pd

logger = logging.getLogger(__name__)

try:
    import sys, os
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    from config import (
        SL_ATR_MULT, TP1_R, TP1_CLOSE_PCT, TP2_R, TP2_CLOSE_PCT,
        TP3_R, RUNNER_CLOSE_PCT, MIN_RR,
        BREAKEVEN_ENABLED, BREAKEVEN_TRIGGER_R, BREAKEVEN_OFFSET_PCT,
        TAKER_FEE_PCT, SLIPPAGE_PCT, MAX_LEVERAGE_ALLOWED,
    )
except ImportError:
    SL_ATR_MULT          = 1.2
    TP1_R                = 1.0
    TP1_CLOSE_PCT        = 30
    TP2_R                = 2.0
    TP2_CLOSE_PCT        = 50
    TP3_R                = 3.0
    RUNNER_CLOSE_PCT     = 20
    MIN_RR               = 1.5
    BREAKEVEN_ENABLED    = True
    BREAKEVEN_TRIGGER_R  = 1.0
    BREAKEVEN_OFFSET_PCT = 0.05
    TAKER_FEE_PCT        = 0.04
    SLIPPAGE_PCT         = 0.05
    MAX_LEVERAGE_ALLOWED = 20


def _round_to_tick(price: float, tick_size: float) -> float:
    """Fiyatı tick_size'a göre yuvarla."""
    if tick_size <= 0:
        return price
    decimals = max(0, -int(math.floor(math.log10(tick_size))))
    return round(round(price / tick_size) * tick_size, decimals)

def _round_to_step(qty: float, step_size: float) -> float:
    """Miktarı step_size'a göre aşağı yuvarla (Binance: floor)."""
    if step_size <= 0:
        return qty
    return math.floor(qty / step_size) * step_size


class RiskEngine:
    def __init__(self, client):
        self.client      = client
        self.base_risk_pct = 1.0
        self.min_rr      = MIN_RR
        self._exch_cache: dict = {}
        self._exch_ts:    float = 0
        self._EXCH_TTL   = 3600

    # ── Mum verisi ────────────────────────────────────────────────────────────

    def get_candles(self, symbol: str, interval: str, limit: int) -> pd.DataFrame:
        try:
            klines = self.client.futures_klines(symbol=symbol, interval=interval, limit=limit)
            df = pd.DataFrame(klines, columns=[
                "time","open","high","low","close","volume",
                "ct","qav","nt","tbbav","tbqav","ignore"
            ])
            for col in ("open","high","low","close","volume"):
                df[col] = df[col].astype(float)
            return df
        except Exception as e:
            logger.error(f"Mum alınamadı {symbol}/{interval}: {e}")
            return pd.DataFrame()

    # ── ATR ───────────────────────────────────────────────────────────────────

    def _atr(self, df: pd.DataFrame, period: int = 14) -> float:
        tr = pd.concat([
            df["high"] - df["low"],
            (df["high"] - df["close"].shift()).abs(),
            (df["low"]  - df["close"].shift()).abs(),
        ], axis=1).max(axis=1)
        val = tr.rolling(period).mean().iloc[-1]
        return float(val) if not pd.isna(val) else 0.0

    # ── Exchange info ─────────────────────────────────────────────────────────

    def _get_symbol_info(self, symbol: str) -> dict:
        import time
        now = time.time()
        if now - self._exch_ts > self._EXCH_TTL:
            try:
                info = self.client.futures_exchange_info()
                for s in info.get("symbols", []):
                    sym = s["symbol"]
                    tick = step = min_notional = None
                    for f in s.get("filters", []):
                        ft = f["filterType"]
                        if ft == "PRICE_FILTER":
                            tick = float(f["tickSize"])
                        elif ft == "LOT_SIZE":
                            step = float(f["stepSize"])
                        elif ft in ("MIN_NOTIONAL", "NOTIONAL"):
                            min_notional = float(f.get("notional") or f.get("minNotional") or 5)
                    self._exch_cache[sym] = {
                        "tick_size":    tick or 0.0001,
                        "step_size":    step or 0.001,
                        "min_notional": min_notional or 5.0,
                    }
                self._exch_ts = now
            except Exception as e:
                logger.warning(f"Exchange info cache yüklenemedi: {e}")
        return self._exch_cache.get(symbol, {
            "tick_size": 0.0001, "step_size": 0.001, "min_notional": 5.0
        })

    # ── Ana hesaplama ─────────────────────────────────────────────────────────

    def calculate(self, symbol: str, direction: str, entry: float,
                  quality: str, balance: float) -> dict:
        """
        Risk parametrelerini hesapla.
        candidate sinyaller dahil herkes için çalışır.
        valid=False → trade açılmaz.
        """
        if direction == "NO TRADE" or entry <= 0:
            return {"score": 0, "valid": False, "risk_reject_reason": "no_trade"}

        df15 = self.get_candles(symbol, "15m", 50)
        if df15.empty:
            return {"score": 0, "valid": False, "risk_reject_reason": "no_candle_data"}

        atr_val = self._atr(df15)
        if atr_val <= 0:
            return {"score": 0, "valid": False, "risk_reject_reason": "invalid_atr"}

        # ── Coin parametreleri ────────────────────────────────────────────────
        try:
            from coin_library import get_coin_params
            cp = get_coin_params(symbol)
            sl_mult   = cp.get("sl_atr_mult", SL_ATR_MULT)
            base_risk = cp.get("risk_pct",    self.base_risk_pct)
            max_lev   = min(cp.get("max_leverage", 20), MAX_LEVERAGE_ALLOWED)
        except Exception:
            sl_mult   = SL_ATR_MULT
            base_risk = self.base_risk_pct
            max_lev   = MAX_LEVERAGE_ALLOWED

        # ── Exchange bilgisi ──────────────────────────────────────────────────
        sym_info    = self._get_symbol_info(symbol)
        tick_size   = sym_info["tick_size"]
        step_size   = sym_info["step_size"]
        min_notional= sym_info["min_notional"]

        # ── SL/TP hesabı ──────────────────────────────────────────────────────
        sl_dist = atr_val * sl_mult

        if direction == "LONG":
            sl  = _round_to_tick(entry - sl_dist, tick_size)
            tp1 = _round_to_tick(entry + sl_dist * TP1_R,   tick_size)
            tp2 = _round_to_tick(entry + sl_dist * TP2_R,   tick_size)
            tp3 = _round_to_tick(entry + sl_dist * TP3_R,   tick_size)
            breakeven_sl = _round_to_tick(
                entry + entry * BREAKEVEN_OFFSET_PCT / 100, tick_size)
        else:
            sl  = _round_to_tick(entry + sl_dist, tick_size)
            tp1 = _round_to_tick(entry - sl_dist * TP1_R,   tick_size)
            tp2 = _round_to_tick(entry - sl_dist * TP2_R,   tick_size)
            tp3 = _round_to_tick(entry - sl_dist * TP3_R,   tick_size)
            breakeven_sl = _round_to_tick(
                entry - entry * BREAKEVEN_OFFSET_PCT / 100, tick_size)

        actual_sl_dist = abs(entry - sl)
        rr = abs(tp2 - entry) / (actual_sl_dist + 1e-10)

        # ── Dinamik risk ──────────────────────────────────────────────────────
        risk_pct = base_risk
        if quality == "S":
            risk_pct = base_risk * 2.0
        elif quality == "A+":
            risk_pct = base_risk * 1.5
        elif quality == "A":
            risk_pct = base_risk * 1.0
        elif quality == "B":
            risk_pct = base_risk * 0.5
        elif quality in ("C", "D"):
            risk_pct = 0.0
        risk_pct = min(risk_pct, 3.0)

        risk_amount   = balance * (risk_pct / 100)
        raw_qty       = risk_amount / actual_sl_dist if actual_sl_dist > 0 else 0
        position_size = _round_to_step(raw_qty, step_size)
        notional      = position_size * entry

        # ── Fee & Slippage ────────────────────────────────────────────────────
        # Taker fee her iki taraf: open + close
        estimated_fee      = notional * (TAKER_FEE_PCT / 100) * 2
        estimated_slippage = notional * (SLIPPAGE_PCT / 100)
        total_cost         = estimated_fee + estimated_slippage

        # Net RR: TP2 kârından maliyet düşülmüş / (SL kaybı + maliyet)
        gross_tp2_gain = abs(tp2 - entry) * position_size
        gross_sl_loss  = actual_sl_dist * position_size
        net_gain       = gross_tp2_gain - total_cost
        net_loss       = gross_sl_loss  + total_cost
        net_rr         = net_gain / (net_loss + 1e-10)

        # ── Minimum notional kontrolü ─────────────────────────────────────────
        risk_reject_reason = None
        if risk_pct <= 0:
            risk_reject_reason = "quality_too_low"
        elif notional < min_notional:
            risk_reject_reason = "below_min_notional"
        elif rr < self.min_rr:
            risk_reject_reason = "bad_rr"
        elif position_size <= 0:
            risk_reject_reason = "zero_position_size"

        # ── Kaldıraç ──────────────────────────────────────────────────────────
        leverage = min(max_lev, max(1, int(notional / (balance + 1e-10)))) if balance > 0 else 1

        # ── Liquidation distance (yaklaşık) ──────────────────────────────────
        # Futures: ~1/leverage, biraz buffer bırak
        liq_pct          = 1.0 / (leverage + 1e-10)
        sl_dist_pct      = actual_sl_dist / (entry + 1e-10)
        liq_distance_pct = round(liq_pct * 100, 2)

        valid = risk_reject_reason is None

        # ── Skor ─────────────────────────────────────────────────────────────
        score = 5.0
        if rr > 2.5:   score += 3.0
        elif rr > 2.0: score += 2.0
        elif rr > 1.8: score += 1.0
        if sl_dist_pct < 0.003: score -= 2.0
        if sl_dist_pct > 0.04:  score -= 2.0
        if net_rr < 1.2:        score -= 1.5
        score = round(min(10.0, max(0.0, score)), 2)

        return {
            "valid":                valid,
            "risk_reject_reason":   risk_reject_reason,
            "score":                score,
            # Fiyatlar
            "sl":                   sl,
            "tp1":                  tp1,
            "tp2":                  tp2,
            "tp3":                  tp3,
            "breakeven_sl":         breakeven_sl,
            # Risk metrikleri
            "atr":                  round(atr_val, 6),
            "sl_dist":              round(actual_sl_dist, 6),
            "stop_distance_percent":round(sl_dist_pct * 100, 3),
            "rr":                   round(rr, 2),
            "net_rr":               round(net_rr, 2),
            # Pozisyon
            "risk_pct":             round(risk_pct, 2),
            "risk_amount":          round(risk_amount, 2),
            "position_size":        round(position_size, 4),
            "notional":             round(notional, 2),
            "leverage":             leverage,
            "max_loss":             round(risk_amount, 2),
            # Exchange
            "tick_size":            tick_size,
            "step_size":            step_size,
            "min_notional":         min_notional,
            # Maliyet
            "estimated_fee":        round(estimated_fee, 4),
            "estimated_slippage":   round(estimated_slippage, 4),
            # Liquidation
            "liq_distance_pct":     liq_distance_pct,
            # Breakeven
            "breakeven_enabled":    BREAKEVEN_ENABLED,
            "breakeven_trigger_r":  BREAKEVEN_TRIGGER_R,
            # TP oranları
            "tp1_close_pct":        TP1_CLOSE_PCT,
            "tp2_close_pct":        TP2_CLOSE_PCT,
            "runner_close_pct":     RUNNER_CLOSE_PCT,
        }
