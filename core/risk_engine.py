"""
core/risk_engine.py – Sinyal risk filtresi.

Trade açma kararını risk parametreleri bazında değerlendirir.
Max open trades, duplicate symbol, RR, leverage gibi kontrolleri yapar.
"""

from __future__ import annotations

import logging
<<<<<<< HEAD
from typing import Any
=======
import pandas as pd
from core.coin_personality import CoinPersonalityEngine
>>>>>>> 0797c70b8640d2006e47a50d5580ffae4606199b

import config
from core.data_layer import SignalData, SignalDecision
from core.accounting import calculate_rr

logger = logging.getLogger("ax.risk_engine")

<<<<<<< HEAD
=======
# ─────────────────────────────────────────────────────────────────────────────
# BAĞIMSIZ RISK GOVERNOR FONKSİYONLARI (class dışı, modül seviyesi)
# ─────────────────────────────────────────────────────────────────────────────

def check_daily_loss_limit(balance: float) -> bool:
    """
    Bugünkü net PnL, günlük max kayıp limitini aştı mı?
    Returns True: trade açılabilir. Returns False: bloke.
    """
    try:
        import sys, os
        sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
        from config import DAILY_MAX_LOSS_PCT
        from database import get_conn
        from datetime import datetime, timezone
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        with get_conn() as conn:
            row = conn.execute(
                "SELECT COALESCE(SUM(net_pnl), 0) FROM trades "
                "WHERE DATE(close_time) = ? AND status = 'closed'", (today,)
            ).fetchone()
        daily_pnl = float(row[0] or 0)
        limit = balance * (DAILY_MAX_LOSS_PCT / 100)
        return daily_pnl > -abs(limit)
    except Exception as e:
        logger.warning(f"check_daily_loss_limit hatası: {e}")
        return True


def check_consecutive_losses() -> bool:
    """
    Son N trade ardışık kayıp mı? Bloke eder.
    Returns True: trade açılabilir. Returns False: bloke.
    """
    try:
        import sys, os
        sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
        from config import MAX_CONSECUTIVE_LOSSES
        from database import get_conn
        with get_conn() as conn:
            rows = conn.execute(
                "SELECT net_pnl FROM trades WHERE status = 'closed' "
                "ORDER BY id DESC LIMIT ?", (MAX_CONSECUTIVE_LOSSES,)
            ).fetchall()
        if len(rows) < MAX_CONSECUTIVE_LOSSES:
            return True
        return not all((r[0] or 0) <= 0 for r in rows)
    except Exception as e:
        logger.warning(f"check_consecutive_losses hatası: {e}")
        return True


def check_coin_cooldown(symbol: str) -> bool:
    """
    Coin cooldown'da mı?
    Returns True: trade açılabilir. Returns False: bloke.
    """
    try:
        import sys, os
        sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
        from database import is_coin_in_cooldown
        return not is_coin_in_cooldown(symbol)
    except Exception as e:
        logger.warning(f"check_coin_cooldown hatası: {e}")
        return True


def check_max_open_trades() -> bool:
    """
    Maksimum açık trade sayısına ulaşıldı mı?
    Returns True: trade açılabilir. Returns False: bloke.
    """
    try:
        import sys, os
        sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
        from config import MAX_OPEN_TRADES
        from database import get_open_trades
        return len(get_open_trades()) < MAX_OPEN_TRADES
    except Exception as e:
        logger.warning(f"check_max_open_trades hatası: {e}")
        return True


def check_correlated_exposure(symbol: str, open_trades: list) -> bool:
    """
    Aynı base asset veya yüksek korelasyonlu coin için max açık
    pozisyon sayısını kontrol eder.
    Returns True: trade açılabilir. Returns False: bloke.
    """
    try:
        import sys, os
        sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
        from config import MAX_CORRELATED_TRADES
        base = symbol.replace("USDT", "").replace("BUSD", "")
        same_base = [t for t in open_trades if base in t.get("symbol", "")]
        return len(same_base) < MAX_CORRELATED_TRADES
    except Exception as e:
        logger.warning(f"check_correlated_exposure hatası: {e}")
        return True


class RiskEngine:
    def __init__(self, client, db_path="trade_engine.db"):
        self.client = client
        self.db_path = db_path
        self.base_risk_pct = 1.0
        self.min_rr = MIN_RR
        self.personality_engine = CoinPersonalityEngine(db_path=db_path)
>>>>>>> 0797c70b8640d2006e47a50d5580ffae4606199b

def evaluate_signal_risk(
    signal: SignalData,
    open_trades: list[dict],
    balance: float,
) -> dict[str, Any]:
    """
    Sinyalin risk durumunu değerlendirir.

<<<<<<< HEAD
    Returns:
        {
            "decision": "ALLOW" | "WATCH" | "VETO" | "SKIPPED_BY_RISK" | "SKIPPED_BY_FILTER",
            "reason": str,
            "confidence": float (0.0 – 1.0)
=======
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

        # Coin profili parametrelerini al (ADAPTIVE)
        try:
            # Önce Coin Personality Engine'den adaptif parametreleri al
            adaptive_params = self.personality_engine.get_adaptive_params(symbol)
            
            from coin_library import get_coin_params
            coin_params = get_coin_params(symbol)
            
            # Kişilik bazlı parametreler ile kütüphane parametrelerini harmanla
            sl_mult = adaptive_params.get("sl_atr_mult", coin_params.get("sl_atr_mult", SL_ATR_MULT))
            base_risk = adaptive_params.get("risk_pct", coin_params.get("risk_pct", self.base_risk_pct))
            max_lev = adaptive_params.get("leverage", coin_params.get("max_leverage", 20))
            
            logger.info(f"[Risk] {symbol} için adaptif parametreler uygulandı: SL={sl_mult}, Risk={base_risk}, Lev={max_lev}")
        except Exception as e:
            logger.warning(f"Adaptif parametre hatası, varsayılanlar kullanılıyor: {e}")
            sl_mult   = SL_ATR_MULT
            base_risk = self.base_risk_pct
            max_lev   = 20

        # ── Volatilite Adaptasyonu ──────────────────────────────────────────
        # Eğer coin çok volatil ise SL'i biraz daha genişlet, sakinse daralt
        try:
            volatility_24h = self.client.futures_ticker(symbol=symbol).get("priceChangePercent", 0)
            vol_factor = max(0.8, min(1.5, abs(float(volatility_24h)) / 10.0))
            sl_mult = sl_mult * vol_factor
            logger.debug(f"[Risk] {symbol} Volatilite Adaptasyonu: Factor={vol_factor:.2f}, New SL Mult={sl_mult:.2f}")
        except:
            pass

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
        estimated_fee = notional_fee = 0.0

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
        estimated_fee = notional * 0.0008
        estimated_slippage = notional * 0.0005
        net_rr = max(0.0, rr - ((estimated_fee + estimated_slippage) / (risk_amount + 1e-10)))

        # Kaldıraç önerisi
        leverage = min(max_lev, max(1, int(notional / (balance + 1e-10)))) if balance > 0 else 1

        # Tasfiye mesafesi (yaklaşık — USDT lineer izole, güvenlik marjlı kaba formül)
        lev_use = max(1, min(int(leverage), int(max_lev)))
        liquidation_distance_percent = round(100.0 / lev_use * 0.92, 4)

        risk_reject_reason = ""
        valid = rr >= self.min_rr and risk_pct > 0
        if rr < self.min_rr:
            risk_reject_reason = "bad_rr"
        elif risk_pct <= 0:
            risk_reject_reason = "risk_guard_failed"

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
            "atr":           round(float(atr_val), 6),
            "stop_distance_percent": round((sl_dist / (entry + 1e-10)) * 100, 4),
            "estimated_fee": round(estimated_fee, 4),
            "estimated_slippage": round(estimated_slippage, 4),
            "net_rr":        round(net_rr, 3),
            "risk_amount":   round(risk_amount, 2),
            "risk_reject_reason": risk_reject_reason,
            "liquidation_distance_percent": liquidation_distance_percent,
            # ── Breakeven Parametreleri ────────────────────────────────────────
            "breakeven_enabled":   BREAKEVEN_ENABLED,
            "breakeven_sl":        round(breakeven_sl, 6),
            "breakeven_trigger_r": BREAKEVEN_TRIGGER_R,
            # ── TP Kapatma Oranları ────────────────────────────────────────────
            "tp1_close_pct":    TP1_CLOSE_PCT,   # %30
            "tp2_close_pct":    TP2_CLOSE_PCT,   # %50
            "runner_close_pct": RUNNER_CLOSE_PCT, # %20
>>>>>>> 0797c70b8640d2006e47a50d5580ffae4606199b
        }
    """
    # 1. Max open trades kontrolü
    if len(open_trades) >= config.MAX_OPEN_TRADES:
        return {
            "decision": SignalDecision.SKIPPED_BY_RISK.value,
            "reason": f"Max açık trade ({config.MAX_OPEN_TRADES}) aşıldı",
            "confidence": 1.0,
        }

    # 2. Duplicate symbol kontrolü
    open_symbols = {t.get("symbol", "") for t in open_trades}
    if signal.symbol in open_symbols:
        return {
            "decision": SignalDecision.SKIPPED_BY_RISK.value,
            "reason": f"{signal.symbol} zaten açık",
            "confidence": 1.0,
        }

    # 3. Entry price kontrolü
    if signal.entry_price <= 0:
        return {
            "decision": SignalDecision.VETO.value,
            "reason": "Entry price sıfır veya negatif",
            "confidence": 1.0,
        }

    # 4. Stop loss kontrolü
    if signal.stop_loss <= 0:
        return {
            "decision": SignalDecision.VETO.value,
            "reason": "Stop loss sıfır veya negatif",
            "confidence": 1.0,
        }

    # 5. Stop distance kontrolü
    stop_distance = abs(signal.entry_price - signal.stop_loss)
    if stop_distance <= 0:
        return {
            "decision": SignalDecision.VETO.value,
            "reason": "Stop distance sıfır",
            "confidence": 1.0,
        }

    # 6. TP kontrolü
    tp1 = signal.tp1 or 0
    if tp1 <= 0:
        return {
            "decision": SignalDecision.SKIPPED_BY_FILTER.value,
            "reason": "TP1 tanımlı değil",
            "confidence": 0.8,
        }

    # 7. RR kontrolü – minimum 1.5
    rr = calculate_rr(signal.entry_price, signal.stop_loss, tp1)
    if rr < 1.5:
        return {
            "decision": SignalDecision.SKIPPED_BY_RISK.value,
            "reason": f"RR ({rr}) minimum 1.5 altında",
            "confidence": 0.9,
        }

    # 8. Risk pct limit kontrolü
    if signal.risk_pct > config.RISK_PCT * 3:
        return {
            "decision": SignalDecision.VETO.value,
            "reason": f"Risk% ({signal.risk_pct}) çok yüksek",
            "confidence": 0.95,
        }

    # 9. Leverage limit kontrolü
    if signal.leverage > config.MAX_LEVERAGE:
        return {
            "decision": SignalDecision.SKIPPED_BY_RISK.value,
            "reason": f"Leverage ({signal.leverage}) > max ({config.MAX_LEVERAGE})",
            "confidence": 0.9,
        }

    # Tüm kontroller geçti
    confidence = min(0.6 + rr * 0.1, 0.95)
    return {
        "decision": SignalDecision.ALLOW.value,
        "reason": f"Risk kontrolleri OK (RR={rr})",
        "confidence": round(confidence, 2),
    }


def should_open_trade(
    signal: SignalData,
    open_trades: list[dict],
    balance: float,
) -> tuple[bool, str, str]:
    """
    Trade açılıp açılamayacağını kontrol eder.

    Returns:
        (can_open: bool, decision: str, reason: str)
    """
    result = evaluate_signal_risk(signal, open_trades, balance)
    decision = result["decision"]
    reason = result["reason"]
    can_open = decision == SignalDecision.ALLOW.value

    logger.info(
        "Risk değerlendirmesi: %s %s → %s – %s",
        signal.symbol, signal.side, decision, reason,
    )

    return can_open, decision, reason
