"""
execution_engine.py — AX Paper Execution Motoru v4.3
=====================================================
Trade State:
  open -> tp1_hit -> runner -> closed_win/closed_loss/closed_trail
Sorumluluklar:
  - Paper trade ac (qty hesapla, TP1/TP2/runner bol)
  - Her mum/tick'te SL ve TP takip et
  - Trailing stop uygula (runner fazinda)
  - Trade kapat (DB guncelle, bakiye guncelle)
  - Binance fiyati: API key gerektirmeyen REST endpoint kullanir
  - PnL: unrealized_pnl DB'ye yazilir, dashboard okur
"""
import math
import logging
import requests as _req
from datetime import datetime, timezone
from config import (
    RISK_PCT, TP1_CLOSE_PCT, TP2_CLOSE_PCT, RUNNER_CLOSE_PCT,
    TRAIL_ATR_MULT, EXECUTION_MODE,
    BREAKEVEN_ENABLED, BREAKEVEN_OFFSET_PCT, PAPER_LEVERAGE,
)
from database import (
    save_trade, update_trade, close_trade as db_close_trade,
    get_open_trades, update_paper_balance, get_paper_balance, save_postmortem,
)
logger = logging.getLogger(__name__)

# Binance Futures REST base URL (API key gerektirmez)
_FAPI_BASE = "https://fapi.binance.com"

# ─────────────────────────────────────────────────────────────────────────────
# YARDIMCI
# ─────────────────────────────────────────────────────────────────────────────
def _floor(val, step):
    if step <= 0:
        return val
    prec = len(str(step).rstrip("0").split(".")[-1]) if "." in str(step) else 0
    return round(math.floor(val / step) * step, prec)


def _get_filters(client, symbol):
    """LOT_SIZE ve PRICE_FILTER filtrelerini Binance REST API ile al."""
    try:
        resp = _req.get(f"{_FAPI_BASE}/fapi/v1/exchangeInfo", timeout=10)
        if resp.status_code == 200:
            for s in resp.json().get("symbols", []):
                if s["symbol"] == symbol:
                    filters = {f["filterType"]: f for f in s["filters"]}
                    lot = filters.get("LOT_SIZE", {})
                    price_f = filters.get("PRICE_FILTER", {})
                    min_notional_f = filters.get("MIN_NOTIONAL", {})
                    return {
                        "step_size":    float(lot.get("stepSize", "0.001")),
                        "min_qty":      float(lot.get("minQty",   "0.001")),
                        "tick_size":    float(price_f.get("tickSize", "0.01")),
                        "min_notional": float(min_notional_f.get("notional", "5.0")),
                    }
    except Exception as e:
        logger.debug(f"[Execution] REST exchangeInfo hatasi {symbol}: {e}")
    # Fallback: client
    try:
        info = client.futures_exchange_info()
        for s in info["symbols"]:
            if s["symbol"] == symbol:
                filters = {f["filterType"]: f for f in s["filters"]}
                lot = filters.get("LOT_SIZE", {})
                price_f = filters.get("PRICE_FILTER", {})
                return {
                    "step_size":    float(lot.get("stepSize", "0.001")),
                    "min_qty":      float(lot.get("minQty",   "0.001")),
                    "tick_size":    float(price_f.get("tickSize", "0.01")),
                    "min_notional": 5.0,
                }
    except Exception as e:
        logger.warning(f"[Execution] Filtre alinamadi {symbol}: {e}")
    return {"step_size": 0.001, "min_qty": 0.001, "tick_size": 0.01, "min_notional": 5.0}


def _calc_qty(balance, entry, sl, risk_pct, step_size, min_qty, min_notional, leverage=1):
    """Kaldirach paper trade miktar hesabi.
    Ornek: 250$ bakiye, %1 risk, 10x kaldirach:
      risk_usd = 2.5$, pozisyon = 25$, qty = 25 / entry
    """
    risk_usd = balance * risk_pct / 100
    sl_dist  = abs(entry - sl)
    if sl_dist <= 0:
        return 0.0
    # Kaldirach ile qty: risk_usd * leverage / sl_dist
    qty = (risk_usd * leverage) / sl_dist
    qty = _floor(qty, step_size)
    qty = max(qty, min_qty)
    if qty * entry < min_notional:
        qty = _floor(min_notional / entry * 1.01 * leverage, step_size)
    return round(qty, 8)


def _calc_pnl(direction, entry, price, qty):
    """PnL hesabi. Kaldirach zaten qty icinde yansitilmistir.
    qty = risk_usd * leverage / sl_dist oldugu icin leverage burada tekrar uygulanmaz.
    """
    if direction == "LONG":
        return round((price - entry) * qty, 6)
    else:
        return round((entry - price) * qty, 6)


# ─────────────────────────────────────────────────────────────────────────────
# BINANCE FIYAT — API KEY GEREKTIRMEZ
# ─────────────────────────────────────────────────────────────────────────────
def _get_price(client, symbol):
    """
    Binance Futures fiyatini ceker.
    1. REST API (API key gerektirmez)
    2. Fallback: python-binance client
    3. Son fallback: spot ticker
    """
    try:
        resp = _req.get(
            f"{_FAPI_BASE}/fapi/v1/ticker/price",
            params={"symbol": symbol},
            timeout=5
        )
        if resp.status_code == 200:
            price = float(resp.json().get("price", 0))
            if price > 0:
                return price
    except Exception as e:
        logger.debug(f"[Execution] REST fiyat hatasi {symbol}: {e}")

    try:
        ticker = client.futures_ticker(symbol=symbol)
        return float(ticker["lastPrice"])
    except Exception as e:
        logger.debug(f"[Execution] Client futures_ticker hatasi {symbol}: {e}")

    try:
        resp = _req.get(
            "https://api.binance.com/api/v3/ticker/price",
            params={"symbol": symbol},
            timeout=5
        )
        if resp.status_code == 200:
            return float(resp.json().get("price", 0))
    except Exception:
        pass

    return 0.0


def _get_atr(client, symbol, interval="5m", period=14):
    """Trailing stop icin anlik ATR. REST API ile ceker."""
    df = None
    try:
        import pandas as pd
        resp = _req.get(
            f"{_FAPI_BASE}/fapi/v1/klines",
            params={"symbol": symbol, "interval": interval, "limit": period + 5},
            timeout=10
        )
        if resp.status_code == 200:
            klines = resp.json()
        else:
            klines = client.futures_klines(symbol=symbol, interval=interval, limit=period + 5)

        df = pd.DataFrame(klines, columns=[
            "time", "open", "high", "low", "close", "volume",
            "ct", "qav", "nt", "tbbav", "tbqav", "ignore"
        ])
        for col in ("high", "low", "close"):
            df[col] = df[col].astype(float)
        tr = pd.concat([
            df["high"] - df["low"],
            (df["high"] - df["close"].shift()).abs(),
            (df["low"]  - df["close"].shift()).abs(),
        ], axis=1).max(axis=1)
        atr_val = float(tr.rolling(period).mean().iloc[-1])
        return atr_val if atr_val > 0 else float(df["close"].iloc[-1]) * 0.005
    except Exception as e:
        logger.debug(f"[Execution] ATR hesaplama hatasi {symbol}: {e}")
        if df is not None:
            try:
                return float(df["close"].iloc[-1]) * 0.005
            except Exception:
                pass
        return 0.01


# ─────────────────────────────────────────────────────────────────────────────
# TRADE AC
# ─────────────────────────────────────────────────────────────────────────────
def open_trade(client, signal, ax_decision, risk_pct=None):
    """
    Paper trade ac.
    Returns:
        trade_id (int) ya da None (basarisiz)
    """
    symbol    = signal["symbol"]
    direction = signal["direction"]
    entry     = signal["entry"]
    sl        = signal["sl"]
    tp1       = signal["tp1"]
    tp2       = signal["tp2"]
    runner    = signal.get("runner_target", signal.get("tp3", tp2))
    if not direction or not entry or not sl:
        return None
    balance  = get_paper_balance()
    rp       = risk_pct or RISK_PCT
    filters  = _get_filters(client, symbol)
    lev = PAPER_LEVERAGE  # Simule kaldirach (default 10x)
    qty = _calc_qty(balance, entry, sl, rp,
                    filters["step_size"], filters["min_qty"], filters["min_notional"],
                    leverage=lev)
    if qty <= 0:
        logger.warning(f"[Execution] {symbol} qty hesaplanamadi")
        return None
    qty_tp1    = round(qty * TP1_CLOSE_PCT / 100, 8)
    qty_tp2    = round(qty * TP2_CLOSE_PCT / 100, 8)
    qty_runner = round(qty - qty_tp1 - qty_tp2, 8)
    trade = {
        "symbol":               symbol,
        "direction":            direction,
        "status":               "open",
        "environment":          EXECUTION_MODE,
        "ax_mode":              "execute",
        "entry":                entry,
        "sl":                   sl,
        "tp1":                  tp1,
        "tp2":                  tp2,
        "tp3":                  signal.get("tp3", runner),
        "runner_target":        runner,
        "trail_stop":           None,
        "qty":                  qty,
        "qty_tp1":              qty_tp1,
        "qty_tp2":              qty_tp2,
        "qty_runner":           qty_runner,
        "trade_stage":          "open",
        "active_target":        "tp1",
        "tp1_hit":              0,
        "tp2_hit":              0,
        "confidence":           signal.get("confidence", 0.8),
        "score":                ax_decision.get("final_score", signal.get("score", 0)),
        "setup_quality":        signal.get("setup_quality", "B"),
        "risk_percent":         rp,
        "leverage":             lev,
        "position_size":        qty * entry / lev,  # Gercek teminat
        "notional_size":        qty * entry,         # Kaldirach dahil pozisyon
        "linked_candidate_id":  None,
        "linked_candidate_uuid": signal.get("candidate_id"),
        "open_time":            datetime.now(timezone.utc).isoformat(),
    }
    trade_id = save_trade(trade)
    logger.info(
        f"[Execution] ACILDI #{trade_id} {symbol} {direction} "
        f"entry={entry:.6f} sl={sl:.6f} tp1={tp1:.6f} tp2={tp2:.6f} qty={qty}"
    )
    try:
        from live_tracker import record_open
        record_open(trade_id, symbol, direction, entry)
    except Exception as e:
        logger.debug(f"live_tracker.record_open hatasi: {e}")
    try:
        from n8n_bridge import notify_trade_open
        notify_trade_open({**trade, "id": trade_id})
    except Exception as e:
        logger.debug(f"n8n notify_trade_open hatasi: {e}")
    return trade_id


# ─────────────────────────────────────────────────────────────────────────────
# TRADE MONITORU
# ─────────────────────────────────────────────────────────────────────────────
def monitor_open_trades(client):
    """Tum acik tradeleri kontrol et. Kapanan trade ID'lerini doner."""
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


def _check_trade(client, t):
    """
    Tek trade'i kontrol et. Kapandiysa True doner.
    Ayrica unrealized_pnl'i DB'ye yazar (dashboard icin).
    """
    trade_id   = t["id"]
    symbol     = t["symbol"]
    direction  = t["direction"]
    status     = t["status"]
    entry      = t["entry"]
    sl         = t["sl"]
    tp1        = t["tp1"]
    tp2        = t["tp2"]
    trail      = t.get("trail_stop")
    qty        = t.get("qty") or 0
    qty_tp1    = t.get("qty_tp1") or (qty * TP1_CLOSE_PCT / 100)
    qty_tp2    = t.get("qty_tp2") or (qty * TP2_CLOSE_PCT / 100)
    qty_runner = t.get("qty_runner") or (qty - qty_tp1 - qty_tp2)

    price = _get_price(client, symbol)
    if not price:
        logger.debug(f"[Execution] {symbol} fiyat alinamadi, atlaniyor")
        return False

    is_long = direction == "LONG"

    # Unrealized PnL hesapla ve DB'ye yaz (dashboard icin)
    if qty > 0:
        unrealized = _calc_pnl(direction, entry, price, qty)
        try:
            update_trade(trade_id, {
                "current_price":  round(price, 6),
                "unrealized_pnl": round(unrealized, 4),
            })
        except Exception as e:
            logger.debug(f"unrealized_pnl yazma hatasi: {e}")

    # ── SL Kontrolu ─────────────────────────────────────────────────────────
    sl_hit = (is_long and price <= sl) or (not is_long and price >= sl)
    if sl_hit:
        pnl      = _calc_pnl(direction, entry, price, qty)
        realized = t.get("realized_pnl") or 0
        net_pnl  = realized + pnl
        _finalize(trade_id, price, net_pnl, "sl", t)
        return True

    # ── TP1 Kontrolu ────────────────────────────────────────────────────────
    if status == "open":
        tp1_hit = (is_long and price >= tp1) or (not is_long and price <= tp1)
        if tp1_hit:
            pnl_tp1 = _calc_pnl(direction, entry, tp1, qty_tp1)
            try:
                be_enabled = t.get("breakeven_enabled", BREAKEVEN_ENABLED)
                be_sl      = t.get("breakeven_sl")
                if be_sl is None:
                    offset = entry * (BREAKEVEN_OFFSET_PCT / 100)
                    be_sl  = (entry + offset) if is_long else (entry - offset)
            except Exception:
                be_enabled = True
                offset = entry * 0.0005
                be_sl  = (entry + offset) if is_long else (entry - offset)
            new_sl = be_sl if be_enabled else entry
            update_trade(trade_id, {
                "status":        "tp1_hit",
                "tp1_hit":       1,
                "realized_pnl":  pnl_tp1,
                "sl":            round(new_sl, 6),
                "trade_stage":   "tp1_hit",
                "active_target": "tp2",
            })
            update_paper_balance(pnl_tp1)
            logger.info(
                f"[Execution] TP1 #{trade_id} {symbol} +{pnl_tp1:.3f}$ "
                f"| Breakeven SL -> {new_sl:.6f}"
            )
            try:
                from telegram_delivery import send_message as tg_send
                tg_send(
                    f"\U0001f3af <b>TP1 HIT</b>\n"
                    f"{symbol} {direction} | +{pnl_tp1:.3f}$\n"
                    f"Breakeven SL \u2192 {new_sl:.6f} | Runner devam ediyor"
                )
            except Exception as _tg_e:
                logger.warning(f"TP1 Telegram bildirimi hatasi: {_tg_e}")
            return False

    # ── TP2 Kontrolu ────────────────────────────────────────────────────────
    if status == "tp1_hit":
        tp2_hit = (is_long and price >= tp2) or (not is_long and price <= tp2)
        if tp2_hit:
            pnl_tp2  = _calc_pnl(direction, entry, tp2, qty_tp2)
            realized = (t.get("realized_pnl") or 0) + pnl_tp2
            atr_val  = _get_atr(client, symbol)
            if is_long:
                new_trail = tp2 - atr_val * TRAIL_ATR_MULT
            else:
                new_trail = tp2 + atr_val * TRAIL_ATR_MULT
            update_trade(trade_id, {
                "status":        "runner",
                "tp2_hit":       1,
                "realized_pnl":  realized,
                "trail_stop":    new_trail,
                "sl":            entry,
                "trade_stage":   "runner",
                "active_target": "runner",
            })
            update_paper_balance(pnl_tp2)
            logger.info(f"[Execution] TP2 #{trade_id} {symbol} +{pnl_tp2:.3f}$ -> RUNNER trail={new_trail:.6f}")
            try:
                from telegram_delivery import send_message as tg_send
                tg_send(
                    f"\U0001f3af <b>TP2 HIT \u2014 RUNNER BASLADI</b>\n"
                    f"{symbol} {direction} | +{pnl_tp2:.3f}$\n"
                    f"Trail Stop: {new_trail:.6f}"
                )
            except Exception as _tg_e:
                logger.warning(f"TP2 Telegram bildirimi hatasi: {_tg_e}")
            return False

    # ── Runner / Trailing Stop Kontrolu ─────────────────────────────────────
    if status == "runner":
        atr_val = _get_atr(client, symbol)
        if is_long:
            new_trail = price - atr_val * TRAIL_ATR_MULT
            if trail is None or new_trail > trail:
                trail = new_trail
                update_trade(trade_id, {"trail_stop": round(trail, 6)})
        else:
            new_trail = price + atr_val * TRAIL_ATR_MULT
            if trail is None or new_trail < trail:
                trail = new_trail
                update_trade(trade_id, {"trail_stop": round(trail, 6)})

        trail_hit = trail and ((is_long and price <= trail) or (not is_long and price >= trail))
        if trail_hit:
            pnl_runner = _calc_pnl(direction, entry, price, qty_runner)
            realized   = (t.get("realized_pnl") or 0) + pnl_runner
            _finalize(trade_id, price, realized, "trail", t)
            return True

        tp3 = t.get("tp3") or t.get("runner_target")
        if tp3:
            tp3_hit = (is_long and price >= tp3) or (not is_long and price <= tp3)
            if tp3_hit:
                pnl_runner = _calc_pnl(direction, entry, tp3, qty_runner)
                realized   = (t.get("realized_pnl") or 0) + pnl_runner
                _finalize(trade_id, tp3, realized, "tp3", t)
                return True

    return False


# ─────────────────────────────────────────────────────────────────────────────
# TRADE KAPAT
# ─────────────────────────────────────────────────────────────────────────────
def _finalize(trade_id, close_price, net_pnl, reason, t):
    """Trade'i kapat, bakiyeyi guncelle, tum servisleri bildir."""
    open_t = t.get("open_time", "")
    try:
        opened   = datetime.fromisoformat(open_t.replace("Z", "+00:00"))
        hold_min = (datetime.now(timezone.utc) - opened).total_seconds() / 60
    except Exception:
        hold_min = 0

    db_close_trade(trade_id, close_price, net_pnl, reason, hold_min)
    update_paper_balance(net_pnl - (t.get("realized_pnl") or 0))
    result = "WIN" if net_pnl > 0 else "LOSS"

    try:
        from live_tracker import record_close
        record_close(trade_id, close_price, reason)
    except Exception as e:
        logger.warning(f"live_tracker.record_close hatasi: {e}")

    try:
        from core.ai_decision_engine import AIDecisionEngine
        from config import DB_PATH
        ai_engine = AIDecisionEngine(db_path=DB_PATH)
        setup_quality = t.get("setup_quality") or t.get("quality") or "B"
        ai_engine.learn_from_trade(
            symbol        = t["symbol"],
            result        = result,
            pnl           = net_pnl,
            setup_quality = setup_quality,
        )
    except Exception as e:
        logger.warning(f"AI learn_from_trade hatasi: {e}")

    try:
        from coin_library import update_coin_stats
        from database import get_conn
        entry_p = t.get("entry", 0)
        sl_p    = t.get("sl", 0)
        sl_dist = abs(entry_p - sl_p) if sl_p else 1e-10
        r_mult  = round(net_pnl / (sl_dist * t.get("qty", 1) + 1e-10), 3)
        mfe_r_val = 0.0
        mae_r_val = 0.0
        try:
            with get_conn() as _conn:
                _pm = _conn.execute(
                    "SELECT mfe_r, mae_r FROM trade_postmortem WHERE trade_id=?",
                    (trade_id,)
                ).fetchone()
                if _pm:
                    mfe_r_val = float(_pm["mfe_r"] or 0)
                    mae_r_val = float(_pm["mae_r"] or 0)
        except Exception:
            pass
        if mfe_r_val == 0 and mae_r_val == 0 and sl_dist > 0:
            direction_val = (t.get("direction") or "").upper()
            if direction_val == "LONG":
                mfe_r_val = round(max(0, close_price - entry_p) / sl_dist, 3)
                mae_r_val = round(max(0, entry_p - close_price) / sl_dist, 3)
            elif direction_val == "SHORT":
                mfe_r_val = round(max(0, entry_p - close_price) / sl_dist, 3)
                mae_r_val = round(max(0, close_price - entry_p) / sl_dist, 3)
        update_coin_stats(
            symbol     = t["symbol"],
            result     = result,
            net_pnl    = net_pnl,
            r_multiple = r_mult,
            mfe_r      = mfe_r_val,
            mae_r      = mae_r_val,
            direction  = t.get("direction"),
        )
    except Exception as e:
        logger.warning(f"CoinLibrary update_coin_stats hatasi: {e}")

    try:
        import threading
        from ai_brain import post_trade_analysis
        threading.Thread(target=post_trade_analysis, args=(trade_id,), daemon=True).start()
    except Exception as e:
        logger.warning(f"AI Brain post_trade_analysis hatasi: {e}")

    try:
        from telegram_delivery import send_message as tg_send
        emoji = "\u2705" if net_pnl > 0 else "\U0001f534"
        tg_send(
            f"{emoji} <b>Trade Kapandi</b>\n"
            f"{t.get('symbol','')} {t.get('direction','')} | {reason.upper()}\n"
            f"PnL: <b>{net_pnl:+.3f}$</b> | Sure: {hold_min:.0f}dk"
        )
    except Exception as _tg_e:
        logger.warning(f"Trade kapanis Telegram bildirimi hatasi: {_tg_e}")

    try:
        from n8n_bridge import notify_trade_close
        notify_trade_close(trade={**t, "current_price": close_price}, pnl=net_pnl, reason=reason)
    except Exception as _n8n_e:
        logger.debug(f"n8n trade_close bildirimi: {_n8n_e}")

    logger.info(
        f"[Execution] KAPANDI #{trade_id} {t['symbol']} {t['direction']} "
        f"{reason.upper()} pnl={net_pnl:+.3f}$ hold={hold_min:.0f}dk"
    )
