"""
core/risk_engine.py – Sinyal risk filtresi.

Trade açma kararını risk parametreleri bazında değerlendirir.
Max open trades, duplicate symbol, RR, leverage gibi kontrolleri yapar.
"""

from __future__ import annotations

import logging
from typing import Any

import config
from core.data_layer import SignalData, SignalDecision
from core.accounting import calculate_rr

logger = logging.getLogger("ax.risk_engine")

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


def check_spread(ticker: dict, max_spread_pct: float = 0.08) -> bool:
    """
    Bid-ask spread kontrolü. %0.08 üstü spread → kâr eridi → skip.
    Returns True if acceptable.
    """
    try:
        bid = float(ticker.get("bidPrice", 0))
        ask = float(ticker.get("askPrice", 0))
        mid = (bid + ask) / 2
        if mid <= 0:
            return True
        spread_pct = (ask - bid) / mid * 100
        return spread_pct <= max_spread_pct
    except Exception:
        return True


def check_correlated_exposure(symbol: str, direction: str, open_trades: list) -> bool:
    """
    Aynı base asset veya yüksek korelasyonlu yön (Directional Correlation) için
    max açık pozisyon sayısını kontrol eder.
    Returns True: trade açılabilir. Returns False: bloke.
    """
    try:
        import sys, os
        sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
        from config import MAX_CORRELATED_TRADES
        
        # 1. Base Asset Check
        base = symbol.replace("USDT", "").replace("BUSD", "")
        same_base = [t for t in open_trades if base in t.get("symbol", "")]
        if len(same_base) >= MAX_CORRELATED_TRADES:
            logger.debug(f"[Risk] {symbol} blocked due to base asset correlation.")
            return False
            
        # 2. Directional Correlation Block (Overtrading Control)
        # Don't take too many trades in the same direction if we already have exposure
        same_direction = [t for t in open_trades if t.get("direction", t.get("side", "")) == direction]
        MAX_SAME_DIRECTION = 3 # Hard limit for now, could be moved to config
        if len(same_direction) >= MAX_SAME_DIRECTION:
            logger.debug(f"[Risk] {symbol} blocked due to max directional exposure ({direction}).")
            return False

        return True
    except Exception as e:
        logger.warning(f"check_correlated_exposure hatası: {e}")
        return True


def evaluate_signal_risk(
    signal: SignalData,
    open_trades: list[dict],
    balance: float,
) -> dict[str, Any]:
    """
    Sinyalin risk durumunu değerlendirir.

    Returns:
        {
            "decision": "ALLOW" | "WATCH" | "VETO" | "SKIPPED_BY_RISK" | "SKIPPED_BY_FILTER",
            "reason": str,
            "confidence": float (0.0 – 1.0)
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

    # 2.5 Correlation Block Kontrolü
    if not check_correlated_exposure(signal.symbol, signal.direction, open_trades):
        return {
            "decision": SignalDecision.SKIPPED_BY_RISK.value,
            "reason": f"Correlation Block / Overtrading Limits Aşıldı",
            "confidence": 0.9,
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


# ─────────────────────────────────────────────────────────────────────────────
# RiskEngine CLASS — scalp_bot line 49: from core.risk_engine import RiskEngine
# ─────────────────────────────────────────────────────────────────────────────

class RiskEngine:
    """ATR bazlı SL/TP hesaplayan ve risk filtreleyen class."""

    def __init__(self, client, db_path: str = "trading.db"):
        self.client = client
        self.db_path = db_path

    def calculate(self, symbol: str, direction: str, entry: float, quality: str, balance: float) -> dict:
        try:
            from core.accounting import calculate_position_size, calculate_rr as _calc_rr
            import database

            quality_mult = {"S": 2.0, "A+": 1.5, "A": 1.0, "B": 0.5}.get(quality, 0)
            if quality_mult == 0:
                return {"valid": False, "score": 0, "risk_reject_reason": f"quality_{quality}_blocked"}

            open_trades = database.get_open_trades()
            if len(open_trades) >= int(getattr(config, "MAX_OPEN_TRADES", 5)):
                return {"valid": False, "score": 0, "risk_reject_reason": "max_open_trades"}

            if symbol in {t.get("symbol") for t in open_trades}:
                return {"valid": False, "score": 0, "risk_reject_reason": "duplicate_symbol"}

            if not check_daily_loss_limit(balance):
                return {"valid": False, "score": 0, "risk_reject_reason": "daily_loss_limit"}

            if not check_coin_cooldown(symbol):
                return {"valid": False, "score": 0, "risk_reject_reason": "coin_cooldown"}

            if not check_correlated_exposure(symbol, direction, open_trades):
                return {"valid": False, "score": 0, "risk_reject_reason": "directional_correlation_blocked"}

            # Human mode veya scalp mode'a göre parametreler
            _human = bool(getattr(config, "HUMAN_MODE", False))
            sl_atr_mult = float(getattr(config, "HUMAN_SL_ATR_MULT" if _human else "SL_ATR_MULT", 2.0 if _human else 1.8))
            tp1_r = float(getattr(config, "HUMAN_TP1_R" if _human else "TP1_R", 1.5 if _human else 1.5))
            tp2_r = float(getattr(config, "HUMAN_TP2_R" if _human else "TP2_R", 2.5 if _human else 2.5))
            tp3_r = float(getattr(config, "TP3_R", 4.0))
            max_lev = int(getattr(config, "MAX_LEVERAGE", 20))
            min_rr = float(getattr(config, "MIN_RR", 1.5))
            fee_rate = float(getattr(config, "DEFAULT_FEE_RATE", 0.0004))
            risk_pct_base = float(getattr(config, "RISK_PCT", 1.0))

            # Dinamik TP Ölçekleme (Dynamic TP Scaling)
            try:
                from database import get_market_regime
                regime = get_market_regime()
                if regime == "CHOPPY":
                    # Kötü piyasada kârı erken al (Scalp TP)
                    tp1_r = max(1.0, tp1_r * 0.8)
                    tp2_r = max(1.5, tp2_r * 0.8)
                    tp3_r = max(2.5, tp3_r * 0.8)
                elif regime in ("BULLISH", "BEARISH"):
                    # Trend piyasasında runner'ı uzat
                    if (regime == "BULLISH" and direction == "LONG") or (regime == "BEARISH" and direction == "SHORT"):
                        tp2_r *= 1.2
                        tp3_r *= 1.5
            except Exception as e:
                logger.debug(f"[Risk] Dynamic TP scaling failed: {e}")

            atr_val = self._get_atr(symbol) or entry * 0.02
            # ATR fallback — sıfır veya çok küçükse fiyatın %2'sini kullan
            if atr_val <= 0 or atr_val < entry * 0.005:
                atr_val = entry * 0.02
                logger.warning(f"ATR fallback kullanıldı: {symbol} atr={atr_val:.6f}")
            is_long = direction == "LONG"
            sl_dist = atr_val * sl_atr_mult
            sl = (entry - sl_dist) if is_long else (entry + sl_dist)
            tp1 = (entry + sl_dist * tp1_r) if is_long else (entry - sl_dist * tp1_r)
            tp2 = (entry + sl_dist * tp2_r) if is_long else (entry - sl_dist * tp2_r)
            tp3 = (entry + sl_dist * tp3_r) if is_long else (entry - sl_dist * tp3_r)
            # MIN_SL_PCT kontrolü — SL entry'ye çok yakınsa zorla aç
            min_sl_dist = entry * float(getattr(config, "MIN_SL_PCT", 0.015))
            if abs(sl - entry) < min_sl_dist:
                sl  = (entry - min_sl_dist) if is_long else (entry + min_sl_dist)
                tp1 = (entry + min_sl_dist * tp1_r) if is_long else (entry - min_sl_dist * tp1_r)
                tp2 = (entry + min_sl_dist * tp2_r) if is_long else (entry - min_sl_dist * tp2_r)
                tp3 = (entry + min_sl_dist * tp3_r) if is_long else (entry - min_sl_dist * tp3_r)
                logger.warning(f"MIN_SL_PCT override: {symbol} sl_dist={min_sl_dist:.6f}")
            rr = _calc_rr(entry, sl, tp2)

            if rr < min_rr:
                return {"valid": False, "score": 0, "rr": rr, "risk_reject_reason": f"low_rr_{rr:.2f}"}

            # Leverage — config'den oku, ATR volatilitesine göre ölçekle ama min 2
            try:
                from config import MAX_LEVERAGE as _ML
                max_lev = min(int(_ML), 20)
            except Exception:
                max_lev = 10
            stop_dist_pct = abs(entry - sl) / entry if entry > 0 else 0.02
            leverage = min(max_lev, max(2, int(0.50 / stop_dist_pct))) if stop_dist_pct > 0 else max_lev
            risk_pct = risk_pct_base * quality_mult

            pos = calculate_position_size(
                balance=balance, risk_pct=risk_pct, entry_price=entry,
                stop_loss=sl, leverage=leverage, fee_rate=fee_rate,
            )
            if pos.get("qty", 0) <= 0:
                return {"valid": False, "score": 0, "risk_reject_reason": "position_size_invalid"}

            # Fee hesabı: open + close (notional × fee_rate × 2)
            notional_val   = pos.get("notional", 0)
            estimated_fee  = round(notional_val * fee_rate * 2, 6)
            # net_rr: fee düşüldükten sonra gerçek R/R
            risk_usd = pos.get("risk_usd", 0) or 1e-10
            net_rr = round((rr * risk_usd - estimated_fee) / risk_usd, 3)
            return {
                "valid": True, "sl": round(sl, 6), "tp1": round(tp1, 6),
                "tp2": round(tp2, 6), "tp3": round(tp3, 6), "rr": round(rr, 3),
                "risk_pct": round(risk_pct, 3), "position_size": round(pos.get("qty", 0), 6),
                "notional": round(notional_val, 4), "leverage": leverage,
                "max_loss": round(risk_usd, 4),
                "risk_usd": round(risk_usd, 4),
                "score": round(min(10.0, 5.0 + rr * 1.5), 2), "atr": round(atr_val, 6),
                "estimated_fee": estimated_fee,   # BUG FIX: test_risk_engine için eksikti
                "net_rr": net_rr,                 # BUG FIX: fee sonrası gerçek R/R
            }
        except Exception as e:
            logger.error("RiskEngine.calculate: %s", e, exc_info=True)
            return {"valid": False, "score": 0, "risk_reject_reason": f"exception_{type(e).__name__}"}

    def preview_for_paper(self, symbol: str, direction: str, entry: float, balance: float) -> dict:
        try:
            from core.accounting import calculate_position_size, calculate_rr as _calc_rr
            # Human mode veya scalp mode'a göre parametreler
            _human = bool(getattr(config, "HUMAN_MODE", False))
            sl_atr_mult = float(getattr(config, "HUMAN_SL_ATR_MULT" if _human else "SL_ATR_MULT", 2.0 if _human else 1.8))
            tp1_r = float(getattr(config, "HUMAN_TP1_R" if _human else "TP1_R", 1.5 if _human else 1.5))
            tp2_r = float(getattr(config, "HUMAN_TP2_R" if _human else "TP2_R", 2.5 if _human else 2.5))
            tp3_r = float(getattr(config, "TP3_R", 4.0))
            max_lev = int(getattr(config, "MAX_LEVERAGE", 20))
            risk_pct = float(getattr(config, "RISK_PCT", 1.0))
            fee_rate = float(getattr(config, "DEFAULT_FEE_RATE", 0.0004))
            atr_val = self._get_atr(symbol) or entry * 0.02
            # ATR fallback — sıfır veya çok küçükse fiyatın %2'sini kullan
            if atr_val <= 0 or atr_val < entry * 0.005:
                atr_val = entry * 0.02
                logger.warning(f"ATR fallback (preview): {symbol} atr={atr_val:.6f}")
            is_long = direction == "LONG"
            sl_dist = atr_val * sl_atr_mult
            sl = (entry - sl_dist) if is_long else (entry + sl_dist)
            tp1 = (entry + sl_dist * tp1_r) if is_long else (entry - sl_dist * tp1_r)
            tp2 = (entry + sl_dist * tp2_r) if is_long else (entry - sl_dist * tp2_r)
            tp3 = (entry + sl_dist * tp3_r) if is_long else (entry - sl_dist * tp3_r)
            # MIN_SL_PCT kontrolü — SL entry'ye çok yakınsa zorla aç
            min_sl_dist = entry * float(getattr(config, "MIN_SL_PCT", 0.015))
            if abs(sl - entry) < min_sl_dist:
                sl  = (entry - min_sl_dist) if is_long else (entry + min_sl_dist)
                tp1 = (entry + min_sl_dist * tp1_r) if is_long else (entry - min_sl_dist * tp1_r)
                tp2 = (entry + min_sl_dist * tp2_r) if is_long else (entry - min_sl_dist * tp2_r)
                tp3 = (entry + min_sl_dist * tp3_r) if is_long else (entry - min_sl_dist * tp3_r)
            rr = _calc_rr(entry, sl, tp2)
            # Leverage — config'den oku
            try:
                from config import MAX_LEVERAGE as _ML
                max_lev = min(int(_ML), 20)
            except Exception:
                max_lev = 10
            stop_dist_pct = abs(entry - sl) / entry if entry > 0 else 0.02
            leverage = min(max_lev, max(2, int(0.50 / stop_dist_pct))) if stop_dist_pct > 0 else max_lev
            pos = calculate_position_size(
                balance=balance, risk_pct=risk_pct, entry_price=entry,
                stop_loss=sl, leverage=leverage, fee_rate=fee_rate,
            )
            return {
                "valid": True, "sl": round(sl, 6), "tp1": round(tp1, 6),
                "tp2": round(tp2, 6), "tp3": round(tp3, 6), "rr": round(rr, 3),
                "risk_pct": risk_pct, "position_size": round(pos.get("qty", 0), 6),
                "notional": round(pos.get("notional", 0), 4), "leverage": leverage,
                "max_loss": round(pos.get("risk_usd", 0), 4),
                "risk_usd": round(pos.get("risk_usd", 0), 4),
            }
        except Exception as e:
            logger.error("preview_for_paper: %s", e)
            return {"valid": False}

    def _get_atr(self, symbol: str, interval: str = "5m", period: int = 14) -> float:
        try:
            import pandas as pd
            klines = self.client.futures_klines(symbol=symbol, interval=interval, limit=period + 5)
            df = pd.DataFrame(klines, columns=[
                "time", "open", "high", "low", "close", "volume",
                "ct", "qav", "nt", "tbbav", "tbqav", "ignore"
            ])
            for col in ("high", "low", "close"):
                df[col] = df[col].astype(float)
            tr = pd.concat([
                df["high"] - df["low"],
                (df["high"] - df["close"].shift()).abs(),
                (df["low"] - df["close"].shift()).abs(),
            ], axis=1).max(axis=1)
            v = float(tr.rolling(period).mean().iloc[-1])
            return v if v > 0 else 0.0
        except Exception:
            return 0.0
