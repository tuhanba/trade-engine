"""
<<<<<<< HEAD
core/accounting.py — AX Accounting Engine v5.0 (Production)
=============================================================
Merkezi PnL / fee / margin / risk hesap motoru.

Tüm finansal hesaplamalar bu dosyadan yapılır.
Backtest Engine ve Execution Engine bu modülü kullanır.
"""

from __future__ import annotations

import logging
from typing import Optional, Tuple

from core.data_layer import SignalData, TradeData, TradeStatus

logger = logging.getLogger("ax.accounting")


# ── Pozisyon büyüklüğü ─────────────────────────────────────────────
=======
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

>>>>>>> 0797c70b8640d2006e47a50d5580ffae4606199b

def calculate_position_size(
    balance: float,
    risk_pct: float,
<<<<<<< HEAD
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

    if signal.leverage > max_leverage:
        return False, f"Leverage ({signal.leverage}) > max ({max_leverage})"

    if signal.risk_pct > max_risk_pct:
        return False, f"Risk% ({signal.risk_pct}) > max ({max_risk_pct})"

    if signal.entry_price <= 0:
        return False, "Entry price sıfır veya negatif"

    tp1 = signal.tp1 or 0
    if tp1 > 0:
        rr = calculate_rr(signal.entry_price, signal.stop_loss, tp1)
        if rr < 1.0:
            return False, f"RR ({rr}) minimum 1.0 altında"

    return True, "OK"


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
    valid, reason = validate_risk(signal, balance, max_leverage)
    if not valid:
        logger.warning(
            "Trade oluşturulamadı [%s]: %s", signal.symbol, reason
        )
        return None

    leverage = min(signal.leverage, max_leverage)
    if leverage <= 0:
        leverage = 1

    pos = calculate_position_size(
        balance=balance,
        risk_pct=signal.risk_pct,
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

    return TradeData(
        symbol=signal.symbol,
        side=signal.side,
        entry_price=signal.entry_price,
        stop_loss=signal.stop_loss,
        tp1=signal.tp1,
        tp2=signal.tp2,
        tp3=signal.tp3,
        quantity=quantity,
        leverage=leverage,
        notional=notional,
        margin_used=margin_used,
        risk_usd=round(risk_usd, 4),
        risk_pct=signal.risk_pct,
        status=TradeStatus.OPEN.value,
        current_price=signal.entry_price,
    )
=======
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
>>>>>>> 0797c70b8640d2006e47a50d5580ffae4606199b
