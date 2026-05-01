"""
Risk Engine
Stop, TP, RR ve pozisyon büyüklüğünü hesaplar.
"""
import logging
import pandas as pd

logger = logging.getLogger(__name__)

class RiskEngine:
    def __init__(self, client):
        self.client = client
        self.base_risk_pct = 1.0
        self.min_rr = 1.5

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

        # Stop mesafesi (ATR bazlı)
        sl_dist = atr_val * 1.5
        
        if direction == "LONG":
            sl = entry - sl_dist
            tp1 = entry + sl_dist * 1.0
            tp2 = entry + sl_dist * 2.0
            tp3 = entry + sl_dist * 3.0
        else:
            sl = entry + sl_dist
            tp1 = entry - sl_dist * 1.0
            tp2 = entry - sl_dist * 2.0
            tp3 = entry - sl_dist * 3.0

        rr = abs(tp2 - entry) / sl_dist

        # Risk ayarlaması
        risk_pct = self.base_risk_pct
        if quality == "A+": risk_pct *= 1.2
        elif quality == "B": risk_pct *= 0.5
        elif quality in ["C", "D"]: risk_pct = 0

        # Pozisyon büyüklüğü
        risk_amount = balance * (risk_pct / 100)
        position_size = risk_amount / sl_dist if sl_dist > 0 else 0
        notional = position_size * entry
        
        # Kaldıraç önerisi (max 20x)
        leverage = min(20, max(1, int(notional / balance))) if balance > 0 else 1

        valid = rr >= self.min_rr and risk_pct > 0

        score = 5.0
        if rr > 2.5: score += 3.0
        elif rr > 2.0: score += 2.0
        
        if sl_dist / entry < 0.005: score -= 2.0 # Çok yakın stop
        if sl_dist / entry > 0.05: score -= 2.0  # Çok uzak stop

        return {
            "valid": valid,
            "score": min(10.0, max(0.0, score)),
            "sl": round(sl, 6),
            "tp1": round(tp1, 6),
            "tp2": round(tp2, 6),
            "tp3": round(tp3, 6),
            "rr": round(rr, 2),
            "risk_pct": round(risk_pct, 2),
            "position_size": round(position_size, 4),
            "notional": round(notional, 2),
            "leverage": leverage,
            "max_loss": round(risk_amount, 2)
        }
