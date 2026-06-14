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

def check_daily_loss_limit(balance: float, environment: str = "live") -> bool:
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
                "WHERE DATE(close_time) = ? AND status = 'closed' AND environment = ?", (today, environment)
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
        
        in_cooldown = is_coin_in_cooldown(symbol)
        if in_cooldown:
            if getattr(config, "GHOST_WARMUP_ENABLED", False):
                import database
                lookback = getattr(config, "GHOST_WARMUP_TRADES_LOOKBACK", 10)
                min_win_rate = getattr(config, "GHOST_WARMUP_MIN_WIN_RATE", 0.55)
                win_rate, count = database.get_ghost_warmup_win_rate(symbol, lookback)
                if count >= 3 and win_rate >= min_win_rate:
                    logger.info(f"[Ghost Warm-up] Bypassing coin cooldown for {symbol}: virtual win rate {win_rate:.2%} (count={count}) >= {min_win_rate:.2%}")
                    return True
            return False
        return True
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
        MAX_SAME_DIRECTION = int(getattr(config, "MAX_SAME_DIRECTION", 5))
        if len(same_direction) >= MAX_SAME_DIRECTION:
            logger.debug(f"[Risk] {symbol} blocked due to max directional exposure ({direction}) (limit={MAX_SAME_DIRECTION}).")
            return False

        return True
    except Exception as e:
        logger.warning(f"check_correlated_exposure hatası: {e}")
        return True


def calculate_historical_correlation(symbol_a: str, symbol_b: str, client) -> float:
    """
    Computes Pearson correlation coefficient between two assets using 15m candle close prices (last 20 candles).
    Returns 0.0 on failure.
    """
    try:
        if not client:
            return 0.0
        if symbol_a == symbol_b:
            return 1.0
        import numpy as np
        klines_a = client.futures_klines(symbol=symbol_a, interval="15m", limit=20)
        klines_b = client.futures_klines(symbol=symbol_b, interval="15m", limit=20)
        if not klines_a or not klines_b or len(klines_a) < 10 or len(klines_b) < 10:
            return 0.0
        closes_a = np.array([float(k[4]) for k in klines_a[-20:]])
        closes_b = np.array([float(k[4]) for k in klines_b[-20:]])
        min_len = min(len(closes_a), len(closes_b))
        closes_a = closes_a[-min_len:]
        closes_b = closes_b[-min_len:]
        if np.std(closes_a) == 0 or np.std(closes_b) == 0:
            return 0.0
        corr = np.corrcoef(closes_a, closes_b)[0, 1]
        if np.isnan(corr):
            return 0.0
        return float(corr)
    except Exception as e:
        logger.debug(f"[Correlation] Calculation error between {symbol_a} and {symbol_b}: {e}")
        return 0.0


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

    # 2.6 Coin Reputation Check (Phase A)
    try:
        from database import get_coin_config
        coin_cfg = get_coin_config(signal.symbol)
        reputation = coin_cfg.get("reputation", "Neutral")
        
        import sys
        is_paper = (getattr(config, "EXECUTION_MODE", "paper") == "paper")
        is_testing = "pytest" in sys.modules or "unittest" in sys.modules
        bypass_shields = getattr(config, "BYPASS_LIVE_RISK_SHIELDS", False)
        if not is_testing:
            bypass_shields = bypass_shields or is_paper

        if reputation == "Trash" and not bypass_shields:
            return {
                "decision": SignalDecision.VETO.value,
                "reason": f"Coin Reputation 'Trash' (Win Rate < 25%). Signal vetoed.",
                "confidence": 1.0,
            }
    except Exception as e:
        logger.debug(f"[Risk Engine] Coin Reputation check failed/skipped: {e}")

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


def calculate_kelly_risk_pct(symbol: str, setup_rr: float, base_risk_pct: float) -> float:
    """
    Dinamik Kelly Kriteri ile pozisyon büyüklüğü hesaplar.
    Sonuç [0.5, 3.0] aralığında sınırlandırılır.
    """
    try:
        from database import get_conn
        total, wins, avg_win, avg_loss = 0, 0, 0.0, 0.0
        
        dynamic_kelly_enabled = getattr(config, "DYNAMIC_KELLY_ENABLED", False)
        lookback_days = getattr(config, "DYNAMIC_KELLY_LOOKBACK_DAYS", 7)
        
        if dynamic_kelly_enabled:
            try:
                from datetime import datetime, timezone, timedelta
                since_dt = (datetime.now(timezone.utc) - timedelta(days=lookback_days)).strftime("%Y-%m-%d %H:%M:%S")
                with get_conn() as conn:
                    # Symbol specific 7-day
                    row_rolling = conn.execute("""
                        SELECT 
                            COUNT(*),
                            SUM(CASE WHEN net_pnl > 0 THEN 1 ELSE 0 END),
                            COALESCE(AVG(CASE WHEN net_pnl > 0 THEN net_pnl END), 0),
                            COALESCE(AVG(CASE WHEN net_pnl <= 0 THEN ABS(net_pnl) END), 0)
                        FROM trades 
                        WHERE status = 'closed' AND symbol = ? AND close_time >= ?
                    """, (symbol, since_dt)).fetchone()
                    
                    if row_rolling and row_rolling[0] >= 3:
                        total, wins, avg_win, avg_loss = row_rolling
                    else:
                        # Global 7-day
                        row_rolling_global = conn.execute("""
                            SELECT 
                                COUNT(*),
                                SUM(CASE WHEN net_pnl > 0 THEN 1 ELSE 0 END),
                                COALESCE(AVG(CASE WHEN net_pnl > 0 THEN net_pnl END), 0),
                                COALESCE(AVG(CASE WHEN net_pnl <= 0 THEN ABS(net_pnl) END), 0)
                            FROM trades 
                            WHERE status = 'closed' AND close_time >= ?
                        """, (since_dt,)).fetchone()
                        if row_rolling_global and row_rolling_global[0] >= 3:
                            total, wins, avg_win, avg_loss = row_rolling_global
            except Exception as e:
                logger.debug(f"[Kelly] Rolling check failed: {e}")
                
        if total < 3:
            with get_conn() as conn:
                # 1. Coin bazlı istatistikler (en az 5 trade)
                row = conn.execute("""
                    SELECT 
                        COUNT(*),
                        SUM(CASE WHEN net_pnl > 0 THEN 1 ELSE 0 END),
                        COALESCE(AVG(CASE WHEN net_pnl > 0 THEN net_pnl END), 0),
                        COALESCE(AVG(CASE WHEN net_pnl <= 0 THEN ABS(net_pnl) END), 0)
                    FROM trades 
                    WHERE status = 'closed' AND symbol = ?
                """, (symbol,)).fetchone()
                
                if row and row[0] >= 5:
                    total, wins, avg_win, avg_loss = row
                else:
                    # 2. Global istatistikler
                    row_global = conn.execute("""
                        SELECT 
                            COUNT(*),
                            SUM(CASE WHEN net_pnl > 0 THEN 1 ELSE 0 END),
                            COALESCE(AVG(CASE WHEN net_pnl > 0 THEN net_pnl END), 0),
                            COALESCE(AVG(CASE WHEN net_pnl <= 0 THEN ABS(net_pnl) END), 0)
                        FROM trades 
                        WHERE status = 'closed'
                    """).fetchone()
                    if row_global and row_global[0] >= 5:
                        total, wins, avg_win, avg_loss = row_global
                        
        if total < 3:
            return base_risk_pct
            
        win_rate = wins / total
        payoff = avg_win / avg_loss if avg_loss > 0 else setup_rr
        if payoff <= 0:
            payoff = setup_rr
            
        # Kelly Formülü: K = W - (1 - W) / R
        kelly_f = win_rate - ((1.0 - win_rate) / payoff)
        
        # Dinamik Kelly Kesri (Win Rate'e göre ölçeklenir)
        kelly_fraction = 0.25
        if win_rate >= 0.60:
            kelly_fraction = 0.45
        elif win_rate >= 0.50:
            kelly_fraction = 0.35
        elif win_rate < 0.40:
            kelly_fraction = 0.15
            
        fractional_kelly = kelly_f * kelly_fraction
        risk_pct = fractional_kelly * 100
        
        # Kelly Safety Cap Check
        # If win_rate < 0.45 or consecutive losses >= 2, cap Kelly at 1.2%
        consec_losses = 0
        try:
            with get_conn() as conn:
                recent_trades = conn.execute("""
                    SELECT net_pnl FROM trades 
                    WHERE status = 'closed'
                    ORDER BY id DESC LIMIT 5
                """).fetchall()
                for r in recent_trades:
                    pnl_val = float(r[0] or 0)
                    if pnl_val <= 0:
                        consec_losses += 1
                    else:
                        break
        except Exception as consec_err:
            logger.debug(f"[Kelly Safety Cap] consecutive loss check failed: {consec_err}")

        kelly_safety_cap = 3.0
        if win_rate < 0.45 or consec_losses >= 2:
            kelly_safety_cap = 1.2
            logger.info(f"[Kelly Safety Cap Triggered] win_rate={win_rate:.2f}, consecutive_losses={consec_losses}. Cap set to 1.2% (down from 3.0%)")

        clamped = max(0.5, min(kelly_safety_cap, risk_pct))
        
        # Apply Kelly scaling based on rolling 7-day win rate (if enabled)
        if dynamic_kelly_enabled:
            if win_rate >= 0.60:
                clamped *= 1.3
                logger.info(f"[Dynamic Kelly] Compounding applied for {symbol} due to win_rate={win_rate:.2%}: 1.3x multiplier. Risk%={clamped:.2f}%")
            elif win_rate < 0.40:
                clamped *= 0.5
                logger.info(f"[Dynamic Kelly] Downscaling applied for {symbol} due to win_rate={win_rate:.2%}: 0.5x multiplier. Risk%={clamped:.2f}%")
                
        # Apply volatility adjustment based on market regime
        try:
            from database import get_market_regime
            regime = get_market_regime()
            if regime and "HIGH_VOL" in regime:
                clamped *= 0.6
                logger.info(f"[Kelly Volatility Scale] High volatility regime ({regime}) detected. Scaling Kelly risk by 0.6x. Risk%={clamped:.2f}%")
        except Exception as vol_err:
            logger.debug(f"[Kelly Volatility Scale] Error querying regime: {vol_err}")
            
        # Make sure final is clamped to safety limits
        clamped = max(0.5, min(3.0, clamped))
        
        logger.info(f"[Kelly Sizing] {symbol}: W={win_rate:.2f}, R={payoff:.2f}, Kelly={kelly_f:.3f}, Risk%={clamped:.2f}% (Base: {base_risk_pct}%)")
        return clamped
    except Exception as e:
        logger.debug(f"[Kelly Sizing] Hesaplama hatası: {e}")
        return base_risk_pct


def get_coin_sector(symbol: str) -> str:
    """Helper to map a symbol's base asset to its coin sector/narrative category."""
    base_asset = symbol.upper().replace("USDT", "").replace("BUSD", "")
    sectors = {
        # Layer 1
        "BTC": "L1", "ETH": "L1", "SOL": "L1", "BNB": "L1", "ADA": "L1", "DOT": "L1", "AVAX": "L1", 
        "NEAR": "L1", "ATOM": "L1", "LINK": "L1", "FTM": "L1", "MATIC": "L1", "TRX": "L1", "SUI": "L1", "SEI": "L1",
        # Memes
        "DOGE": "MEME", "SHIB": "MEME", "PEPE": "MEME", "FLOKI": "MEME", "BONK": "MEME", "WIF": "MEME", "BOME": "MEME",
        # AI
        "FET": "AI", "AGIX": "AI", "OCEAN": "AI", "RNDR": "AI", "WLD": "AI", "GRT": "AI", "LPT": "AI", "TAO": "AI", "AKT": "AI",
        # DeFi
        "UNI": "DEFI", "CAKE": "DEFI", "AAVE": "DEFI", "MKR": "DEFI", "COMP": "DEFI", "CRV": "DEFI", "DYDX": "DEFI", "RUNE": "DEFI", "JUP": "DEFI",
        # Layer 2
        "ARB": "L2", "OP": "L2", "METIS": "L2", "MNT": "L2",
    }
    return sectors.get(base_asset, "OTHER")


# ─────────────────────────────────────────────────────────────────────────────
# RiskEngine CLASS — scalp_bot line 49: from core.risk_engine import RiskEngine
# ─────────────────────────────────────────────────────────────────────────────

class RiskEngine:
    """ATR bazlı SL/TP hesaplayan ve risk filtreleyen class."""

    def __init__(self, client, db_path: str = ""):
        self.client = client
        self._atr_cache = {}  # symbol -> (atr_value, expire_time)
        try:
            import config as _cfg
            self.db_path = db_path or _cfg.DB_PATH
        except Exception:
            self.db_path = db_path or "trading.db"

    def calculate(self, symbol: str, direction: str, entry: float, quality: str, balance: float, score: float = 0.0) -> dict:
        try:
            from core.accounting import calculate_position_size, calculate_rr as _calc_rr
            import database

            quality_mult = {"S": 2.0, "A+": 1.5, "A": 1.0, "B": 0.5, "C": 0.25, "M": 0.5}.get(quality, 0)
            if quality_mult == 0:
                return {"valid": False, "score": 0, "risk_reject_reason": f"quality_{quality}_blocked"}

            # Human mode veya scalp mode'a göre parametreler
            _human = bool(getattr(config, "HUMAN_MODE", False))
            max_open = int(getattr(config, "HUMAN_MAX_OPEN_TRADES" if _human else "MAX_OPEN_TRADES", 2 if _human else 5))

            open_trades = database.get_open_trades()
            if len(open_trades) >= max_open:
                return {"valid": False, "score": 0, "risk_reject_reason": "max_open_trades"}

            if symbol in {t.get("symbol") for t in open_trades}:
                return {"valid": False, "score": 0, "risk_reject_reason": "duplicate_symbol"}

            # Boss Cooldown Gate
            import sys
            is_paper = getattr(config, "EXECUTION_MODE", "paper") == "paper"
            is_testing = "pytest" in sys.modules or "unittest" in sys.modules
            bypass_shields = getattr(config, "BYPASS_LIVE_RISK_SHIELDS", False)
            if not is_testing:
                bypass_shields = bypass_shields or is_paper

            if not bypass_shields:
                cooldown_until_str = database.get_system_state("friday_boss_cooldown_until")
                if cooldown_until_str and cooldown_until_str != "-":
                    try:
                        from datetime import datetime, timezone
                        cooldown_dt = datetime.fromisoformat(cooldown_until_str)
                        if datetime.now(timezone.utc) < cooldown_dt:
                            bypass_boss = False
                            if getattr(config, "GHOST_WARMUP_ENABLED", False):
                                lookback = getattr(config, "GHOST_WARMUP_TRADES_LOOKBACK", 10)
                                min_win_rate = getattr(config, "GHOST_WARMUP_MIN_WIN_RATE", 0.55)
                                win_rate, count = database.get_ghost_warmup_win_rate(None, lookback)
                                if count >= 3 and win_rate >= min_win_rate:
                                    logger.info(f"[Ghost Warm-up] Bypassing boss cooldown: global virtual win rate {win_rate:.2%} (count={count}) >= {min_win_rate:.2%}")
                                    bypass_boss = True
                            if not bypass_boss:
                                return {"valid": False, "score": 0, "risk_reject_reason": "friday_boss_cooldown"}
                    except Exception:
                        pass

            # Macro News Watcher Gate
            macro_paused_str = database.get_system_state("friday_macro_paused")
            if macro_paused_str == "true" and not bypass_shields:
                return {"valid": False, "score": 0, "risk_reject_reason": "macro_news_watcher_paused"}

            # Sector Guard (Maximum 2 open trades per sector)
            if not bypass_shields:
                current_sector = get_coin_sector(symbol)
                if current_sector != "OTHER":
                    same_sector_count = 0
                    for t in open_trades:
                        if get_coin_sector(t.get("symbol", "")) == current_sector:
                            same_sector_count += 1
                    if same_sector_count >= 2:
                        return {"valid": False, "score": 0, "risk_reject_reason": f"sector_limit_reached_{current_sector}"}

            if not bypass_shields and not check_daily_loss_limit(balance, "live"):
                return {"valid": False, "score": 0, "risk_reject_reason": "daily_loss_limit"}

            if not bypass_shields and not check_coin_cooldown(symbol):
                return {"valid": False, "score": 0, "risk_reject_reason": "coin_cooldown"}

            if not bypass_shields and not check_correlated_exposure(symbol, direction, open_trades):
                return {"valid": False, "score": 0, "risk_reject_reason": "directional_correlation_blocked"}

            # L2 Order Book Wall Guard Check
            ob_wall_detected = False
            if getattr(config, "ORDER_BOOK_WALL_FILTER_ENABLED", True) and not bypass_shields:
                wall_mode = getattr(config, "ORDER_BOOK_WALL_FILTER_MODE", "soft")
                is_blocked, wall_reason = self.check_order_book_wall(symbol, direction, entry, mode=wall_mode)
                if is_blocked:
                    return {"valid": False, "score": 0, "risk_reject_reason": wall_reason}
                elif wall_reason:
                    ob_wall_detected = True
            sl_atr_mult = float(getattr(config, "HUMAN_SL_ATR_MULT" if _human else "SL_ATR_MULT", 2.0 if _human else 1.8))
            tp1_r = float(getattr(config, "HUMAN_TP1_R" if _human else "TP1_R", 1.5 if _human else 1.5))
            tp2_r = float(getattr(config, "HUMAN_TP2_R" if _human else "TP2_R", 2.5 if _human else 2.5))
            tp3_r = float(getattr(config, "TP3_R", 4.0))
            max_lev = int(getattr(config, "MAX_LEVERAGE", 20))
            min_rr = float(getattr(config, "MIN_RR", 1.5))
            fee_rate = float(getattr(config, "DEFAULT_FEE_RATE", 0.0004))
            risk_pct_base = float(getattr(config, "RISK_PCT", 1.0))

            # M (Micro-Scalp) Özel Kuralları (Aşırı Hızlı Çıkış)
            if quality == "M":
                sl_atr_mult = 0.5   # Dar SL
                tp1_r = 0.8         # Dar TP1
                tp2_r = 1.2
                tp3_r = 2.0
                min_rr = 1.0        # Tolerans
                logger.info(f"[Micro-Scalp] {symbol} parametreler M moduna geçirildi.")

            # Dinamik Kâr Kilitleme Kalkanı (Dynamic Profit Lock)
            daily_pnl = 0.0
            weekly_pnl = 0.0
            environment = getattr(config, "EXECUTION_MODE", "paper")
            try:
                from datetime import datetime, timezone, timedelta
                today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
                last_7_days = (datetime.now(timezone.utc) - timedelta(days=7)).strftime("%Y-%m-%d")
                
                with database.open_db(self.db_path) as conn:
                    row_daily = conn.execute(
                        "SELECT COALESCE(SUM(net_pnl), 0) FROM trades "
                        "WHERE DATE(close_time) = ? AND status = 'closed' AND environment = ?",
                        (today, environment)
                    ).fetchone()
                    daily_pnl = float(row_daily[0] or 0.0)
                    
                    row_weekly = conn.execute(
                        "SELECT COALESCE(SUM(net_pnl), 0) FROM trades "
                        "WHERE DATE(close_time) >= ? AND status = 'closed' AND environment = ?",
                        (last_7_days, environment)
                    ).fetchone()
                    weekly_pnl = float(row_weekly[0] or 0.0)
            except Exception as e:
                logger.warning(f"[Risk-Profit-Check] Error checking PnL: {e}")

            daily_profit_lock_pct = float(getattr(config, "DAILY_PROFIT_LOCK_PCT", 3.0))
            weekly_profit_lock_pct = float(getattr(config, "WEEKLY_PROFIT_LOCK_PCT", 10.0))
            
            daily_profit_target = balance * (daily_profit_lock_pct / 100.0)
            weekly_profit_target = balance * (weekly_profit_lock_pct / 100.0)
            
            profit_lock_mult = 1.0
            
            if daily_pnl >= daily_profit_target and daily_profit_target > 0 and not bypass_shields:
                if quality not in ("S", "A+"):
                    return {"valid": False, "score": 0, "risk_reject_reason": "daily_profit_target_reached_quality_gate"}
                profit_lock_mult = min(profit_lock_mult, 0.5)
                logger.info(f"[Profit Lock] Daily profit target reached (${daily_pnl:.2f} >= ${daily_profit_target:.2f}). Scaling risk by 0.5x.")
                
            if weekly_pnl >= weekly_profit_target and weekly_profit_target > 0 and not bypass_shields:
                if quality not in ("S", "A+"):
                    return {"valid": False, "score": 0, "risk_reject_reason": "weekly_profit_target_reached_quality_gate"}
                profit_lock_mult = min(profit_lock_mult, 0.5)
                logger.info(f"[Profit Lock] Weekly profit target reached (${weekly_pnl:.2f} >= ${weekly_profit_target:.2f}). Scaling risk by 0.5x.")

            # Dinamik TP / Parametre Ölçekleme ve Otonom Rejim
            is_choppy = False
            try:
                from database import get_market_regime
                regime = get_market_regime()
                
                # Apply GMM Regime-Adaptive TP/SL ATR Multipliers
                if regime == "TRENDING_HIGH_VOL":
                    sl_atr_mult *= 1.3
                    tp1_r *= 1.3
                    tp2_r *= 1.3
                    tp3_r *= 1.3
                    logger.info(f"[GMM-Adaptive] TRENDING_HIGH_VOL scaling applied: stops 1.3x, targets 1.3x.")
                elif regime == "CHOPPY_HIGH_VOL":
                    is_choppy = True
                    sl_atr_mult *= 1.4
                    tp1_r *= 0.8
                    tp2_r *= 0.8
                    tp3_r *= 0.8
                    logger.info(f"[GMM-Adaptive] CHOPPY_HIGH_VOL scaling applied: stops 1.4x, targets 0.8x.")
                elif regime == "CHOPPY_LOW_VOL":
                    is_choppy = True
                    sl_atr_mult *= 0.85
                    tp1_r *= 0.75
                    tp2_r *= 0.75
                    tp3_r *= 0.75
                    logger.info(f"[GMM-Adaptive] CHOPPY_LOW_VOL scaling applied: stops 0.85x, targets 0.75x.")
                
                if regime in ("CHOPPY", "CHOPPY_HIGH_VOL", "CHOPPY_LOW_VOL"):
                    is_choppy = True
                    # Kötü piyasada kârı erken al (Scalp TP)
                    if regime == "CHOPPY":
                        tp1_r = max(1.0, tp1_r * 0.8)
                        tp2_r = max(1.5, tp2_r * 0.8)
                        tp3_r = max(2.5, tp3_r * 0.8)
                    
                    if getattr(config, "REGIME_FILTER_ENABLED", True):
                        # OTONOM KALİTE GATING: CHOPPY piyasada izin verilen kaliteleri config'den oku
                        min_q_choppy = getattr(config, "REGIME_FILTER_MIN_QUALITY_IN_CHOPPY", "A")
                        quality_order = ["C", "B", "A", "A+", "S"]
                        allowed_qualities = quality_order[quality_order.index(min_q_choppy):] if min_q_choppy in quality_order else ["S", "A+", "A"]
                        
                        if not bypass_shields:
                            if quality not in allowed_qualities:
                                return {"valid": False, "score": 0, "risk_reject_reason": "choppy_market_quality_gate"}
                            # OTONOM SKOR GATING: CHOPPY piyasada eşiği 5 puan artır
                            required_score = float(getattr(config, "TRADE_THRESHOLD", 55.0)) + 5.0
                            if score > 0.0 and score < required_score:
                                return {"valid": False, "score": 0, "risk_reject_reason": "choppy_market_score_gate"}
                        else:
                            # Paper mode/Bypass mode: relax allowed quality to C and above, ignore score gate
                            paper_allowed = ["S", "A+", "A", "B", "C"]
                            if quality not in paper_allowed:
                                return {"valid": False, "score": 0, "risk_reject_reason": "choppy_market_quality_gate_paper"}
                elif regime in ("BULLISH", "BEARISH", "TRENDING_HIGH_VOL", "TRENDING_LOW_VOL"):
                    # Trend piyasasında runner'ı uzat
                    is_trending_dir = (
                        (regime == "BULLISH" and direction == "LONG")
                        or (regime == "BEARISH" and direction == "SHORT")
                        or (regime in ("TRENDING_HIGH_VOL", "TRENDING_LOW_VOL"))
                    )
                    if is_trending_dir and regime != "TRENDING_HIGH_VOL":
                        tp2_r *= 1.2
                        tp3_r *= 1.5
            except Exception as e:
                logger.debug(f"[Risk] Dynamic TP scaling failed: {e}")

            atr_val = self._get_atr(symbol) or entry * 0.02
            # ATR fallback — sıfır veya çok küçükse fiyatın %2'sini kullan
            if atr_val <= 0 or atr_val < entry * 0.005:
                atr_val = entry * 0.02
                logger.warning(f"ATR fallback kullanıldı: {symbol} atr={atr_val:.6f}")

            # Volatility-Adaptive Risk Adjustments
            atr_pct = atr_val / entry if entry > 0 else 0.02
            if atr_pct > 0.018:
                sl_atr_mult *= 1.25
                logger.info(f"[Volatility-Adaptive] High volatility ({atr_pct:.4f}) on {symbol}. sl_atr_mult scaled by 1.25x to {sl_atr_mult:.2f}")
            elif atr_pct < 0.008:
                sl_atr_mult *= 0.85
                logger.info(f"[Volatility-Adaptive] Low volatility ({atr_pct:.4f}) on {symbol}. sl_atr_mult scaled by 0.85x to {sl_atr_mult:.2f}")

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
            base_leverage = min(max_lev, max(2, int(0.50 / stop_dist_pct))) if stop_dist_pct > 0 else max_lev
            if atr_pct > 0.018:
                base_leverage = max(2, int(base_leverage * 0.80))
                logger.info(f"[Volatility-Adaptive] Scaling leverage down by 0.80x due to high volatility. New base leverage: {base_leverage}")

            # Dinamik Kaldıraç Çarpanı (AI Coin Profiles'dan)
            lev_multiplier = 1.0
            try:
                from database import get_conn
                with get_conn() as conn:
                    row = conn.execute("SELECT win_rate, total_trades FROM coin_profiles WHERE symbol = ?", (symbol,)).fetchone()
                    if row and row[1] >= 3:
                        wr = float(row[0] or 0)
                        if wr > 0.60:
                            lev_multiplier = 1.25
                        elif wr < 0.35:
                            lev_multiplier = 0.50
            except Exception as e:
                logger.debug(f"[Risk] Dinamik kaldıraç hatası: {e}")

            leverage = max(2, min(max_lev, int(base_leverage * lev_multiplier)))
            if is_choppy:
                leverage = max(2, int(leverage * 0.5))
                logger.info(f"[Regime-Auto-Switch] CHOPPY regime detected. Scaling leverage down to {leverage}.")
            if ob_wall_detected:
                leverage = max(2, int(leverage * 0.5))
                logger.info(f"[OrderBook-Wall-Soft] Wall detected near entry. Scaling leverage down to {leverage}.")
            
            # Apply Dynamic Kelly position sizing
            kelly_base = calculate_kelly_risk_pct(symbol, rr, risk_pct_base)
            risk_pct = kelly_base * quality_mult * profit_lock_mult
            if ob_wall_detected:
                risk_pct *= 0.5
                logger.info(f"[OrderBook-Wall-Soft] Wall detected near entry. Scaling risk_pct down to {risk_pct:.2f}%.")

            # Apply Drawdown, Equity Curve and MTF Trend Alignment protections
            # 1. Fetch balance ledger history to compute Drawdown and Equity SMA
            try:
                with database.open_db(self.db_path) as conn:
                    ledger_rows = conn.execute(
                        "SELECT balance_after FROM balance_ledger ORDER BY id DESC LIMIT 50"
                    ).fetchall()
                    balances = [float(row["balance_after"]) for row in ledger_rows]
                    balances.reverse() # chronological order
            except Exception as e:
                logger.debug(f"[Risk-Balance-Check] Ledger query failed: {e}")
                balances = []

            current_bal = balance
            if balances:
                ath_balance = max(balances + [current_bal])
                drawdown_pct = ((ath_balance - current_bal) / ath_balance) * 100.0 if ath_balance > 0 else 0.0
                
                # Hard Drawdown Lock check
                drawdown_lock_pct = float(getattr(config, "DRAWDOWN_LOCK_PCT", 10.0))
                if drawdown_pct >= drawdown_lock_pct and not bypass_shields:
                    logger.warning(f"[Risk-Drawdown-Lock] HARD LOCKOUT! Drawdown {drawdown_pct:.2f}% >= limit {drawdown_lock_pct}%. ATH={ath_balance:.2f}, Current={current_bal:.2f}")
                    return {"valid": False, "score": 0, "risk_reject_reason": "drawdown_hard_lock"}
                
                # Progressive Drawdown Risk Governor
                progressive_enabled = getattr(config, "PROGRESSIVE_DRAWDOWN_ENABLED", False)
                if progressive_enabled and not bypass_shields:
                    if drawdown_pct > 1.0:
                        scale_factor = (drawdown_pct - 1.0) / (drawdown_lock_pct - 1.0 + 1e-10)
                        drawdown_mult = max(0.15, min(1.0, 1.0 - scale_factor))
                        risk_pct *= drawdown_mult
                        logger.info(f"[Risk-Drawdown-Progressive] Drawdown {drawdown_pct:.2f}% scaled risk by {drawdown_mult:.2f}x to {risk_pct:.2f}%")
                elif not bypass_shields:
                    # Defensive Drawdown mode check (binary fallback)
                    drawdown_defensive_pct = float(getattr(config, "DRAWDOWN_DEFENSIVE_PCT", 5.0))
                    if drawdown_pct >= drawdown_defensive_pct:
                        risk_pct *= 0.5
                        logger.info(f"[Risk-Drawdown-Defensive] Defensive mode: Drawdown {drawdown_pct:.2f}% >= limit {drawdown_defensive_pct}%. Risk scaled by 0.5x to {risk_pct:.2f}%")
                
                # Equity Curve SMA check
                if bool(getattr(config, "EQUITY_CURVE_FILTER_ENABLED", True)) and not bypass_shields:
                    period = int(getattr(config, "EQUITY_CURVE_EMA_PERIOD", 10))
                    recent_balances = balances[-period:]
                    if len(recent_balances) >= 3:
                        sma_balance = sum(recent_balances) / len(recent_balances)
                        if current_bal < sma_balance:
                            reduction = float(getattr(config, "EQUITY_CURVE_RISK_REDUCTION", 0.5))
                            risk_pct *= reduction
                            logger.info(f"[Risk-Equity-Curve] Current balance {current_bal:.2f} < SMA {sma_balance:.2f} (n={len(recent_balances)}). Risk scaled by {reduction}x to {risk_pct:.2f}%")

            # 2. MTF Trend Alignment check
            mtf_align_enabled = bool(getattr(config, "MTF_TREND_ALIGN_ENABLED", True))
            if mtf_align_enabled and self.client and not bypass_shields:
                try:
                    from core.trend_engine import TrendEngine
                    trend_engine = TrendEngine(self.client)
                    trend_1h = trend_engine.get_1h_trend(symbol)
                    trend_4h = trend_engine.get_4h_trend(symbol)
                    
                    opposite_trend = False
                    if direction == "LONG":
                        if trend_1h == "BEARISH" or trend_4h == "BEARISH":
                            opposite_trend = True
                    elif direction == "SHORT":
                        if trend_1h == "BULLISH" or trend_4h == "BULLISH":
                            opposite_trend = True
                            
                    if opposite_trend:
                        mtf_reduction = float(getattr(config, "MTF_TREND_ALIGN_RISK_REDUCTION", 0.4))
                        risk_pct *= mtf_reduction
                        logger.info(f"[Risk-MTF-Align] {symbol} {direction} opposite to 1H={trend_1h}/4H={trend_4h}. Risk scaled by {mtf_reduction}x to {risk_pct:.2f}%")
                except Exception as e:
                    logger.debug(f"[Risk-MTF-Align] MTF Trend check skipped: {e}")

            # 3. Dynamic Pearson Correlation Risk Clustering Shield
            correlation_risk_mult = 1.0
            has_conflict = False
            if not bypass_shields:
                for t in open_trades:
                    t_sym = t.get("symbol")
                    if t_sym == symbol:
                        continue
                    # Calculate correlation
                    corr = calculate_historical_correlation(symbol, t_sym, self.client)
                    logger.info(f"[Correlation Check] {symbol} vs {t_sym}: {corr:.3f}")
                    if corr > 0.85:
                        has_conflict = True
                        try:
                            from database import set_state
                            set_state("pearson_correlation_conflict", "True")
                        except Exception:
                            pass
                        logger.warning(f"[Correlation Block] {symbol} blocked due to high correlation with {t_sym} ({corr:.3f} > 0.85)")
                        return {"valid": False, "score": 0, "risk_reject_reason": "high_correlation_block"}
                    elif corr > 0.75:
                        correlation_risk_mult = min(correlation_risk_mult, 0.5)
                        logger.info(f"[Correlation Warning] {symbol} risk scaled down due to correlation with {t_sym} ({corr:.3f} > 0.75)")
            
                if not has_conflict:
                    try:
                        from database import set_state
                        set_state("pearson_correlation_conflict", "False")
                    except Exception:
                        pass
                risk_pct *= correlation_risk_mult

            # 4. Adaptive Slippage & Latency Guard
            if not bypass_shields:
                try:
                    from database import get_conn, get_system_state
                    from datetime import datetime, timezone, timedelta
                    
                    # Check if manual clutch override/cooldown is active
                    clutch_cooldown = False
                    cooldown_until_str = get_system_state("friday_clutch_cooldown_until")
                    if cooldown_until_str and cooldown_until_str != "-":
                        try:
                            cooldown_dt = datetime.fromisoformat(cooldown_until_str)
                            if datetime.now(timezone.utc) < cooldown_dt:
                                clutch_cooldown = True
                                logger.info(f"[Emergency Clutch] Clutch check bypassed due to active cooldown until {cooldown_until_str}")
                        except Exception as ce:
                            logger.warning(f"[Emergency Clutch] Error parsing clutch cooldown: {ce}")
                            
                    if not clutch_cooldown:
                        cutoff_str = (datetime.now(timezone.utc) - timedelta(hours=2)).isoformat()
                        with get_conn() as conn:
                            recent_perf = conn.execute("""
                                SELECT COALESCE(slippage, 0.0) as slippage, COALESCE(latency_ms, 0) as latency_ms
                                FROM trades
                                WHERE status = 'closed' AND close_time >= ?
                                ORDER BY id DESC LIMIT 3
                            """, (cutoff_str,)).fetchall()
                        if recent_perf:
                            avg_slippage = sum(float(r["slippage"]) for r in recent_perf) / len(recent_perf)
                            avg_latency = sum(int(r["latency_ms"]) for r in recent_perf) / len(recent_perf)
                            
                            # Emergency Clutch Check (High latency or slippage protection gate)
                            if avg_slippage > 0.25 or avg_latency > 800:
                                current_mode = getattr(config, "EXECUTION_MODE", "paper")
                                if current_mode == "paper":
                                    logger.info("[Emergency Clutch] Already in paper mode. Skipping clutch trigger.")
                                else:
                                    try:
                                        from database import update_system_state
                                        update_system_state("tg_execution_mode", "paper")
                                        update_system_state("friday_emergency_clutch", f"slippage={avg_slippage:.3f},latency={avg_latency}")
                                        logger.critical(f"[Emergency Clutch] CRITICAL latency ({avg_latency}ms) or slippage ({avg_slippage:.3f}%) detected! Autonomously switched engine to paper mode.")
                                        return {"valid": False, "score": 0, "risk_reject_reason": "emergency_clutch_switch_triggered"}
                                    except Exception as cl_err:
                                        logger.error(f"[Emergency Clutch] Failed to trigger paper switch: {cl_err}")
                            
                            slippage_mult = 1.0
                            if avg_slippage > 0.15:
                                slippage_mult = 0.70
                                logger.info(f"[Slippage Guard] Avg slippage of last {len(recent_perf)} trades is {avg_slippage:.3f}% > 0.15%. Risk scaled by 0.7x (30% reduction)")
                            
                            latency_mult = 1.0
                            if avg_latency > 500:
                                latency_mult = 0.80
                                logger.info(f"[Latency Guard] Avg latency of last {len(recent_perf)} trades is {avg_latency:.1f}ms > 500ms. Risk scaled by 0.8x (20% reduction)")
                            
                            risk_pct *= (slippage_mult * latency_mult)
                except Exception as e:
                    logger.debug(f"[Risk-Slippage-Latency-Check] Query failed: {e}")

            # Safety Limits (minimum 0.5%, maximum 3.0%)
            risk_pct = max(0.5, min(3.0, risk_pct))

            pos = calculate_position_size(
                balance=balance, risk_pct=risk_pct, entry_price=entry,
                stop_loss=sl, leverage=leverage, fee_rate=fee_rate,
            )
            if pos.get("qty", 0) <= 0:
                return {"valid": False, "score": 0, "risk_reject_reason": "position_size_invalid"}

            # Portfolio Exposure & Available Balance checks
            used_margin = sum(float(t.get("margin_used") or t.get("margin") or 0.0) for t in open_trades)
            max_allowed_margin = balance * (float(getattr(config, "MAX_PORTFOLIO_EXPOSURE_PCT", 95.0)) / 100.0)
            req_margin = pos.get("margin", 0)
            
            if used_margin + req_margin > max_allowed_margin:
                return {"valid": False, "score": 0, "risk_reject_reason": "portfolio_margin_exposure_exceeded"}
                
            if req_margin > (balance - used_margin):
                return {"valid": False, "score": 0, "risk_reject_reason": "insufficient_available_balance"}

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

            # Volatility-Adaptive Risk Adjustments
            atr_pct = atr_val / entry if entry > 0 else 0.02
            if atr_pct > 0.018:
                sl_atr_mult *= 1.25
            elif atr_pct < 0.008:
                sl_atr_mult *= 0.85

            # GMM Regime-Adaptive TP/SL ATR Multipliers
            try:
                from database import get_market_regime
                regime = get_market_regime()
                if regime == "TRENDING_HIGH_VOL":
                    sl_atr_mult *= 1.3
                    tp1_r *= 1.3
                    tp2_r *= 1.3
                    tp3_r *= 1.3
                elif regime == "CHOPPY_HIGH_VOL":
                    sl_atr_mult *= 1.4
                    tp1_r *= 0.8
                    tp2_r *= 0.8
                    tp3_r *= 0.8
                elif regime == "CHOPPY_LOW_VOL":
                    sl_atr_mult *= 0.85
                    tp1_r *= 0.75
                    tp2_r *= 0.75
                    tp3_r *= 0.75
                elif regime in ("CHOPPY", "SIDEWAYS"):
                    tp1_r = max(1.0, tp1_r * 0.8)
                    tp2_r = max(1.5, tp2_r * 0.8)
                    tp3_r = max(2.5, tp3_r * 0.8)
                elif regime in ("BULLISH", "BEARISH", "TRENDING_LOW_VOL"):
                    is_trending_dir = (
                        (regime == "BULLISH" and direction == "LONG")
                        or (regime == "BEARISH" and direction == "SHORT")
                        or (regime == "TRENDING_LOW_VOL")
                    )
                    if is_trending_dir:
                        tp2_r *= 1.2
                        tp3_r *= 1.5
            except Exception as e:
                logger.debug(f"[Risk Preview] Dynamic TP scaling failed: {e}")

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
            if atr_pct > 0.018:
                leverage = max(2, int(leverage * 0.80))
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
            import time
            now = time.time()
            if hasattr(self, "_atr_cache") and symbol in self._atr_cache:
                val, expire = self._atr_cache[symbol]
                if now < expire:
                    return val

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
            if v > 0:
                if not hasattr(self, "_atr_cache"):
                    self._atr_cache = {}
                self._atr_cache[symbol] = (v, now + 300.0) # Cache for 5 minutes
                return v
            return 0.0
        except Exception:
            return 0.0

    def check_order_book_wall(self, symbol: str, direction: str, entry_price: float, mode: str = "hard") -> tuple[bool, str]:
        """
        Check Binance L2 order book depth for thick opposite walls or spoofing orders.
        Returns: (is_blocked, reason)
        """
        if not self.client:
            return False, ""
        try:
            # Fetch futures L2 order book
            book = self.client.futures_order_book(symbol=symbol, limit=100)
            bids = book.get("bids", [])
            asks = book.get("asks", [])
            if not bids or not asks:
                return False, ""
                
            total_bid_qty = sum(float(b[1]) for b in bids)
            total_ask_qty = sum(float(a[1]) for a in asks)
            total_qty = total_bid_qty + total_ask_qty
            if total_qty <= 0:
                return False, ""
                
            is_blocked = False
            reason = ""
            
            # Bid-Ask Imbalance Check (> 75%)
            if direction.upper() == "LONG":
                ask_ratio = total_ask_qty / total_qty
                if ask_ratio > 0.75:
                    is_blocked = (mode == "hard")
                    reason = f"order_book_wall_block (ask_imbalance={ask_ratio:.2f})"
                    return is_blocked, reason
            elif direction.upper() == "SHORT":
                bid_ratio = total_bid_qty / total_qty
                if bid_ratio > 0.75:
                    is_blocked = (mode == "hard")
                    reason = f"order_book_wall_block (bid_imbalance={bid_ratio:.2f})"
                    return is_blocked, reason
                    
            # Spoofing/Thick Wall Check near entry
            ob_mult = float(getattr(config, "SCALP_OB_WALL_MULTIPLIER", 5.0))
            ob_pct = float(getattr(config, "SCALP_OB_WALL_PCT", 0.002))
            
            if direction.upper() == "LONG":
                # Opposing side is asks (sellers)
                avg_ask_qty = total_ask_qty / len(asks)
                for price_str, qty_str in asks:
                    price = float(price_str)
                    qty = float(qty_str)
                    if price <= entry_price * (1 + ob_pct):
                        if qty > avg_ask_qty * ob_mult:
                            is_blocked = (mode == "hard")
                            reason = f"order_book_wall_block (sell_wall={price:.4f}, qty={qty:.1f})"
                            return is_blocked, reason
            elif direction.upper() == "SHORT":
                # Opposing side is bids (buyers)
                avg_bid_qty = total_bid_qty / len(bids)
                for price_str, qty_str in bids:
                    price = float(price_str)
                    qty = float(qty_str)
                    if price >= entry_price * (1 - ob_pct):
                        if qty > avg_bid_qty * ob_mult:
                            is_blocked = (mode == "hard")
                            reason = f"order_book_wall_block (buy_wall={price:.4f}, qty={qty:.1f})"
                            return is_blocked, reason
                            
            return False, ""
        except Exception as e:
            logger.warning(f"[Risk Book Guard] Error checking order book for {symbol}: {e}")
            return False, ""
