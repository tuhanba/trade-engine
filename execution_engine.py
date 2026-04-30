"""
execution_engine.py — AX Paper Execution Motoru
=================================================
Trade State:
  open → tp1_hit → runner → closed

Sorumluluklar:
  - Paper trade aç (qty hesapla, TP1/TP2/runner böl)
  - Her tick'te SL ve TP takip et
  - Trailing stop uygula (runner fazında)
  - Trade kapat (DB güncelle, bakiye güncelle)
  - R-multiple hesapla

ÖNEMLİ DÜZELTME:
  TP1 kısmi kapanışından sonra SL/timeout PnL hesabı YALNIZCA
  kalan pozisyon büyüklüğü üzerinden yapılır. Aksi hâlde TP1
  kârı çifte sayılarak bakiye yanlış güncellenir.
"""

import math
import time
import logging
from datetime import datetime, timezone

from config import (
    RISK_PCT, TP1_CLOSE_PCT, TP2_CLOSE_PCT, RUNNER_CLOSE_PCT,
    TRAIL_ATR_MULT, EXECUTION_MODE, MAX_HOLD_MINUTES,
)
from database import (
    save_trade, update_trade, close_trade as db_close_trade,
    get_open_trades, update_paper_balance, get_paper_balance, save_postmortem,
)

logger = logging.getLogger(__name__)


# ─────────────────────────────────────────────────────────────────────────────
# EXCHANGE INFO CACHE — futures_exchange_info ağır bir çağrıdır; 1 saat cache
# ─────────────────────────────────────────────────────────────────────────────

_filters_cache: dict = {}
_filters_ts: float = 0
_FILTERS_TTL = 3600  # saniye


def _get_filters(client, symbol: str) -> dict:
    """LOT_SIZE ve PRICE_FILTER filtrelerini Binance'ten al. 1 saatlik cache."""
    global _filters_cache, _filters_ts
    now = time.time()
    if _filters_cache and now - _filters_ts < _FILTERS_TTL and symbol in _filters_cache:
        return _filters_cache[symbol]
    try:
        info = client.futures_exchange_info()
        _filters_ts = now
        for s in info["symbols"]:
            sym = s["symbol"]
            fmap = {f["filterType"]: f for f in s["filters"]}
            lot     = fmap.get("LOT_SIZE", {})
            price_f = fmap.get("PRICE_FILTER", {})
            _filters_cache[sym] = {
                "step_size":    float(lot.get("stepSize",   "0.001")),
                "min_qty":      float(lot.get("minQty",     "0.001")),
                "tick_size":    float(price_f.get("tickSize", "0.01")),
                "min_notional": 5.0,
            }
    except Exception as e:
        logger.warning(f"[Execution] Filtre alınamadı {symbol}: {e}")
    return _filters_cache.get(symbol,
           {"step_size": 0.001, "min_qty": 0.001, "tick_size": 0.01, "min_notional": 5.0})


# ─────────────────────────────────────────────────────────────────────────────
# YARDIMCI
# ─────────────────────────────────────────────────────────────────────────────

def _floor(val: float, step: float) -> float:
    if step <= 0:
        return val
    prec = len(str(step).rstrip("0").split(".")[-1]) if "." in str(step) else 0
    return round(math.floor(val / step) * step, prec)


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
    if qty * entry < min_notional:
        qty = _floor(min_notional / entry * 1.01, step_size)
    return round(qty, 8)


def _calc_pnl(direction: str, entry: float, exit_price: float, qty: float) -> float:
    """Paper trade PnL hesabı (USD, kaldıraçsız)."""
    if direction == "LONG":
        pnl = (exit_price - entry) * qty
    else:
        pnl = (entry - exit_price) * qty
    return round(pnl, 4)


def _remaining_qty(t: dict) -> float:
    """
    Status'a göre kalan pozisyon büyüklüğü.
    TP1 kapandıktan sonra SL/timeout hesabı yalnızca bu qty üzerinden yapılır.
    """
    status    = t.get("status", "open")
    qty       = t["qty"]
    qty_tp1   = t.get("qty_tp1") or qty * TP1_CLOSE_PCT / 100
    qty_tp2   = t.get("qty_tp2") or qty * TP2_CLOSE_PCT / 100
    qty_runner = t.get("qty_runner") or (qty - qty_tp1 - qty_tp2)

    if status == "open":
        return qty
    elif status == "tp1_hit":
        return qty_tp2 + qty_runner
    elif status == "runner":
        return qty_runner
    return qty


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

    if not direction or not entry or not sl:
        return None

    balance = get_paper_balance()
    rp      = risk_pct or RISK_PCT
    filters = _get_filters(client, symbol)

    qty = _calc_qty(balance, entry, sl, rp,
                    filters["step_size"], filters["min_qty"], filters["min_notional"])
    if qty <= 0:
        logger.warning(f"[Execution] {symbol} qty hesplanamadı")
        return None

    qty_tp1    = round(qty * TP1_CLOSE_PCT   / 100, 8)
    qty_tp2    = round(qty * TP2_CLOSE_PCT   / 100, 8)
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
    Tüm açık trade'leri kontrol et.

    Returns:
        Kapanan trade ID listesi.
    """
    trades = get_open_trades()
    closed = []
    for t in trades:
        try:
            if _check_trade(client, t):
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

    SL/Timeout PnL düzeltmesi:
      TP1 kapandıktan sonra kalan pozisyon = qty_tp2 + qty_runner.
      SL bu kalan miktar üzerinden hesaplanır.
      _finalize'e geçilen net_pnl = realized_pnl (önceki kısmi kârlar)
                                   + kalan pozisyon SL PnL'i
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
    qty_runner = t.get("qty_runner") or qty - qty_tp1 - qty_tp2
    realized  = t.get("realized_pnl") or 0

    price = _get_price(client, symbol)
    if not price:
        return False

    is_long = direction == "LONG"
    rem_qty = _remaining_qty(t)  # Kalan pozisyon büyüklüğü

    # ── SL Kontrolü ─────────────────────────────────────────────────────────
    sl_hit = (is_long and price <= sl) or (not is_long and price >= sl)
    if sl_hit:
        sl_pnl    = _calc_pnl(direction, entry, price, rem_qty)
        net_pnl   = realized + sl_pnl
        _finalize(trade_id, price, net_pnl, "sl", t)
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
                "sl":           entry,  # Break-even'e çek
            })
            update_paper_balance(pnl_tp1)
            logger.info(f"[Execution] TP1 #{trade_id} {symbol} +{pnl_tp1:.3f}$")
            return False

    # ── TP2 Kontrolü ────────────────────────────────────────────────────────
    if status == "tp1_hit":
        tp2_hit = (is_long and price >= tp2) or (not is_long and price <= tp2)
        if tp2_hit:
            pnl_tp2  = _calc_pnl(direction, entry, tp2, qty_tp2)
            new_real = realized + pnl_tp2
            atr_val  = _get_atr(client, symbol)
            new_trail = (tp2 - atr_val * TRAIL_ATR_MULT) if is_long else (tp2 + atr_val * TRAIL_ATR_MULT)
            update_trade(trade_id, {
                "status":       "runner",
                "tp2_hit":      1,
                "realized_pnl": new_real,
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
            trail     = max(trail or entry, new_trail)
        else:
            new_trail = price + atr_val * TRAIL_ATR_MULT
            trail     = min(trail or entry, new_trail)

        update_trade(trade_id, {"trail_stop": trail})

        trail_hit = (is_long and price <= trail) or (not is_long and price >= trail)
        if trail_hit:
            pnl_runner = _calc_pnl(direction, entry, price, qty_runner)
            net_pnl    = realized + pnl_runner
            _finalize(trade_id, price, net_pnl, "trail", t)
            return True

    # ── Max Hold Süresi ──────────────────────────────────────────────────────
    open_t = t.get("open_time")
    if open_t:
        try:
            opened  = datetime.fromisoformat(open_t.replace("Z", "+00:00"))
            elapsed = (datetime.now(timezone.utc) - opened).total_seconds() / 60
            if elapsed > MAX_HOLD_MINUTES:
                timeout_pnl = _calc_pnl(direction, entry, price, rem_qty)
                net_pnl     = realized + timeout_pnl
                _finalize(trade_id, price, net_pnl, "timeout", t)
                return True
        except Exception:
            pass

    return False


# ─────────────────────────────────────────────────────────────────────────────
# YARDIMCILAR
# ─────────────────────────────────────────────────────────────────────────────

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
        return 0.01


def _finalize(trade_id: int, close_price: float, net_pnl: float,
              reason: str, t: dict):
    """
    Trade'i kapat, bakiyeyi ve R-multiple'ı güncelle.

    net_pnl: Bu trade'in TOPLAM net kârı/zararı (realized_pnl dahil).
    Bakiye güncellemesi = net_pnl - already_credited_realized
    """
    already_realized = t.get("realized_pnl") or 0
    open_t = t.get("open_time", "")
    try:
        opened   = datetime.fromisoformat(open_t.replace("Z", "+00:00"))
        hold_min = (datetime.now(timezone.utc) - opened).total_seconds() / 60
    except Exception:
        hold_min = 0

    # R-multiple: net_pnl / (sl_dist * original_qty)
    entry   = t.get("entry", 0) or 0
    sl      = t.get("sl",    0) or 0
    qty     = t.get("qty",   1) or 1
    sl_dist = abs(entry - sl)
    r_mult  = round(net_pnl / (sl_dist * qty), 3) if sl_dist > 0 and qty > 0 else 0.0

    db_close_trade(trade_id, close_price, net_pnl, reason, hold_min)
    update_trade(trade_id, {"r_multiple": r_mult})

    # Bakiyeye sadece henüz kredilendirilmemiş kısmı ekle
    update_paper_balance(net_pnl - already_realized)

    result = "WIN" if net_pnl > 0 else "LOSS"
    logger.info(
        f"[Execution] KAPANDI #{trade_id} {t['symbol']} {t['direction']} "
        f"{reason.upper()} pnl={net_pnl:+.3f}$ R={r_mult:+.2f} hold={hold_min:.0f}dk"
    )
