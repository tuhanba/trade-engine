"""
core/accounting.py — AX Merkezi Muhasebe Modülü v5.1 (PAPER-ONLY / LIVE-BLOCKED)
==================================================================
Tüm PnL, Fee, Margin, Risk ve Leverage hesaplamaları burada yapılır.
execution_engine, dashboard, backtest ve audit bu modülü kullanır.

KURALLAR:
- Leverage PnL'e ikinci kez çarpılmaz.
- LONG PnL = (price - entry) * qty
- SHORT PnL = (entry - price) * qty
- Fee paper modda bile net PnL'den düşülür.
- x20 + %5 stop = %100 margin kaybı olarak algılanır.
- margin_loss_pct > 0.40 ise trade açılmaz.

FEE MİMARİSİ:
- open_fee: trade açılışında full pozisyon için tek seferlik ödenir.
- Kısmi kapanışlarda (TP1/TP2/final) SADECE exit-side fee kesilir.
- Entry-side fee partial close'larda tekrar kesilmez — double-count önlenir.
"""
import math
import logging

logger = logging.getLogger(__name__)


def calculate_pnl(direction: str, entry_price: float, current_price: float, qty: float) -> float:
    if direction.upper() == "LONG":
        return (current_price - entry_price) * qty
    else:
        return (entry_price - current_price) * qty


def calculate_fee(notional_size: float, fee_rate: float = 0.0004) -> float:
    return notional_size * fee_rate


def calculate_notional_and_margin(entry_price: float, qty: float, leverage: int) -> tuple:
    notional_size = qty * entry_price
    margin_used = notional_size / leverage if leverage > 0 else notional_size
    return notional_size, margin_used


def calculate_position_size(
    balance: float,
    risk_pct: float,
    entry: float,
    stop: float,
    leverage: int,
    fee_rate: float = 0.0004,
    filters: dict = None
) -> dict:
    from config import TP1_CLOSE_PCT, TP2_CLOSE_PCT, MAX_MARGIN_LOSS_PCT

    risk_usd = balance * (risk_pct / 100.0)
    sl_dist = abs(entry - stop)
    if sl_dist <= 0:
        return {"valid": False, "reason": "SL distance is zero"}

    stop_dist_pct = sl_dist / entry
    margin_loss_pct = stop_dist_pct * leverage
    if margin_loss_pct > MAX_MARGIN_LOSS_PCT:
        return {
            "valid": False,
            "reason": f"margin_loss_pct={margin_loss_pct:.2%} > {MAX_MARGIN_LOSS_PCT:.0%}"
        }

    qty = risk_usd / sl_dist

    step_size = 0.001
    min_qty = 0.001
    min_notional = 5.0
    if filters:
        step_size = filters.get("step_size", 0.001)
        min_qty = filters.get("min_qty", 0.001)
        min_notional = filters.get("min_notional", 5.0)

    qty = _floor(qty, step_size)
    qty = max(qty, min_qty)

    if qty * entry < min_notional:
        qty = _floor(min_notional / entry * 1.01, step_size)

    qty = round(qty, 8)

    qty_tp1 = round(qty * TP1_CLOSE_PCT / 100, 8)
    qty_tp2 = round(qty * TP2_CLOSE_PCT / 100, 8)
    qty_runner = round(qty - qty_tp1 - qty_tp2, 8)

    notional_size, margin_used = calculate_notional_and_margin(entry, qty, leverage)

    open_fee = calculate_fee(notional_size, fee_rate)

    sl_exit_notional = stop * qty
    sl_exit_fee = calculate_fee(sl_exit_notional, fee_rate)
    max_loss_after_fee = risk_usd + open_fee + sl_exit_fee

    return {
        "valid": True,
        "qty": qty,
        "qty_tp1": qty_tp1,
        "qty_tp2": qty_tp2,
        "qty_runner": qty_runner,
        "risk_usd": round(risk_usd, 4),
        "notional_size": round(notional_size, 4),
        "margin_used": round(margin_used, 4),
        "open_fee": round(open_fee, 6),
        "close_fee_estimate": round(sl_exit_fee, 6),
        "max_loss_after_fee": round(max_loss_after_fee, 4),
        "margin_loss_pct": round(margin_loss_pct, 4),
        "stop_dist_pct": round(stop_dist_pct, 6),
    }


def calculate_max_loss_after_fee(
    entry: float, stop: float, qty: float, fee_rate: float = 0.0004
) -> float:
    sl_dist = abs(entry - stop)
    raw_loss = sl_dist * qty
    entry_notional = entry * qty
    exit_notional = stop * qty
    total_fee = calculate_fee(entry_notional, fee_rate) + calculate_fee(exit_notional, fee_rate)
    return round(raw_loss + total_fee, 4)


def calculate_margin_loss_pct(entry: float, stop: float, leverage: int) -> float:
    stop_dist_pct = abs(entry - stop) / entry if entry > 0 else 0
    return round(stop_dist_pct * leverage, 4)


def calculate_partial_close_pnl(
    direction: str, entry: float, exit_price: float,
    close_qty: float, fee_rate: float = 0.0004
) -> tuple:
    """
    Kısmi kapanış PnL (TP1, TP2, SL, final).
    SADECE exit-side fee kesilir — entry fee açılışta ödendi.
    Returns: (net_pnl, exit_fee)
    """
    raw_pnl = calculate_pnl(direction, entry, exit_price, close_qty)
    exit_fee = calculate_fee(exit_price * close_qty, fee_rate)
    net_pnl = raw_pnl - exit_fee
    return round(net_pnl, 6), round(exit_fee, 6)


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


def calculate_r_multiple(net_pnl: float, risk_usd: float) -> float:
    if not risk_usd or risk_usd <= 0:
        return 0.0
    return round(net_pnl / risk_usd, 3)


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


def _floor(val: float, step: float) -> float:
    if step <= 0:
        return val
    prec = (
        len(str(step).rstrip("0").split(".")[-1])
        if "." in str(step) else 0
    )
    return round(math.floor(val / step) * step, prec)
