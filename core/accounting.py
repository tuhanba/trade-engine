"""
core/accounting.py — Canonical Muhasebe Modülü
================================================
TÜM PnL, fee, risk, margin hesapları buradan yapılır.
execution_engine, app.py, dashboard_service, telegram_delivery,
backtest ve audit scriptleri SADECE bu modülü kullanır.

KURAL: Leverage PnL'e ikinci kez çarpılmaz.
       gross_pnl = (exit - entry) * qty          (LONG)
       gross_pnl = (entry - exit) * qty          (SHORT)
       notional  = qty * entry
       margin    = notional / leverage
"""

from __future__ import annotations
import logging

logger = logging.getLogger(__name__)

# ─── Binance Futures varsayılan fee oranları ───────────────────────────────
DEFAULT_TAKER_FEE = 0.0004   # %0.04
DEFAULT_MAKER_FEE = 0.0002   # %0.02


# ══════════════════════════════════════════════════════════════════════════════
# TEMEL HESAPLAMALAR
# ══════════════════════════════════════════════════════════════════════════════

def calculate_pnl(entry: float, price: float, qty: float, direction: str) -> float:
    """
    Kaldıraçsız gross PnL hesapla.
    Leverage qty içinde zaten yansımıştır; buraya ikinci kez çarpılmaz.

    LONG:  gross_pnl = (price - entry) * qty
    SHORT: gross_pnl = (entry - price) * qty
    """
    if entry <= 0 or qty <= 0:
        return 0.0
    if direction.upper() == "LONG":
        return (price - entry) * qty
    else:
        return (entry - price) * qty


def calculate_fee(notional: float, fee_rate: float = DEFAULT_TAKER_FEE) -> float:
    """
    Tek taraf komisyon.
    fee = notional * fee_rate
    """
    return max(0.0, notional * fee_rate)


def calculate_margin(notional: float, leverage: int) -> float:
    """
    Teminat (margin) = notional / leverage
    """
    lev = max(1, int(leverage or 1))
    return notional / lev


def calculate_notional(qty: float, entry: float) -> float:
    """Pozisyon büyüklüğü = qty * entry"""
    return qty * entry


# ══════════════════════════════════════════════════════════════════════════════
# POZİSYON BOYUTU VE RİSK
# ══════════════════════════════════════════════════════════════════════════════

def calculate_position_size(
    balance: float,
    risk_pct: float,
    entry: float,
    stop: float,
    leverage: int,
    taker_fee_rate: float = DEFAULT_TAKER_FEE,
    symbol_filters: dict | None = None,
) -> dict:
    """
    Pozisyon boyutunu hesapla.

    Formül:
        risk_usd = balance * risk_pct / 100
        sl_dist  = abs(entry - stop)
        qty      = risk_usd / sl_dist          ← kaldıraç burada kullanılmaz
        notional = qty * entry
        margin   = notional / leverage
        open_fee = notional * taker_fee_rate
        close_fee_at_stop = qty * stop * taker_fee_rate
        max_loss_after_fee = sl_dist * qty + open_fee + close_fee_at_stop

    Güvenlik: max_loss_after_fee > risk_usd ise qty'yi küçült.

    Returns dict:
        qty, notional_size, margin_used, risk_usd, sl_dist,
        open_fee, close_fee_at_stop, max_loss_after_fee, fee_rate
    """
    if entry <= 0 or stop <= 0 or balance <= 0:
        return _zero_position(risk_pct, balance, taker_fee_rate)

    sl_dist = abs(entry - stop)
    if sl_dist < entry * 0.0003:   # SL çok yakın → geçersiz
        logger.warning(f"[Accounting] SL çok yakın: entry={entry} stop={stop}")
        return _zero_position(risk_pct, balance, taker_fee_rate)

    risk_usd = balance * (risk_pct / 100.0)
    qty_raw  = risk_usd / sl_dist

    # Fee hesapla
    notional       = qty_raw * entry
    open_fee       = notional * taker_fee_rate
    close_fee_stop = qty_raw * stop * taker_fee_rate
    max_loss_fee   = sl_dist * qty_raw + open_fee + close_fee_stop

    # Fee dahil max_loss > risk_usd ise qty'yi küçült
    if max_loss_fee > risk_usd:
        # qty * (sl_dist + entry*fee + stop*fee) = risk_usd
        denom = sl_dist + entry * taker_fee_rate + stop * taker_fee_rate
        if denom > 0:
            qty_raw = risk_usd / denom
            notional       = qty_raw * entry
            open_fee       = notional * taker_fee_rate
            close_fee_stop = qty_raw * stop * taker_fee_rate
            max_loss_fee   = sl_dist * qty_raw + open_fee + close_fee_stop

    # symbol_filters: min_qty, step_size, min_notional
    if symbol_filters:
        min_qty     = float(symbol_filters.get("min_qty", 0))
        step_size   = float(symbol_filters.get("step_size", 0))
        min_notional = float(symbol_filters.get("min_notional", 0))
        if step_size > 0:
            import math
            qty_raw = math.floor(qty_raw / step_size) * step_size
        if min_qty > 0 and qty_raw < min_qty:
            qty_raw = 0.0   # min_qty altında trade açma
        if min_notional > 0 and qty_raw * entry < min_notional:
            qty_raw = 0.0   # min_notional altında trade açma

    lev = max(1, int(leverage or 1))
    notional_final = qty_raw * entry
    margin_final   = notional_final / lev
    open_fee_final = notional_final * taker_fee_rate
    close_fee_final = qty_raw * stop * taker_fee_rate
    max_loss_final  = sl_dist * qty_raw + open_fee_final + close_fee_final

    return {
        "qty":               round(qty_raw, 8),
        "notional_size":     round(notional_final, 4),
        "margin_used":       round(margin_final, 4),
        "risk_usd":          round(risk_usd, 4),
        "sl_dist":           round(sl_dist, 8),
        "open_fee":          round(open_fee_final, 6),
        "close_fee_at_stop": round(close_fee_final, 6),
        "max_loss_after_fee":round(max_loss_final, 4),
        "fee_rate":          taker_fee_rate,
    }


def _zero_position(risk_pct: float, balance: float, fee_rate: float) -> dict:
    risk_usd = balance * (risk_pct / 100.0)
    return {
        "qty": 0.0, "notional_size": 0.0, "margin_used": 0.0,
        "risk_usd": round(risk_usd, 4), "sl_dist": 0.0,
        "open_fee": 0.0, "close_fee_at_stop": 0.0,
        "max_loss_after_fee": 0.0, "fee_rate": fee_rate,
    }


def calculate_max_loss(
    entry: float, stop: float, qty: float,
    taker_fee_rate: float = DEFAULT_TAKER_FEE
) -> float:
    """
    Fee dahil maksimum zarar.
    max_loss = qty * sl_dist + open_fee + close_fee_at_stop
    """
    sl_dist = abs(entry - stop)
    open_fee = qty * entry * taker_fee_rate
    close_fee = qty * stop * taker_fee_rate
    return round(sl_dist * qty + open_fee + close_fee, 4)


# ══════════════════════════════════════════════════════════════════════════════
# UNREALIZED PnL (AÇIK POZİSYON)
# ══════════════════════════════════════════════════════════════════════════════

def calculate_unrealized_net_pnl(
    entry: float,
    mark: float,
    remaining_qty: float,
    direction: str,
    realized_pnl: float,
    taker_fee_rate: float = DEFAULT_TAKER_FEE,
) -> float:
    """
    Açık pozisyonun anlık net PnL'i.

    unrealized_gross = calculate_pnl(entry, mark, remaining_qty, direction)
    close_fee_est    = remaining_qty * mark * taker_fee_rate
    unrealized_net   = realized_pnl + unrealized_gross - close_fee_est

    Bu değer trade kapanınca DB'ye yazılan net_pnl ile aynı mantıkta olmalı.
    """
    if remaining_qty <= 0:
        return round(realized_pnl, 4)
    gross = calculate_pnl(entry, mark, remaining_qty, direction)
    close_fee = remaining_qty * mark * taker_fee_rate
    return round(realized_pnl + gross - close_fee, 4)


def calculate_unrealized_pct(
    unrealized_net_pnl: float,
    margin_used: float,
) -> float:
    """
    Unrealized PnL yüzdesi = unrealized_net_pnl / margin_used * 100
    """
    if margin_used <= 0:
        return 0.0
    return round(unrealized_net_pnl / margin_used * 100, 2)


# ══════════════════════════════════════════════════════════════════════════════
# KAPANIŞ PnL
# ══════════════════════════════════════════════════════════════════════════════

def calculate_close_pnl(
    entry: float,
    close_price: float,
    qty: float,
    direction: str,
    realized_pnl: float,
    open_fee: float,
    taker_fee_rate: float = DEFAULT_TAKER_FEE,
) -> dict:
    """
    Trade kapanışında net PnL hesapla.

    gross_pnl  = calculate_pnl(entry, close_price, qty, direction)
    close_fee  = qty * close_price * taker_fee_rate
    net_pnl    = realized_pnl + gross_pnl - close_fee
    total_fee  = open_fee + close_fee
    """
    gross = calculate_pnl(entry, close_price, qty, direction)
    close_fee = qty * close_price * taker_fee_rate
    net_pnl   = realized_pnl + gross - close_fee
    total_fee = open_fee + close_fee
    return {
        "gross_pnl":  round(gross, 4),
        "close_fee":  round(close_fee, 6),
        "total_fee":  round(total_fee, 6),
        "net_pnl":    round(net_pnl, 4),
    }


def calculate_partial_close_pnl(
    entry: float,
    close_price: float,
    partial_qty: float,
    direction: str,
    taker_fee_rate: float = DEFAULT_TAKER_FEE,
) -> dict:
    """
    Kısmi kapanış (TP1/TP2) için net PnL.
    open_fee bu partial için orantılı hesaplanır.
    """
    gross = calculate_pnl(entry, close_price, partial_qty, direction)
    close_fee = partial_qty * close_price * taker_fee_rate
    # Açılış fee'si orantılı (partial_qty / total_qty bilinmediğinden sadece close fee düşülür)
    net_pnl = gross - close_fee
    return {
        "gross_pnl": round(gross, 4),
        "close_fee": round(close_fee, 6),
        "net_pnl":   round(net_pnl, 4),
    }


# ══════════════════════════════════════════════════════════════════════════════
# R-MULTIPLE
# ══════════════════════════════════════════════════════════════════════════════

def calculate_r_multiple(net_pnl: float, risk_usd: float) -> float:
    """
    R-multiple = net_pnl / risk_usd
    Pozitif = kazanç, negatif = zarar.
    """
    if risk_usd <= 0:
        return 0.0
    return round(net_pnl / risk_usd, 3)


# ══════════════════════════════════════════════════════════════════════════════
# BREAKEVEN SL
# ══════════════════════════════════════════════════════════════════════════════

def calculate_breakeven_sl(
    entry: float,
    direction: str,
    taker_fee_rate: float = DEFAULT_TAKER_FEE,
    offset_pct: float = 0.05,
) -> float:
    """
    TP1 vurulunca SL'yi breakeven + fee buffer'a çek.
    LONG:  breakeven = entry * (1 + fee_rate + offset_pct/100)
    SHORT: breakeven = entry * (1 - fee_rate - offset_pct/100)
    """
    buffer = taker_fee_rate + offset_pct / 100.0
    if direction.upper() == "LONG":
        return round(entry * (1 + buffer), 8)
    else:
        return round(entry * (1 - buffer), 8)


# ══════════════════════════════════════════════════════════════════════════════
# YARDIMCI: Binance fee çek (API'den, yoksa fallback)
# ══════════════════════════════════════════════════════════════════════════════

def get_fee_rate(symbol: str = "", use_maker: bool = False) -> float:
    """
    Binance Futures sembol bazlı fee oranını döndür.
    API erişimi yoksa fallback kullan.
    """
    try:
        import os, requests as req
        api_key = os.getenv("BINANCE_API_KEY", "")
        if not api_key:
            raise ValueError("API key yok")
        url = "https://fapi.binance.com/fapi/v1/commissionRate"
        resp = req.get(url, params={"symbol": symbol.upper()},
                       headers={"X-MBX-APIKEY": api_key}, timeout=3)
        if resp.status_code == 200:
            data = resp.json()
            key = "makerCommissionRate" if use_maker else "takerCommissionRate"
            return float(data.get(key, DEFAULT_TAKER_FEE))
    except Exception:
        pass
    return DEFAULT_MAKER_FEE if use_maker else DEFAULT_TAKER_FEE
