"""
core/accounting.py — AX Merkezi Muhasebe Modülü v5.0 (PAPER-ONLY / LIVE-BLOCKED)
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
"""
import math
import logging

logger = logging.getLogger(__name__)


def calculate_pnl(direction: str, entry_price: float, current_price: float, qty: float) -> float:
    """
    Raw PnL hesaplar. Leverage PnL'e çarpılmaz.
    LONG PnL = (price - entry) * qty
    SHORT PnL = (entry - price) * qty
    """
    if direction.upper() == "LONG":
        return (current_price - entry_price) * qty
    else:
        return (entry_price - current_price) * qty


def calculate_fee(notional_size: float, fee_rate: float = 0.0004) -> float:
    """
    Binance fee (default 0.04% for futures taker).
    Tek yön (giriş veya çıkış) için.
    """
    return notional_size * fee_rate


def calculate_notional_and_margin(entry_price: float, qty: float, leverage: int) -> tuple:
    """
    notional_size = qty * entry_price
    margin_used = notional_size / leverage
    Returns: (notional_size, margin_used)
    """
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
    """
    Risk bazlı pozisyon büyüklüğü hesapla.

    Returns dict:
        qty, qty_tp1, qty_tp2, qty_runner,
        risk_usd, notional_size, margin_used,
        max_loss_after_fee, margin_loss_pct
    """
    from config import TP1_CLOSE_PCT, TP2_CLOSE_PCT, MAX_MARGIN_LOSS_PCT

    risk_usd = balance * (risk_pct / 100.0)
    sl_dist = abs(entry - stop)
    if sl_dist <= 0:
        return {"valid": False, "reason": "SL distance is zero"}

    # Margin loss kontrolü
    stop_dist_pct = sl_dist / entry
    margin_loss_pct = stop_dist_pct * leverage
    if margin_loss_pct > MAX_MARGIN_LOSS_PCT:
        return {
            "valid": False,
            "reason": f"margin_loss_pct={margin_loss_pct:.2%} > {MAX_MARGIN_LOSS_PCT:.0%}"
        }

    # Qty = risk_usd / sl_distance (leverage qty'ye dahil değil, notional'da dahil)
    qty = risk_usd / sl_dist

    # Filters uygula
    step_size = 0.001
    min_qty = 0.001
    min_notional = 5.0
    if filters:
        step_size = filters.get("step_size", 0.001)
        min_qty = filters.get("min_qty", 0.001)
        min_notional = filters.get("min_notional", 5.0)

    qty = _floor(qty, step_size)
    qty = max(qty, min_qty)

    # Min notional kontrolü
    if qty * entry < min_notional:
        qty = _floor(min_notional / entry * 1.01, step_size)

    qty = round(qty, 8)

    # Partial close miktarları
    qty_tp1 = round(qty * TP1_CLOSE_PCT / 100, 8)
    qty_tp2 = round(qty * TP2_CLOSE_PCT / 100, 8)
    qty_runner = round(qty - qty_tp1 - qty_tp2, 8)

    # Notional ve margin
    notional_size, margin_used = calculate_notional_and_margin(entry, qty, leverage)

    # Fee tahmini (giriş + çıkış)
    open_fee = calculate_fee(notional_size, fee_rate)
    close_fee_estimate = calculate_fee(notional_size, fee_rate)  # Çıkış tahmini

    # Max loss (risk + fees)
    max_loss_after_fee = risk_usd + open_fee + close_fee_estimate

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
        "close_fee_estimate": round(close_fee_estimate, 6),
        "max_loss_after_fee": round(max_loss_after_fee, 4),
        "margin_loss_pct": round(margin_loss_pct, 4),
        "stop_dist_pct": round(stop_dist_pct, 6),
    }


def calculate_max_loss_after_fee(
    entry: float, stop: float, qty: float, fee_rate: float = 0.0004
) -> float:
    """SL'de toplam kayıp (raw loss + giriş fee + çıkış fee)."""
    sl_dist = abs(entry - stop)
    raw_loss = sl_dist * qty
    entry_notional = entry * qty
    exit_notional = stop * qty
    total_fee = calculate_fee(entry_notional, fee_rate) + calculate_fee(exit_notional, fee_rate)
    return round(raw_loss + total_fee, 4)


def calculate_margin_loss_pct(entry: float, stop: float, leverage: int) -> float:
    """
    Stop'ta margin kaybı yüzdesi.
    x20 + %5 stop = %100 margin kaybı.
    """
    stop_dist_pct = abs(entry - stop) / entry if entry > 0 else 0
    return round(stop_dist_pct * leverage, 4)


def calculate_partial_close_pnl(
    direction: str, entry: float, exit_price: float,
    close_qty: float, fee_rate: float = 0.0004
) -> tuple:
    """
    Kısmi kapanış PnL hesabı (TP1, TP2).
    Returns: (net_pnl, fee)
    """
    raw_pnl = calculate_pnl(direction, entry, exit_price, close_qty)
    entry_notional = entry * close_qty
    exit_notional = exit_price * close_qty
    fee = calculate_fee(entry_notional, fee_rate) + calculate_fee(exit_notional, fee_rate)
    net_pnl = raw_pnl - fee
    return round(net_pnl, 6), round(fee, 6)


def calculate_runner_unrealized_pnl(
    direction: str, entry: float, current_price: float,
    remaining_qty: float, fee_rate: float = 0.0004
) -> float:
    """
    Runner (kalan qty) için anlık unrealized PnL.
    Fee tahmini düşülür.
    """
    if remaining_qty <= 0:
        return 0.0
    raw_pnl = calculate_pnl(direction, entry, current_price, remaining_qty)
    entry_notional = entry * remaining_qty
    exit_notional = current_price * remaining_qty
    fee_estimate = calculate_fee(entry_notional, fee_rate) + calculate_fee(exit_notional, fee_rate)
    return round(raw_pnl - fee_estimate, 6)


def calculate_open_trade_total_pnl(realized_pnl: float, runner_unrealized_pnl: float) -> float:
    """
    Açık trade'nin toplam PnL'i.
    = TP1 + TP2 realized + runner unrealized
    """
    return round((realized_pnl or 0) + (runner_unrealized_pnl or 0), 6)


def calculate_close_pnl(
    realized_pnl: float, runner_pnl: float, total_fee_adjustment: float = 0
) -> float:
    """
    Final kapanış PnL'i.
    trades.net_pnl = TP1 + TP2 + runner/final net toplamı
    """
    return round((realized_pnl or 0) + (runner_pnl or 0) - (total_fee_adjustment or 0), 6)


def calculate_r_multiple(net_pnl: float, risk_usd: float) -> float:
    """
    R-Multiple = net_pnl / risk_usd
    """
    if not risk_usd or risk_usd <= 0:
        return 0.0
    return round(net_pnl / risk_usd, 3)


def validate_trade_risk(
    balance: float, entry: float, stop: float, leverage: int,
    risk_pct: float = 1.0, fee_rate: float = 0.0004,
    max_margin_loss: float = 0.40
) -> tuple:
    """
    Trade açmadan önceki zorunlu güvenlik kontrolü.
    Returns: (is_safe: bool, reason: str)
    """
    if entry <= 0 or stop <= 0 or leverage <= 0:
        return False, "Geçersiz entry/stop/leverage"

    stop_dist_pct = abs(entry - stop) / entry
    margin_loss_pct = stop_dist_pct * leverage

    if margin_loss_pct > max_margin_loss:
        return False, f"Yüksek marjin kaybı riski: %{margin_loss_pct*100:.1f} > %{max_margin_loss*100:.0f}"

    risk_usd = balance * (risk_pct / 100.0)
    qty = risk_usd / abs(entry - stop)
    notional = qty * entry
    total_fee = calculate_fee(notional, fee_rate) * 2
    max_loss_after_fee = risk_usd + total_fee

    if max_loss_after_fee > risk_usd * 1.5:
        return False, f"Yüksek fee maliyeti: {total_fee:.2f} USD"

    if max_loss_after_fee > balance * 0.05:
        return False, f"Tek trade kaybı bakiyenin %5'ini aşıyor: {max_loss_after_fee:.2f}"

    return True, "Güvenli"


def _floor(val: float, step: float) -> float:
    """Step size'a göre aşağı yuvarla."""
    if step <= 0:
        return val
    prec = len(str(step).rstrip("0").split(".")[-1]) if "." in str(step) else 0
    return round(math.floor(val / step) * step, prec)
