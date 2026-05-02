"""
Risk Engine v2.1
Stop, TP, RR ve pozisyon büyüklüğünü hesaplar.
v2.1 Değişiklikleri (Backtest Bulgularına Dayalı):
  - SL_ATR_MULT: config'den okunur (1.5 → 1.2)
  - TP1_CLOSE_PCT: 40 → 30, TP2_CLOSE_PCT: 30 → 50
  - Breakeven parametreleri result dict'e eklendi
  - Config'den SL/TP çarpanları okunur
"""
import logging
import pandas as pd

logger = logging.getLogger(__name__)

# Config'den parametreleri al
try:
    import sys, os
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    from config import (
        SL_ATR_MULT, TP1_R, TP1_CLOSE_PCT, TP2_R, TP2_CLOSE_PCT,
        TP3_R, RUNNER_CLOSE_PCT, MIN_RR,
        BREAKEVEN_ENABLED, BREAKEVEN_TRIGGER_R, BREAKEVEN_OFFSET_PCT
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

class RiskEngine:
    def __init__(self, client):
        self.client = client
        self.base_risk_pct = 1.0
        self.min_rr = MIN_RR

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

    def _atr(self, df: pd.DataFrame, period: int = 14) -> float:
        high_low = df['high'] - df['low']
        high_close = (df['high'] - df['close'].shift()).abs()
        low_close = (df['low'] - df['close'].shift()).abs()
        ranges = pd.concat([high_low, high_close, low_close], axis=1)
        true_range = ranges.max(axis=1)
        return true_range.rolling(period).mean().iloc[-1]

    def calculate(self, symbol: str, direction: str, entry: float, quality: str, balance: float) -> dict:
        """Risk parametrelerini hesaplar."""
        if direction == "NO TRADE" or entry == 0:
            return {"score": 0, "valid": False}

        df15 = self.get_candles(symbol, "15m", 50)
        if df15.empty:
            return {"score": 0, "valid": False}

        atr_val = self._atr(df15)
        if pd.isna(atr_val) or atr_val == 0:
            return {"score": 0, "valid": False}

        # Coin profili parametrelerini al (coin_library'den)
        try:
            from coin_library import get_coin_params
            coin_params = get_coin_params(symbol)
            # Config değerlerine öncelik ver, coin_library fallback
            sl_mult  = coin_params.get("sl_atr_mult", SL_ATR_MULT)
            base_risk = coin_params.get("risk_pct", self.base_risk_pct)
            max_lev  = coin_params.get("max_leverage", 20)
        except Exception as e:
            logger.warning(f"Coin profili alınamadı: {e}")
            sl_mult   = SL_ATR_MULT
            base_risk = self.base_risk_pct
            max_lev   = 20

        # ── Stop Mesafesi (ATR bazlı, config'den sıkılaştırılmış) ─────────────
        sl_dist = atr_val * sl_mult

        # ── TP Seviyeleri (R çarpanı bazlı) ──────────────────────────────────
        if direction == "LONG":
            sl  = entry - sl_dist
            tp1 = entry + sl_dist * TP1_R
            tp2 = entry + sl_dist * TP2_R
            tp3 = entry + sl_dist * TP3_R
            # Breakeven: TP1 tetiklendiğinde SL bu seviyeye çekilir
            breakeven_sl = entry + (entry * BREAKEVEN_OFFSET_PCT / 100)
        else:
            sl  = entry + sl_dist
            tp1 = entry - sl_dist * TP1_R
            tp2 = entry - sl_dist * TP2_R
            tp3 = entry - sl_dist * TP3_R
            breakeven_sl = entry - (entry * BREAKEVEN_OFFSET_PCT / 100)

        rr = abs(tp2 - entry) / (sl_dist + 1e-10)

        # ── Dinamik Risk Yönetimi — Kalite Bazlı ────────────────────────────
        # S  : %2.0 risk — Composite skor ≥10, en güvenilir setup
        # A+ : %1.5 risk — Yüksek kalite, güçlü trend
        # A  : %1.0 risk — İyi kalite, standart risk
        # B  : %0.5 risk — Orta kalite, düşük risk
        # C/D: %0.0 risk — Trade yok
        risk_pct = base_risk
        if quality == "S":
            risk_pct = base_risk * 2.0    # S: 2x risk — en güvenilir setup
        elif quality == "A+":
            risk_pct = base_risk * 1.5    # A+: 1.5x risk
        elif quality == "A":
            risk_pct = base_risk * 1.0    # A: standart risk
        elif quality == "B":
            risk_pct = base_risk * 0.5    # B: yarı risk
        elif quality in ["C", "D"]:
            risk_pct = 0                  # C/D: trade yok
        # Risk üst sınırı: bakiyenin %3'ünü geçemez
        risk_pct = min(risk_pct, 3.0)

        # ── Pozisyon Büyüklüğü ────────────────────────────────────────────────
        risk_amount   = balance * (risk_pct / 100)
        position_size = risk_amount / sl_dist if sl_dist > 0 else 0
        notional      = position_size * entry

        # Kaldıraç önerisi
        leverage = min(max_lev, max(1, int(notional / (balance + 1e-10)))) if balance > 0 else 1

        valid = rr >= self.min_rr and risk_pct > 0

        # ── Risk Skoru ────────────────────────────────────────────────────────
        score = 5.0
        if rr > 2.5:   score += 3.0
        elif rr > 2.0: score += 2.0
        elif rr > 1.8: score += 1.0

        sl_pct = sl_dist / (entry + 1e-10)
        if sl_pct < 0.003: score -= 2.0  # Çok yakın stop (gürültüde tetiklenir)
        if sl_pct > 0.04:  score -= 2.0  # Çok uzak stop (risk/ödül bozulur)

        return {
            "valid":         valid,
            "score":         min(10.0, max(0.0, score)),
            "sl":            round(sl, 6),
            "tp1":           round(tp1, 6),
            "tp2":           round(tp2, 6),
            "tp3":           round(tp3, 6),
            "rr":            round(rr, 2),
            "risk_pct":      round(risk_pct, 2),
            "position_size": round(position_size, 4),
            "notional":      round(notional, 2),
            "leverage":      leverage,
            "max_loss":      round(risk_amount, 2),
            # ── Breakeven Parametreleri ────────────────────────────────────────
            "breakeven_enabled":   BREAKEVEN_ENABLED,
            "breakeven_sl":        round(breakeven_sl, 6),
            "breakeven_trigger_r": BREAKEVEN_TRIGGER_R,
            # ── TP Kapatma Oranları ────────────────────────────────────────────
            "tp1_close_pct":    TP1_CLOSE_PCT,   # %30
            "tp2_close_pct":    TP2_CLOSE_PCT,   # %50
            "runner_close_pct": RUNNER_CLOSE_PCT, # %20
        }
