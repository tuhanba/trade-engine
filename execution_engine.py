"""
execution_engine.py — AX Paper Execution Motoru
=================================================
Trade State:
  open → tp1_hit → runner → closed_win/closed_loss/closed_trail

Sorumluluklar:
  - Paper trade aç (qty hesapla, TP1/TP2/runner böl)
  - Her mum/tick'te SL ve TP takip et
  - Trailing stop uygula (runner fazında)
  - Trade kapat (DB güncelle, bakiye güncelle)
  - Her değişiklik DB'ye yazar
"""

import math
import logging
from datetime import datetime, timezone

from config import (
    RISK_PCT, TP1_CLOSE_PCT, TP2_CLOSE_PCT, RUNNER_CLOSE_PCT,
    TRAIL_ATR_MULT, EXECUTION_MODE,
)
from database import (
    save_trade, update_trade, close_trade as db_close_trade,
    get_open_trades, update_paper_balance, get_paper_balance, save_postmortem,
)

logger = logging.getLogger(__name__)


# ─────────────────────────────────────────────────────────────────────────────
# YARDIMCI
# ─────────────────────────────────────────────────────────────────────────────

def _floor(val: float, step: float) -> float:
    if step <= 0:
        return val
    prec = len(str(step).rstrip("0").split(".")[-1]) if "." in str(step) else 0
    return round(math.floor(val / step) * step, prec)

def _get_filters(client, symbol: str) -> dict:
    """LOT_SIZE ve PRICE_FILTER filtrelerini Binance'ten al."""
    try:
        info = client.futures_exchange_info()
        for s in info["symbols"]:
            if s["symbol"] == symbol:
                filters = {f["filterType"]: f for f in s["filters"]}
                lot = filters.get("LOT_SIZE", {})
                price_f = filters.get("PRICE_FILTER", {})
                return {
                    "step_size":  float(lot.get("stepSize", "0.001")),
                    "min_qty":    float(lot.get("minQty",   "0.001")),
                    "tick_size":  float(price_f.get("tickSize", "0.01")),
                    "min_notional": 5.0,
                }
    except Exception as e:
        logger.warning(f"[Execution] Filtre alınamadı {symbol}: {e}")
    return {"step_size": 0.001, "min_qty": 0.001, "tick_size": 0.01, "min_notional": 5.0}


def _calc_qty(balance: float, entry: float, sl: float,
              risk_pct: float, step_size: float, min_qty: float,
              min_notional: float) -> float:
    """Risk bazlı miktar hesabı."""
    risk_usd = balance * risk_pct / 100
    sl_dist  = abs(entry - sl)
    if sl_dist <= 0:
        return 0.0
    qty = risk_usd / sl_dist
    qty = _floor(qty, step_size)
    qty = max(qty, min_qty)
    # Min notional kontrolü
    if qty * entry < min_notional:
        qty = _floor(min_notional / entry * 1.01, step_size)
    return round(qty, 8)


# ─────────────────────────────────────────────────────────────────────────────
# TRADE AÇ
# ─────────────────────────────────────────────────────────────────────────────

def open_trade(client, signal: dict, ax_decision: dict,
               risk_pct: float = None) -> int | None:
    """
    Paper trade aç.

    Returns:
        trade_id (int) ya da None (başarısız)
    """
    symbol    = signal["symbol"]
    direction = signal["direction"]
    entry     = signal["entry"]
    sl        = signal["sl"]
    tp1       = signal["tp1"]
    tp2       = signal["tp2"]
    runner    = signal["runner_target"]
    atr       = signal.get("atr", 0)

    if not direction or not entry or not sl:
        return None

    balance  = get_paper_balance()
    rp       = risk_pct or RISK_PCT
    filters  = _get_filters(client, symbol)

    qty = _calc_qty(balance, entry, sl, rp,
                    filters["step_size"], filters["min_qty"], filters["min_notional"])
    if qty <= 0:
        logger.warning(f"[Execution] {symbol} qty hesplanamadı")
        return None

    qty_tp1    = round(qty * TP1_CLOSE_PCT / 100, 8)
    qty_tp2    = round(qty * TP2_CLOSE_PCT / 100, 8)
    qty_runner = round(qty - qty_tp1 - qty_tp2, 8)

    trade = {
        "symbol":         symbol,
        "direction":      direction,
        "status":         "open",
        "environment":    EXECUTION_MODE,
        "ax_mode":        "execute",
        "entry":          entry,
        "sl":             sl,
        "tp1":            tp1,
        "tp2":            tp2,
        "trail_stop":     None,
        "qty":            qty,
        "qty_tp1":        qty_tp1,
        "qty_tp2":        qty_tp2,
        "qty_runner":     qty_runner,
        "linked_candidate_id": signal.get("candidate_id"),
        "open_time":      datetime.now(timezone.utc).isoformat(),
    }

    trade_id = save_trade(trade)
    logger.info(
        f"[Execution] AÇILDI #{trade_id} {symbol} {direction} "
        f"entry={entry:.6f} sl={sl:.6f} tp1={tp1:.6f} tp2={tp2:.6f} qty={qty}"
    )
    return trade_id


# ─────────────────────────────────────────────────────────────────────────────
# TRADE MONİTÖRÜ
# ─────────────────────────────────────────────────────────────────────────────

def monitor_trades(client) -> list:
    """
    Tüm açık trade'leri kontrol et:
    - SL tetiklendi mi?
    - TP1 / TP2 tetiklendi mi?
    - Runner trailing stop güncelle

    Returns:
        Kapanan trade'lerin ID listesi.
    """
    trades = get_open_trades()
    closed = []

    for t in trades:
        try:
            result = _check_trade(client, t)
            if result:
                closed.append(t["id"])
        except Exception as e:
            logger.error(f"[Execution] Monitor hata {t['id']}: {e}")

    return closed


def _get_price(client, symbol: str) -> float:
    try:
        ticker = client.futures_ticker(symbol=symbol)
        return float(ticker["lastPrice"])
    except Exception:
        return 0.0


def _check_trade(client, t: dict) -> bool:
    """
    Tek trade'i kontrol et. Kapandıysa True döner.
    """
    trade_id  = t["id"]
    symbol    = t["symbol"]
    direction = t["direction"]
    status    = t["status"]
    entry     = t["entry"]
    sl        = t["sl"]
    tp1       = t["tp1"]
    tp2       = t["tp2"]
    trail     = t.get("trail_stop")
    qty       = t["qty"]
    qty_tp1   = t.get("qty_tp1") or qty * TP1_CLOSE_PCT / 100
    qty_tp2   = t.get("qty_tp2") or qty * TP2_CLOSE_PCT / 100
    qty_runner= t.get("qty_runner") or qty - qty_tp1 - qty_tp2
    atr       = 0   # Trail için atr hesaplanacak

    price = _get_price(client, symbol)
    if not price:
        return False

    is_long = direction == "LONG"

    # ── SL Kontrolü ─────────────────────────────────────────────────────────
    sl_hit = (is_long and price <= sl) or (not is_long and price >= sl)
    if sl_hit:
        pnl = _calc_pnl(direction, entry, price, qty)
        _finalize(trade_id, price, pnl, "sl", t)
        return True

    # ── TP1 Kontrolü ────────────────────────────────────────────────────────
    if status == "open":
        tp1_hit = (is_long and price >= tp1) or (not is_long and price <= tp1)
        if tp1_hit:
            pnl_tp1 = _calc_pnl(direction, entry, tp1, qty_tp1)
            update_trade(trade_id, {
                "status":       "tp1_hit",
                "tp1_hit":      1,
                "realized_pnl": pnl_tp1,
                # SL'yi break-even'e çek
                "sl": entry,
            })
            update_paper_balance(pnl_tp1)
            logger.info(f"[Execution] TP1 #{trade_id} {symbol} +{pnl_tp1:.3f}$")
            return False

    # ── TP2 Kontrolü ────────────────────────────────────────────────────────
    if status == "tp1_hit":
        tp2_hit = (is_long and price >= tp2) or (not is_long and price <= tp2)
        if tp2_hit:
            pnl_tp2 = _calc_pnl(direction, entry, tp2, qty_tp2)
            realized = (t.get("realized_pnl") or 0) + pnl_tp2
            # Runner'ı başlat — trail stop koy
            atr_val = _get_atr(client, symbol)
            if is_long:
                new_trail = tp2 - atr_val * TRAIL_ATR_MULT
            else:
                new_trail = tp2 + atr_val * TRAIL_ATR_MULT
            update_trade(trade_id, {
                "status":       "runner",
                "tp2_hit":      1,
                "realized_pnl": realized,
                "trail_stop":   new_trail,
                "sl":           entry,
            })
            update_paper_balance(pnl_tp2)
            logger.info(f"[Execution] TP2 #{trade_id} {symbol} +{pnl_tp2:.3f}$ → RUNNER trail={new_trail:.6f}")
            return False

    # ── Runner: Trailing Stop ────────────────────────────────────────────────
    if status == "runner":
        atr_val = _get_atr(client, symbol)
        if is_long:
            new_trail = price - atr_val * TRAIL_ATR_MULT
            trail = max(trail or entry, new_trail)
        else:
            new_trail = price + atr_val * TRAIL_ATR_MULT
            trail = min(trail or entry, new_trail)

        update_trade(trade_id, {"trail_stop": trail})

        trail_hit = (is_long and price <= trail) or (not is_long and price >= trail)
        if trail_hit:
            pnl_runner = _calc_pnl(direction, entry, price, qty_runner)
            realized   = (t.get("realized_pnl") or 0) + pnl_runner
            _finalize(trade_id, price, realized, "trail", t)
            return True

    # ── Max Hold Süresi (4 saat) ─────────────────────────────────────────────
    open_t = t.get("open_time")
    if open_t:
        try:
            opened = datetime.fromisoformat(open_t.replace("Z", "+00:00"))
            elapsed = (datetime.now(timezone.utc) - opened).total_seconds() / 60
            if elapsed > 240:
                pnl = _calc_pnl(direction, entry, price, qty)
                _finalize(trade_id, price, pnl, "timeout", t)
                return True
        except Exception:
            pass

    return False


# ─────────────────────────────────────────────────────────────────────────────
# YARDIMCILAR
# ─────────────────────────────────────────────────────────────────────────────

def _calc_pnl(direction: str, entry: float, exit_price: float, qty: float) -> float:
    """Paper trade PnL hesabı (USD, kaldıraçsız)."""
    if direction == "LONG":
        pnl = (exit_price - entry) * qty
    else:
        pnl = (entry - exit_price) * qty
    return round(pnl, 4)


def _get_atr(client, symbol: str, interval: str = "5m", period: int = 14) -> float:
    """Trailing stop için anlık ATR."""
    try:
        import pandas as pd
        klines = client.futures_klines(symbol=symbol, interval=interval, limit=period + 5)
        df = pd.DataFrame(klines, columns=[
            "time","open","high","low","close","volume",
            "ct","qav","nt","tbbav","tbqav","ignore"
        ])
        for col in ("high","low","close"):
            df[col] = df[col].astype(float)
        tr = pd.concat([
            df["high"] - df["low"],
            (df["high"] - df["close"].shift()).abs(),
            (df["low"]  - df["close"].shift()).abs(),
        ], axis=1).max(axis=1)
        return float(tr.rolling(period).mean().iloc[-1])
    except Exception:
        return float(df["close"].iloc[-1]) * 0.005 if "df" in dir() else 0.01


def _finalize(trade_id: int, close_price: float, net_pnl: float,
              reason: str, t: dict):
    """Trade'i kapat, bakiyeyi güncelle."""
    open_t = t.get("open_time", "")
    try:
        opened   = datetime.fromisoformat(open_t.replace("Z", "+00:00"))
        hold_min = (datetime.now(timezone.utc) - opened).total_seconds() / 60
    except Exception:
        hold_min = 0

    db_close_trade(trade_id, close_price, net_pnl, reason, hold_min)
    update_paper_balance(net_pnl - (t.get("realized_pnl") or 0))

    result = "WIN" if net_pnl > 0 else "LOSS"
    logger.info(
        f"[Execution] KAPANDI #{trade_id} {t['symbol']} {t['direction']} "
        f"{reason.upper()} pnl={net_pnl:+.3f}$ hold={hold_min:.0f}dk"
    )
