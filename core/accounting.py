"""
core/accounting.py — AX Accounting Engine v5.0 (Production)
=============================================================
Merkezi PnL / fee / margin / risk hesap motoru.

Tüm finansal hesaplamalar bu dosyadan yapılır.
Backtest Engine ve Execution Engine bu modülü kullanır.
"""

from __future__ import annotations

import logging
import math
from typing import Optional, Tuple

from core.data_layer import SignalData, TradeData, TradeStatus

logger = logging.getLogger("ax.accounting")


# ── Pozisyon büyüklüğü ─────────────────────────────────────────────

def calculate_position_size(
    balance: float,
    risk_pct: float,
    entry_price: float,
    stop_loss: float,
    leverage: int = 1,
    fee_rate: float = 0.0004,
) -> dict:
    """
    Risk bazlı pozisyon büyüklüğü hesaplar.
    Hem basit hem partial-close destekli versiyon.

    Returns dict:
        valid: bool
        qty: float            toplam miktar
        qty_tp1: float        TP1'de kapatılacak (TP1_CLOSE_PCT)
        qty_tp2: float        TP2'de kapatılacak (TP2_CLOSE_PCT)
        qty_runner: float     Runner (RUNNER_CLOSE_PCT)
        risk_usd: float
        notional: float
        margin: float
        open_fee: float
        reason: str
    """
    try:
        from config import TP1_CLOSE_PCT, TP2_CLOSE_PCT, RUNNER_CLOSE_PCT
        tp1_pct = float(TP1_CLOSE_PCT) / 100.0
        tp2_pct = float(TP2_CLOSE_PCT) / 100.0
        runner_pct = float(RUNNER_CLOSE_PCT) / 100.0
    except Exception:
        tp1_pct, tp2_pct, runner_pct = 0.40, 0.30, 0.30

    stop_distance = abs(entry_price - stop_loss)
    if stop_distance <= 0:
        return {
            "valid": False, "reason": "Stop distance sıfır",
            "qty": 0, "qty_tp1": 0, "qty_tp2": 0, "qty_runner": 0,
            "risk_usd": 0, "notional": 0, "margin": 0, "open_fee": 0,
        }

    if entry_price <= 0:
        return {
            "valid": False, "reason": "Entry fiyat geçersiz",
            "qty": 0, "qty_tp1": 0, "qty_tp2": 0, "qty_runner": 0,
            "risk_usd": 0, "notional": 0, "margin": 0, "open_fee": 0,
        }

    risk_usd = balance * (risk_pct / 100.0)
    qty = round(risk_usd / stop_distance, 6)
    notional = round(qty * entry_price, 4)
    margin = round(notional / max(leverage, 1), 4)
    open_fee = round(notional * fee_rate, 6)

    return {
        "valid": True,
        "reason": "OK",
        "qty": qty,
        "qty_tp1": round(qty * tp1_pct, 6),
        "qty_tp2": round(qty * tp2_pct, 6),
        "qty_runner": round(qty * runner_pct, 6),
        "risk_usd": round(risk_usd, 4),
        "notional": notional,
        "margin": margin,
        "open_fee": open_fee,
    }


# ── Notional & margin ──────────────────────────────────────────────

def calculate_notional(quantity: float, entry_price: float) -> float:
    """Pozisyonun toplam değeri = quantity * entry_price."""
    return round(quantity * entry_price, 4)


def calculate_notional_and_margin(
    entry_price: float, quantity: float, leverage: int
) -> Tuple[float, float]:
    """Notional ve margin hesaplar. Tuple (notional, margin) döner."""
    notional = round(quantity * entry_price, 4)
    margin = round(notional / max(leverage, 1), 4)
    return notional, margin


def calculate_margin_used(notional: float, leverage: int) -> float:
    """Kullanılan marjin = notional / leverage."""
    if leverage <= 0:
        leverage = 1
    return round(notional / leverage, 4)


# ── Fee ─────────────────────────────────────────────────────────────

def calculate_fee(notional: float, fee_rate: float = 0.0004) -> float:
    """İşlem ücreti = notional * fee_rate (tek taraf)."""
    return round(notional * fee_rate, 6)


# ── PnL hesaplamaları ──────────────────────────────────────────────

def calculate_unrealized_pnl(
    side: str,
    entry_price: float,
    current_price: float,
    quantity: float,
    fee_rate: float = 0.0,
) -> float:
    """
    Gerçekleşmemiş kar/zarar.
    LONG:  pnl = (current - entry) * qty
    SHORT: pnl = (entry - current) * qty
    Fee açılış+güncel her iki taraf olarak düşülür.
    """
    side_upper = side.upper()
    if side_upper == "LONG":
        raw_pnl = (current_price - entry_price) * quantity
    elif side_upper == "SHORT":
        raw_pnl = (entry_price - current_price) * quantity
    else:
        logger.warning("Bilinmeyen side: %s – LONG kabul ediliyor", side)
        raw_pnl = (current_price - entry_price) * quantity

    total_fee = 0.0
    if fee_rate > 0:
        notional_entry = quantity * entry_price
        notional_current = quantity * current_price
        total_fee = (notional_entry + notional_current) * fee_rate

    return round(raw_pnl - total_fee, 6)


def calculate_realized_pnl(
    side: str,
    entry_price: float,
    exit_price: float,
    quantity: float,
    fee_rate: float = 0.0,
) -> float:
    """
    Gerçekleşmiş kar/zarar.
    LONG:  pnl = (exit - entry) * qty
    SHORT: pnl = (entry - exit) * qty
    Fee her iki taraftan düşülür.
    """
    side_upper = side.upper()
    if side_upper == "LONG":
        raw_pnl = (exit_price - entry_price) * quantity
    elif side_upper == "SHORT":
        raw_pnl = (entry_price - exit_price) * quantity
    else:
        raw_pnl = (exit_price - entry_price) * quantity

    total_fee = 0.0
    if fee_rate > 0:
        notional_entry = quantity * entry_price
        notional_exit = quantity * exit_price
        total_fee = (notional_entry + notional_exit) * fee_rate

    return round(raw_pnl - total_fee, 6)


def calculate_partial_close_pnl(
    side: str,
    entry_price: float,
    exit_price: float,
    quantity: float,
    fee_rate: float = 0.0,
) -> Tuple[float, float]:
    """
    Partial close PnL hesabı.
    Returns: (net_pnl, fee)
    """
    if side.upper() == "LONG":
        raw_pnl = (exit_price - entry_price) * quantity
    else:
        raw_pnl = (entry_price - exit_price) * quantity

    fee = 0.0
    if fee_rate > 0:
        notional_entry = quantity * entry_price
        notional_exit = quantity * exit_price
        fee = (notional_entry + notional_exit) * fee_rate

    return round(raw_pnl - fee, 6), round(fee, 6)


def calculate_pnl(
    side: str,
    entry: float,
    exit_price: float,
    qty: float,
    fee_rate: float = 0.0,
) -> float:
    """Kısa isim alias — backtest uyumu için."""
    return calculate_realized_pnl(side, entry, exit_price, qty, fee_rate)


# ── Risk/Reward ────────────────────────────────────────────────────

def calculate_rr(
    entry_price: float,
    stop_loss: float,
    target_price: float,
) -> float:
    """Risk/Reward oranı hesaplar."""
    stop_distance = abs(entry_price - stop_loss)
    if stop_distance <= 0:
        return 0.0
    reward = abs(target_price - entry_price)
    return round(reward / stop_distance, 2)


def calculate_r_multiple(pnl: float, risk_usd: float) -> float:
    """R multiple = pnl / risk_usd."""
    if risk_usd <= 0:
        return 0.0
    return round(pnl / risk_usd, 3)


# ── Expectancy — Tek Kuzey Yıldızı Metrik (Faz 3.1) ──────────────────

def _trade_r_value(row) -> Optional[float]:
    """Kapanmış bir trade satırının R-multiple değerini döner.

    Önce kayıtlı r_multiple kullanılır; yoksa net_pnl/risk_usd'den hesaplanır.
    Hiçbiri yoksa None (örneklem dışı bırakılır).
    NEDEN: Tek R kaynağı — friday_decisions ile aynı mantık, tutarlı expectancy.
    """
    try:
        r = float(row["r_multiple"] or 0.0)
        if r != 0.0:
            return r
        risk_usd = float(row["risk_usd"] or 0.0)
        if risk_usd > 0:
            return float(row["net_pnl"] or 0.0) / risk_usd
    except Exception:
        pass
    return None


def calculate_expectancy(days: int = 30, environment: Optional[str] = None) -> dict:
    """Son `days` günün kapanmış trade'lerinden expectancy (R cinsinden) hesaplar.

    E = (WR × AvgWin_R) − ((1−WR) × |AvgLoss_R|)

    Returns:
        expectancy_r, avg_win_r, avg_loss_r, win_rate, trades_per_day,
        weekly_r_projection, n (örneklem), days, environment.
    NEDEN (Faz 3.1): Sistemin tek kuzey yıldızı metriği. Dashboard ana kartı,
    /stats, ve Friday context bu fonksiyonu çağırır — tek kaynak.
    """
    import database  # lazy: dairesel import (database → accounting) önlenir
    from datetime import datetime, timezone, timedelta

    env = environment or getattr(__import__("config"), "EXECUTION_MODE", "paper")
    result = {
        "expectancy_r": 0.0, "avg_win_r": 0.0, "avg_loss_r": 0.0,
        "win_rate": 0.0, "trades_per_day": 0.0, "weekly_r_projection": 0.0,
        "n": 0, "days": days, "environment": env,
    }
    try:
        cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
        with database.get_conn() as conn:
            rows = conn.execute(
                """
                SELECT net_pnl, risk_usd, r_multiple FROM trades
                WHERE status = 'closed'
                  AND close_time >= ?
                  AND environment = ?
                  AND COALESCE(is_valid_for_stats, 1) = 1
                """,
                (cutoff, env),
            ).fetchall()
        r_values = [r for r in (_trade_r_value(row) for row in rows) if r is not None]
        n = len(r_values)
        result["n"] = n
        if n == 0:
            return result
        wins = [r for r in r_values if r > 0]
        losses = [r for r in r_values if r <= 0]
        wr = len(wins) / n
        avg_win = (sum(wins) / len(wins)) if wins else 0.0
        avg_loss = abs(sum(losses) / len(losses)) if losses else 0.0
        expectancy = (wr * avg_win) - ((1.0 - wr) * avg_loss)
        trades_per_day = n / max(1, days)
        result.update({
            "expectancy_r": round(expectancy, 4),
            "avg_win_r": round(avg_win, 4),
            "avg_loss_r": round(avg_loss, 4),
            "win_rate": round(wr, 4),
            "trades_per_day": round(trades_per_day, 2),
            # Haftalık R projeksiyonu: işlem/gün × 7 × beklenti
            "weekly_r_projection": round(trades_per_day * 7 * expectancy, 3),
        })
        return result
    except Exception as e:
        logger.error("[Expectancy] Hesaplama hatası: %s", e)
        return result


# ── Margin & leverage ─────────────────────────────────────────────

def calculate_margin_loss_pct(
    entry: float, sl: float, leverage: int
) -> float:
    """SL vurulursa marjinde kayıp yüzdesi = stop_dist_pct * leverage."""
    if entry <= 0:
        return 0.0
    stop_dist_pct = abs(entry - sl) / entry
    return round(stop_dist_pct * leverage, 4)


def calculate_max_loss_after_fee(
    risk_usd: float,
    notional: float,
    fee_rate: float = 0.0004,
) -> float:
    """Fee dahil maksimum kayıp."""
    total_fee = notional * fee_rate * 2  # açılış + kapanış
    return round(risk_usd + total_fee, 4)


# ── Runner & composite PnL helpers ───────────────────────────────

def calculate_runner_unrealized_pnl(
    direction: str, entry: float, current_price: float,
    remaining_qty: float, fee_rate: float = 0.0004
) -> float:
    """
    Runner unrealized PnL. SADECE tahmini exit fee düşülür.
    """
    if remaining_qty <= 0:
        return 0.0
    raw_pnl = calculate_pnl(direction, entry, current_price, remaining_qty)
    exit_fee_est = calculate_fee(current_price * remaining_qty, fee_rate)
    return round(raw_pnl - exit_fee_est, 6)


def calculate_open_trade_total_pnl(realized_pnl: float, runner_unrealized_pnl: float) -> float:
    return round((realized_pnl or 0) + (runner_unrealized_pnl or 0), 6)


def calculate_close_pnl(
    realized_pnl: float, runner_pnl: float, total_fee_adjustment: float = 0
) -> float:
    return round((realized_pnl or 0) + (runner_pnl or 0) - (total_fee_adjustment or 0), 6)


# ── Risk doğrulama ─────────────────────────────────────────────────

def validate_risk(
    signal: SignalData,
    balance: float,
    max_leverage: int = 20,
    max_risk_pct: float = 5.0,
) -> tuple[bool, str]:
    """
    Sinyalin risk parametrelerini doğrular.
    Returns: (geçerli_mi, neden)
    """
    stop_distance = abs(signal.entry_price - signal.stop_loss)
    if stop_distance <= 0:
        return False, "Stop distance sıfır veya negatif"

    if signal.entry_price <= 0:
        return False, "Entry price sıfır veya negatif"

    is_forced = "force" in str(getattr(signal, "source", "")).lower() or "force" in str(getattr(signal, "reason", "")).lower()
    if is_forced:
        return True, "OK"

    if signal.leverage > max_leverage:
        return False, f"Leverage ({signal.leverage}) > max ({max_leverage})"

    if signal.risk_pct > max_risk_pct:
        return False, f"Risk% ({signal.risk_pct}) > max ({max_risk_pct})"

    tp1 = signal.tp1 or 0
    if tp1 > 0:
        rr = calculate_rr(signal.entry_price, signal.stop_loss, tp1)
        import config
        is_scalp = not getattr(config, "HUMAN_MODE", False)
        min_rr = 0.2 if is_scalp else getattr(config, "MIN_RR", 1.0)
        if rr < min_rr:
            return False, f"RR ({rr}) minimum {min_rr} altında"

    return True, "OK"


def validate_trade_risk(
    balance: float, entry: float, stop: float, leverage: int,
    risk_pct: float = 1.0, fee_rate: float = 0.0004,
    max_margin_loss: float = 0.40
) -> tuple:
    if entry <= 0 or stop <= 0 or leverage <= 0:
        return False, "Geçersiz entry/stop/leverage"

    stop_dist_pct = abs(entry - stop) / entry
    margin_loss_pct = stop_dist_pct * leverage

    if margin_loss_pct > max_margin_loss:
        return False, (
            f"Yüksek marjin kaybı riski: "
            f"%{margin_loss_pct*100:.1f} > %{max_margin_loss*100:.0f}"
        )

    risk_usd = balance * (risk_pct / 100.0)
    qty = risk_usd / abs(entry - stop)
    notional = qty * entry
    total_fee = calculate_fee(notional, fee_rate) * 2
    max_loss = risk_usd + total_fee

    if max_loss > balance * 0.05:
        return False, f"Tek trade max kaybı bakiyenin %5'ini aşıyor: {max_loss:.2f}"

    return True, "Güvenli"


# ── Trade builder ──────────────────────────────────────────────────

def build_trade_from_signal(
    signal: SignalData,
    balance: float,
    fee_rate: float = 0.0004,
    max_leverage: int = 20,
) -> Optional[TradeData]:
    """
    SignalData'dan TradeData oluşturur.
    Risk doğrulaması başarısızsa None döner.
    """
    import config

    is_forced = "force" in str(getattr(signal, "source", "")).lower() or "force" in str(getattr(signal, "reason", "")).lower()

    # ── Spread & Fee-Aware Take-Profit Optimizer (Scalp Recommendation) ──
    is_scalp = not getattr(config, "HUMAN_MODE", False)
    if not is_forced and is_scalp and getattr(config, "SCALP_TP_OPTIMIZER_ENABLED", True):
        try:
            from core.market_data import get_book_ticker
            book = get_book_ticker(signal.symbol)
            if book:
                spread = abs(float(book.get("askPrice", 0)) - float(book.get("bidPrice", 0)))
            else:
                spread = signal.entry_price * 0.0005
                
            # Clamp spread to prevent outlier/abnormal values
            max_allowed_spread = signal.entry_price * 0.005
            if spread > max_allowed_spread:
                spread = max_allowed_spread
                
            round_trip_fee = signal.entry_price * fee_rate * 2.0
            min_profit_diff = (round_trip_fee + spread) * getattr(config, "MIN_TP_FEE_SPREAD_RATIO", 2.5)
            
            if signal.tp1 and signal.tp1 > 0:
                current_diff = abs(signal.tp1 - signal.entry_price)
                if current_diff < min_profit_diff:
                    old_tp1 = signal.tp1
                    if signal.side == "LONG":
                        signal.tp1 = signal.entry_price + min_profit_diff
                        if signal.tp2:
                            signal.tp2 = max(signal.tp2, signal.tp1 + max(abs(signal.tp2 - old_tp1), min_profit_diff))
                        if signal.tp3:
                            signal.tp3 = max(signal.tp3, (signal.tp2 or signal.tp1) + max(abs(signal.tp3 - (signal.tp2 or old_tp1)), min_profit_diff))
                    else:  # SHORT
                        signal.tp1 = signal.entry_price - min_profit_diff
                        if signal.tp2:
                            signal.tp2 = min(signal.tp2, signal.tp1 - max(abs(old_tp1 - signal.tp2), min_profit_diff))
                        if signal.tp3:
                            signal.tp3 = min(signal.tp3, (signal.tp2 or signal.tp1) - max(abs((signal.tp2 or old_tp1) - signal.tp3), min_profit_diff))
                            
                    logger.info(
                        "[Accounting] Scalp TP Optimizer: Symbol=%s, Entry=%.4f. Old TP1=%.4f -> Optimized TP1=%.4f (Min Profit Diff Required=%.4f, Spread=%.4f, Est Fee=%.4f)",
                        signal.symbol, signal.entry_price, old_tp1, signal.tp1, min_profit_diff, spread, round_trip_fee
                    )
        except Exception as _e:
            logger.error(f"[Accounting] Error optimizing take profit: {_e}")

    valid, reason = validate_risk(signal, balance, max_leverage)
    if not valid:
        logger.warning(
            "Trade oluşturulamadı [%s]: %s", signal.symbol, reason
        )
        return None

    if is_forced:
        leverage = signal.leverage
    else:
        leverage = min(signal.leverage, max_leverage)
    if leverage <= 0:
        leverage = 1

    # ── Piyasa Rejimi tespiti ─────────────────────────────────────────
    regime = "TRENDING"
    if signal.metadata and "market_regime" in signal.metadata:
        regime = signal.metadata.get("market_regime", "TRENDING")
    else:
        try:
            from database import get_market_regime
            regime = get_market_regime()
        except Exception:
            regime = "TRENDING"

    # Dinamik Büyüklük (Kelly Kriteri Benzeri + Piyasa Rejimi Koruyucusu)
    base_risk = signal.risk_pct
    if is_forced:
        dynamic_risk = base_risk
        logger.info(f"[Accounting] Forced trade detected. Dynamic Risk set to Base Risk: {dynamic_risk}%")
    else:
        score = getattr(signal, "final_score", 75.0) or 75.0
        dynamic_risk = base_risk * (score / 75.0)
        
        if score >= 80.0:
            dynamic_risk *= 1.3
        elif score <= 55.0:
            dynamic_risk *= 0.6
            
        if regime in ("CHOPPY", "CHOPPY_HIGH_VOL", "CHOPPY_LOW_VOL"):
            dynamic_risk *= 0.5
        elif regime in ("BULLISH", "BEARISH", "TRENDING_HIGH_VOL", "TRENDING_LOW_VOL"):
            dynamic_risk *= 1.1

        # Dynamic Risk Scaling via Sharpe & Sortino ratios (Hedge Fund Guard)
        try:
            from core.portfolio_risk import calculate_sharpe_sortino_ratios
            ratios = calculate_sharpe_sortino_ratios("paper")
            sortino = ratios.get("sortino_ratio", 0.0)
            sharpe = ratios.get("sharpe_ratio", 0.0)
            
            if sharpe != 0.0 or sortino != 0.0:
                if sortino < 0.0 or sharpe < 0.0:
                    dynamic_risk *= 0.5
                    logger.info(f"[Accounting Risk Guard] Highly negative Sortino ({sortino}) or Sharpe ({sharpe}). Risk scaled by 0.5x.")
                elif sortino < 0.5 or sharpe < 0.5:
                    dynamic_risk *= 0.75
                    logger.info(f"[Accounting Risk Guard] Low Sortino ({sortino}) or Sharpe ({sharpe}). Risk scaled by 0.75x.")
                elif sortino >= 2.0 and sharpe >= 2.0:
                    dynamic_risk *= 1.2
                    logger.info(f"[Accounting Risk Guard] Excellent Sortino ({sortino}) and Sharpe ({sharpe}). Risk boosted by 1.2x.")
        except Exception as e:
            logger.debug(f"[Accounting Risk Guard] Failed to adjust risk with Sharpe/Sortino: {e}")

        dynamic_risk = max(base_risk * 0.2, min(dynamic_risk, base_risk * 2.0))
        dynamic_risk = max(0.2, min(dynamic_risk, 3.0))
        logger.info(f"[Accounting] Dynamic Risk for {signal.symbol}: Base={base_risk}% -> Dynamic={dynamic_risk:.2f}% (Score={score}, Regime={regime})")

    pos = calculate_position_size(
        balance=balance,
        risk_pct=dynamic_risk,
        entry_price=signal.entry_price,
        stop_loss=signal.stop_loss,
        leverage=leverage,
        fee_rate=fee_rate,
    )
    if not pos["valid"]:
        logger.error("Position size hesaplanamadı [%s]: %s", signal.symbol, pos["reason"])
        return None

    quantity = pos["qty"]
    notional = pos["notional"]
    margin_used = pos["margin"]
    risk_usd = pos["risk_usd"]

    # ── Check Portfolio Exposure & Available Balance limits ──
    try:
        import database
        open_trades = database.get_open_trades()
        used_margin = sum(float(t.get("margin_used") or t.get("margin") or 0.0) for t in open_trades)
        
        # Max portfolio exposure check
        max_allowed_margin = balance * (float(getattr(config, "MAX_PORTFOLIO_EXPOSURE_PCT", 95.0)) / 100.0)
        req_margin = margin_used
        
        if not is_forced:
            if used_margin + req_margin > max_allowed_margin:
                logger.warning(
                    "[Accounting] %s rejected: margin exposure exceeded. Used=%.2f, Req=%.2f, MaxAllowed=%.2f",
                    signal.symbol, used_margin, req_margin, max_allowed_margin
                )
                return None
                
            if req_margin > (balance - used_margin):
                logger.warning(
                    "[Accounting] %s rejected: insufficient available balance. Balance=%.2f, Used=%.2f, Req=%.2f",
                    signal.symbol, balance, used_margin, req_margin
                )
                return None
        else:
            # If forced and we don't have enough available balance, scale down the quantity/margin if possible
            if req_margin > (balance - used_margin):
                available = balance - used_margin
                if available > 0.1:
                    scale = available / req_margin
                    quantity = quantity * scale
                    notional = notional * scale
                    margin_used = margin_used * scale
                    risk_usd = risk_usd * scale
                    logger.info(
                        "[Accounting] Forced trade %s scaled down to fit available margin. "
                        "Scale=%.4f, New Qty=%.6f, Margin=%.4f",
                        signal.symbol, scale, quantity, margin_used
                    )
                else:
                    logger.info(
                        "[Accounting] Forced trade %s bypasses margin limits but no available balance remains. "
                        "Executing with original size.", signal.symbol
                    )
    except Exception as _e:
        logger.exception("[Accounting] Margin exposure check skipped or failed")


    # Dinamik Take-Profit (Piyasa Rejimine Göre)
    regime = "TRENDING"
    if signal.metadata and "market_regime" in signal.metadata:
        regime = signal.metadata.get("market_regime", "TRENDING")
    
    if regime in ("CHOPPY", "CHOPPY_HIGH_VOL", "CHOPPY_LOW_VOL"):
        # Testere piyasada hızlı kâr al, runner bırakma
        pct_tp1, pct_tp2, pct_runner = 0.70, 0.30, 0.0
    else:
        # Trend piyasasında runner bırak
        pct_tp1, pct_tp2, pct_runner = 0.30, 0.20, 0.50

    qty_tp1 = quantity * pct_tp1
    qty_tp2 = quantity * pct_tp2
    qty_runner = quantity * pct_runner

    return TradeData(
        symbol=signal.symbol,
        side=signal.side,
        entry_price=signal.entry_price,
        stop_loss=signal.stop_loss,
        tp1=signal.tp1,
        tp2=signal.tp2,
        tp3=signal.tp3,
        quantity=quantity,
        qty_tp1=qty_tp1,
        qty_tp2=qty_tp2,
        qty_runner=qty_runner,
        leverage=leverage,
        notional=notional,
        margin_used=margin_used,
        risk_usd=round(risk_usd, 4),
        risk_pct=dynamic_risk,
        status=TradeStatus.OPEN.value,
        current_price=signal.entry_price,
        setup_quality=getattr(signal, "setup_quality", "") or "",
        setup_type=getattr(signal, "setup_type", "UNKNOWN") or "UNKNOWN",
    )


# ── Utility ────────────────────────────────────────────────────────

def _floor(val: float, step: float) -> float:
    if step <= 0:
        return val
    prec = (
        len(str(step).rstrip("0").split(".")[-1])
        if "." in str(step) else 0
    )
    return round(math.floor(val / step) * step, prec)
