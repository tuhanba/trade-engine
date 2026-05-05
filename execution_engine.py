"""
execution_engine.py — AX Execution Engine v5.0 FINAL
=====================================================
- Crash-proof: her fonksiyon try/except ile sarılı
- Kaldiraca gore TP/SL: AI decide_tp_sl_ratios kullanir
- qty=0 guard: qty hesaplanamazsa trade acilmaz
- Timeout: 12 saat sonra zorla kapanir
- Monitor: 15 sn'de bir tum acik trade'leri kontrol eder
- Ogrenme: kapanista AI'a WIN/LOSS ogretilir
"""
import time
import logging
import requests
from datetime import datetime, timezone, timedelta
from config import (
    RISK_PCT, PAPER_LEVERAGE, MAX_LEVERAGE, MIN_LEVERAGE,
    TP1_CLOSE_PCT, TP2_CLOSE_PCT, RUNNER_CLOSE_PCT,
    TRAIL_ATR_MULT, BREAKEVEN_ENABLED, BREAKEVEN_OFFSET_PCT,
    TRADE_TIMEOUT_HOURS, ENABLE_LIVE_TRADING, DB_PATH
)
from database import (
    save_trade, update_trade, close_trade,
    update_paper_balance, save_postmortem,
    get_open_trades, get_paper_balance
)
import telegram_delivery as tg

logger = logging.getLogger(__name__)

# ─────────────────────────────────────────────────────────────────────────────
# YARDIMCI FONKSIYONLAR
# ─────────────────────────────────────────────────────────────────────────────

def _get_price(symbol: str, client=None) -> float:
    """Binance Futures REST ile fiyat cek (API key gerektirmez)."""
    try:
        r = requests.get(
            "https://fapi.binance.com/fapi/v1/ticker/price",
            params={"symbol": symbol}, timeout=5
        )
        if r.status_code == 200:
            p = float(r.json().get("price", 0))
            if p > 0:
                return p
    except Exception:
        pass
    # Fallback: python-binance client
    if client:
        try:
            t = client.futures_ticker(symbol=symbol)
            return float(t.get("lastPrice", 0))
        except Exception:
            pass
    return 0.0


def _get_atr(symbol: str, client=None, period: int = 14) -> float:
    """Binance Futures 15m kline'larindan ATR hesapla."""
    try:
        r = requests.get(
            "https://fapi.binance.com/fapi/v1/klines",
            params={"symbol": symbol, "interval": "15m", "limit": period + 5},
            timeout=8
        )
        if r.status_code != 200:
            return 0.0
        klines = r.json()
        if len(klines) < period:
            return 0.0
        trs = []
        for i in range(1, len(klines)):
            h = float(klines[i][2])
            l = float(klines[i][3])
            pc = float(klines[i - 1][4])
            trs.append(max(h - l, abs(h - pc), abs(l - pc)))
        return sum(trs[-period:]) / period
    except Exception:
        return 0.0


def _floor(value: float, step: float) -> float:
    """Lot buyuklugunu adima gore asagi yuvarla."""
    if step <= 0:
        return value
    import math
    return math.floor(value / step) * step


def _get_filters(symbol: str, client=None) -> dict:
    """Binance Futures exchange info'dan lot/price filtrelerini al."""
    defaults = {"lot_step": 0.001, "min_qty": 0.001, "price_tick": 0.0001}
    try:
        r = requests.get(
            "https://fapi.binance.com/fapi/v1/exchangeInfo",
            timeout=10
        )
        if r.status_code != 200:
            return defaults
        for s in r.json().get("symbols", []):
            if s["symbol"] == symbol:
                lot_step = 0.001
                min_qty  = 0.001
                price_tick = 0.0001
                for f in s.get("filters", []):
                    if f["filterType"] == "LOT_SIZE":
                        lot_step = float(f.get("stepSize", 0.001))
                        min_qty  = float(f.get("minQty",  0.001))
                    elif f["filterType"] == "PRICE_FILTER":
                        price_tick = float(f.get("tickSize", 0.0001))
                return {"lot_step": lot_step, "min_qty": min_qty, "price_tick": price_tick}
    except Exception:
        pass
    return defaults


def _calc_qty(balance: float, risk_pct: float, entry: float,
              sl: float, leverage: int) -> float:
    """
    Pozisyon buyuklugunu hesapla.
    risk_usd = bakiye * risk_pct / 100
    sl_dist  = abs(entry - sl)
    qty      = (risk_usd * leverage) / (sl_dist * leverage)
             = risk_usd / sl_dist   (kaldirach sadece pozisyon buyuklugunu arttirir)
    Gercekte: teminat = risk_usd, pozisyon = risk_usd * leverage / entry
    """
    if entry <= 0 or sl <= 0:
        return 0.0
    sl_dist = abs(entry - sl)
    if sl_dist < entry * 0.0005:   # SL cok yakin (<0.05%) → gecersiz
        return 0.0
    risk_usd = balance * (risk_pct / 100.0)
    # qty: kac adet coin alinir
    # Teminat = risk_usd, pozisyon = risk_usd * leverage
    # qty = pozisyon / entry
    qty_raw = (risk_usd * leverage) / entry
    return qty_raw


def _calc_pnl(entry: float, current: float, qty: float,
              direction: str, leverage: int = 1) -> float:
    """Unrealized/Realized PnL hesapla (kaldiraç dahil)."""
    if not (entry and current and qty):
        return 0.0
    lev = max(1, int(leverage or 1))
    if direction == "LONG":
        raw = (current - entry) * qty
    elif direction == "SHORT":
        raw = (entry - current) * qty
    else:
        return 0.0
    return round(raw * lev, 4)


def _get_leverage_tp_sl(leverage: int, entry: float, sl_raw: float,
                        direction: str) -> dict:
    """
    Kaldiraca gore TP/SL degerlerini hesapla.
    Matris: config.py'deki LEVERAGE_TP_SL_MATRIX
    """
    # Matris (inline - config import hatasi olmassin)
    if leverage >= 18:
        sl_mult, tp1_pct, tp2_pct, tp3_pct = 0.65, 1.0, 2.0, 3.5
    elif leverage >= 14:
        sl_mult, tp1_pct, tp2_pct, tp3_pct = 0.75, 1.5, 3.0, 5.0
    elif leverage >= 10:
        sl_mult, tp1_pct, tp2_pct, tp3_pct = 0.90, 2.5, 4.5, 7.5
    elif leverage >= 7:
        sl_mult, tp1_pct, tp2_pct, tp3_pct = 1.10, 3.5, 6.0, 10.0
    else:
        sl_mult, tp1_pct, tp2_pct, tp3_pct = 1.30, 4.5, 7.5, 12.0

    sl_dist = abs(entry - sl_raw)
    new_sl_dist = sl_dist * sl_mult

    if direction == "LONG":
        sl  = entry - new_sl_dist
        tp1 = entry * (1 + tp1_pct / 100)
        tp2 = entry * (1 + tp2_pct / 100)
        tp3 = entry * (1 + tp3_pct / 100)
    else:
        sl  = entry + new_sl_dist
        tp1 = entry * (1 - tp1_pct / 100)
        tp2 = entry * (1 - tp2_pct / 100)
        tp3 = entry * (1 - tp3_pct / 100)

    rr = (tp1_pct / (new_sl_dist / entry * 100)) if entry > 0 else 2.0

    return {
        "sl": round(sl, 8), "tp1": round(tp1, 8),
        "tp2": round(tp2, 8), "tp3": round(tp3, 8),
        "sl_mult": sl_mult, "tp1_pct": tp1_pct,
        "tp2_pct": tp2_pct, "tp3_pct": tp3_pct,
        "rr": round(rr, 2)
    }


# ─────────────────────────────────────────────────────────────────────────────
# TRADE ACMA
# ─────────────────────────────────────────────────────────────────────────────

def open_trade(signal, client=None, ai_engine=None) -> dict:
    """
    Yeni paper trade ac.
    signal: SignalCandidate nesnesi veya dict
    Returns: {"ok": True/False, "trade_id": int, "error": str}
    """
    try:
        # Signal alanlarini al
        symbol    = getattr(signal, "symbol",       None) or signal.get("symbol", "")
        direction = getattr(signal, "direction",    None) or signal.get("direction", "LONG")
        entry     = float(getattr(signal, "entry_zone",  0) or signal.get("entry", 0) or 0)
        sl_raw    = float(getattr(signal, "stop_loss",   0) or signal.get("sl", 0) or 0)
        quality   = getattr(signal, "setup_quality", "B") or signal.get("setup_quality", "B")
        score     = float(getattr(signal, "score", 70) or signal.get("score", 70) or 70)
        leverage  = int(getattr(signal, "leverage",  PAPER_LEVERAGE) or
                        signal.get("leverage", PAPER_LEVERAGE) or PAPER_LEVERAGE)

        if not symbol or entry <= 0:
            return {"ok": False, "error": "Gecersiz symbol veya entry"}

        # Leverage sinirla
        leverage = max(MIN_LEVERAGE, min(MAX_LEVERAGE, leverage))

        # Kaldiraca gore TP/SL hesapla
        if sl_raw <= 0:
            # SL yoksa varsayilan: LONG %2, SHORT %2
            sl_raw = entry * 0.98 if direction == "LONG" else entry * 1.02

        tp_sl = _get_leverage_tp_sl(leverage, entry, sl_raw, direction)
        sl  = tp_sl["sl"]
        tp1 = tp_sl["tp1"]
        tp2 = tp_sl["tp2"]
        tp3 = tp_sl["tp3"]
        rr  = tp_sl["rr"]

        # Bakiye al
        balance = get_paper_balance() or 250.0

        # Qty hesapla
        qty = _calc_qty(balance, RISK_PCT, entry, sl, leverage)
        if qty <= 0:
            logger.warning(f"[Execution] {symbol} qty=0, trade acilmadi (sl_dist cok kucuk)")
            return {"ok": False, "error": "qty=0: SL mesafesi cok kucuk"}

        # Lot filtresi uygula
        try:
            filters  = _get_filters(symbol, client)
            lot_step = filters["lot_step"]
            min_qty  = filters["min_qty"]
            qty      = _floor(qty, lot_step)
            if qty < min_qty:
                qty = min_qty
        except Exception:
            pass

        notional = qty * entry
        risk_usd = balance * RISK_PCT / 100.0

        # Gercek fiyati al (entry dogrulama)
        live_price = _get_price(symbol, client)
        if live_price > 0:
            slippage = abs(live_price - entry) / entry
            if slippage > 0.02:  # %2'den fazla slippage → entry'yi guncelle
                logger.warning(f"[Execution] {symbol} slippage {slippage:.1%}, entry guncellendi")
                entry = live_price
                tp_sl = _get_leverage_tp_sl(leverage, entry, sl_raw, direction)
                sl, tp1, tp2, tp3 = tp_sl["sl"], tp_sl["tp1"], tp_sl["tp2"], tp_sl["tp3"]

        # Trade kaydet
        trade = {
            "symbol":         symbol,
            "direction":      direction,
            "entry":          entry,
            "sl":             sl,
            "tp1":            tp1,
            "tp2":            tp2,
            "tp3":            tp3,
            "qty":            qty,
            "leverage":       leverage,
            "notional_size":  notional,
            "risk_usd":       risk_usd,
            "setup_quality":  quality,
            "score":          score,
            "trade_stage":    "open",
            "active_target":  "TP1",
            "realized_pnl":   0.0,
            "current_price":  entry,
            "unrealized_pnl": 0.0,
            "open_time":      datetime.now(timezone.utc).isoformat(),
            "status":         "open",
        }

        trade_id = save_trade(trade)
        if not trade_id:
            return {"ok": False, "error": "DB kayit hatasi"}

        trade["id"] = trade_id

        logger.info(
            f"[Execution] ACILDI #{trade_id} {symbol} {direction} {leverage}x | "
            f"entry={entry:.6f} sl={sl:.6f} tp1={tp1:.6f} tp2={tp2:.6f} tp3={tp3:.6f} | "
            f"qty={qty:.4f} notional={notional:.2f}$ risk={risk_usd:.2f}$"
        )

        # Telegram bildirimi
        try:
            tg.send_trade_open(trade)
        except Exception as e:
            logger.warning(f"[Execution] Telegram bildirimi hatasi: {e}")

        return {"ok": True, "trade_id": trade_id, "trade": trade}

    except Exception as e:
        logger.error(f"[Execution] open_trade hatasi: {e}", exc_info=True)
        return {"ok": False, "error": str(e)}


# ─────────────────────────────────────────────────────────────────────────────
# MONITOR DONGUSU
# ─────────────────────────────────────────────────────────────────────────────

def monitor_open_trades(client=None, ai_engine=None):
    """
    Tum acik trade'leri kontrol et.
    Her 15 sn'de bir cagirilmali.
    """
    try:
        trades = get_open_trades()
        if not trades:
            return

        closed_count = 0
        for t in trades:
            try:
                _check_trade(dict(t), client, ai_engine)
                closed_count_before = closed_count
            except Exception as e:
                logger.error(f"[Monitor] Trade #{t.get('id')} kontrol hatasi: {e}")

        logger.debug(f"[Monitor] {len(trades)} acik trade kontrol edildi")

    except Exception as e:
        logger.error(f"[Monitor] monitor_open_trades hatasi: {e}", exc_info=True)


def _check_trade(t: dict, client=None, ai_engine=None):
    """Tek bir trade'i kontrol et: SL/TP/Trail/Timeout."""
    trade_id  = t.get("id")
    symbol    = t.get("symbol", "")
    direction = (t.get("direction") or "LONG").upper()
    entry     = float(t.get("entry") or 0)
    sl        = float(t.get("sl") or 0)
    tp1       = float(t.get("tp1") or 0)
    tp2       = float(t.get("tp2") or 0)
    tp3       = float(t.get("tp3") or 0)
    qty       = float(t.get("qty") or 0)
    leverage  = int(t.get("leverage") or PAPER_LEVERAGE)
    stage     = t.get("trade_stage", "open")
    realized  = float(t.get("realized_pnl") or 0)
    open_time_str = t.get("open_time") or t.get("created_at") or ""

    if not symbol or entry <= 0:
        return

    # qty=0 ise notional'dan hesapla
    if qty <= 0 and entry > 0:
        notional = float(t.get("notional_size") or 0)
        if notional > 0:
            qty = notional / entry

    # Guncel fiyat al
    price = _get_price(symbol, client)
    if price <= 0:
        return

    # Unrealized PnL guncelle
    upnl = _calc_pnl(entry, price, qty, direction, leverage)
    update_trade(trade_id, {
        "current_price":  price,
        "unrealized_pnl": round(upnl, 4)
    })

    # ── Timeout Kontrolu ─────────────────────────────────────────────────────
    if open_time_str:
        try:
            if open_time_str.endswith("Z"):
                open_time_str = open_time_str[:-1] + "+00:00"
            open_dt = datetime.fromisoformat(open_time_str)
            if open_dt.tzinfo is None:
                open_dt = open_dt.replace(tzinfo=timezone.utc)
            elapsed_h = (datetime.now(timezone.utc) - open_dt).total_seconds() / 3600
            if elapsed_h >= TRADE_TIMEOUT_HOURS:
                logger.info(f"[Monitor] #{trade_id} {symbol} TIMEOUT ({elapsed_h:.1f}h)")
                _finalize(t, price, qty, "timeout", realized + upnl, ai_engine)
                return
        except Exception:
            pass

    # ── SL Kontrolu ──────────────────────────────────────────────────────────
    sl_hit = (direction == "LONG" and price <= sl) or \
             (direction == "SHORT" and price >= sl)
    if sl_hit:
        net_pnl = realized + upnl
        logger.info(f"[Monitor] #{trade_id} {symbol} SL HIT @ {price:.6f} pnl={net_pnl:.3f}$")
        _finalize(t, price, qty, "sl", net_pnl, ai_engine)
        return

    # ── TP1 Kontrolu ─────────────────────────────────────────────────────────
    if stage == "open":
        tp1_hit = (direction == "LONG" and price >= tp1) or \
                  (direction == "SHORT" and price <= tp1)
        if tp1_hit:
            partial_qty = qty * (TP1_CLOSE_PCT / 100)
            partial_pnl = _calc_pnl(entry, price, partial_qty, direction, leverage)
            new_realized = realized + partial_pnl
            # Breakeven SL
            new_sl = sl
            if BREAKEVEN_ENABLED:
                offset = entry * BREAKEVEN_OFFSET_PCT / 100
                new_sl = (entry + offset) if direction == "LONG" else (entry - offset)
            update_trade(trade_id, {
                "trade_stage":  "tp1_hit",
                "active_target": "TP2",
                "realized_pnl": round(new_realized, 4),
                "sl":           round(new_sl, 8),
                "qty":          round(qty - partial_qty, 6),
            })
            logger.info(f"[Monitor] #{trade_id} {symbol} TP1 HIT +{partial_pnl:.3f}$ breakeven_sl={new_sl:.6f}")
            try:
                tg.send_tp_hit(symbol, direction, 1, partial_pnl)
            except Exception:
                pass
            return

    # ── TP2 Kontrolu ─────────────────────────────────────────────────────────
    if stage == "tp1_hit":
        tp2_hit = (direction == "LONG" and price >= tp2) or \
                  (direction == "SHORT" and price <= tp2)
        if tp2_hit:
            partial_qty = qty * (TP2_CLOSE_PCT / 100)
            partial_pnl = _calc_pnl(entry, price, partial_qty, direction, leverage)
            new_realized = realized + partial_pnl
            # Trail stop baslat
            atr = _get_atr(symbol, client)
            trail_dist = (atr * TRAIL_ATR_MULT) if atr > 0 else (entry * 0.015)
            trail_stop = (price - trail_dist) if direction == "LONG" else (price + trail_dist)
            update_trade(trade_id, {
                "trade_stage":  "runner",
                "active_target": "TP3",
                "realized_pnl": round(new_realized, 4),
                "trail_stop":   round(trail_stop, 8),
                "qty":          round(qty - partial_qty, 6),
            })
            logger.info(f"[Monitor] #{trade_id} {symbol} TP2 HIT +{partial_pnl:.3f}$ runner trail={trail_stop:.6f}")
            try:
                tg.send_tp_hit(symbol, direction, 2, partial_pnl)
            except Exception:
                pass
            return

    # ── Runner: Trail Stop ve TP3 ─────────────────────────────────────────────
    if stage == "runner":
        trail = float(t.get("trail_stop") or 0)
        # Trail stop guncelle
        atr = _get_atr(symbol, client)
        trail_dist = (atr * TRAIL_ATR_MULT) if atr > 0 else (entry * 0.015)
        if direction == "LONG":
            new_trail = price - trail_dist
            if new_trail > trail:
                trail = new_trail
                update_trade(trade_id, {"trail_stop": round(trail, 8)})
        else:
            new_trail = price + trail_dist
            if new_trail < trail or trail == 0:
                trail = new_trail
                update_trade(trade_id, {"trail_stop": round(trail, 8)})

        # Trail hit?
        trail_hit = trail > 0 and (
            (direction == "LONG"  and price <= trail) or
            (direction == "SHORT" and price >= trail)
        )
        if trail_hit:
            net_pnl = realized + _calc_pnl(entry, price, qty, direction, leverage)
            logger.info(f"[Monitor] #{trade_id} {symbol} TRAIL HIT @ {price:.6f} pnl={net_pnl:.3f}$")
            _finalize(t, price, qty, "trail", net_pnl, ai_engine)
            return

        # TP3 hit?
        tp3_hit = (direction == "LONG" and price >= tp3) or \
                  (direction == "SHORT" and price <= tp3)
        if tp3_hit:
            net_pnl = realized + _calc_pnl(entry, price, qty, direction, leverage)
            logger.info(f"[Monitor] #{trade_id} {symbol} TP3 HIT @ {price:.6f} pnl={net_pnl:.3f}$")
            _finalize(t, price, qty, "tp3", net_pnl, ai_engine)
            return


def _finalize(t: dict, close_price: float, qty: float,
              reason: str, net_pnl: float, ai_engine=None):
    """Trade'i kapat, bakiyeyi guncelle, AI'a ogret."""
    trade_id  = t.get("id")
    symbol    = t.get("symbol", "")
    direction = t.get("direction", "LONG")
    entry     = float(t.get("entry") or 0)
    quality   = t.get("setup_quality", "B")
    open_time_str = t.get("open_time") or t.get("created_at") or ""

    # Hold suresi
    hold_minutes = 0
    try:
        if open_time_str:
            if open_time_str.endswith("Z"):
                open_time_str = open_time_str[:-1] + "+00:00"
            open_dt = datetime.fromisoformat(open_time_str)
            if open_dt.tzinfo is None:
                open_dt = open_dt.replace(tzinfo=timezone.utc)
            hold_minutes = int((datetime.now(timezone.utc) - open_dt).total_seconds() / 60)
    except Exception:
        pass

    # MFE/MAE (basit tahmin)
    mfe_r = max(0.0, net_pnl / (abs(entry - float(t.get("sl") or entry)) * qty + 1e-10))
    mae_r = 0.0

    # WIN/LOSS belirle (net PnL bazli)
    result = "WIN" if net_pnl > 0 else "LOSS"

    # DB kapat (result=WIN/LOSS, hold_minutes de yaziliyor)
    try:
        close_trade(trade_id, close_price, net_pnl, reason, hold_min=hold_minutes)
    except Exception as e:
        logger.error(f"[Finalize] close_trade hatasi: {e}")

    # Bakiye guncelle
    try:
        update_paper_balance(net_pnl)
    except Exception as e:
        logger.error(f"[Finalize] update_paper_balance hatasi: {e}")

    # Postmortem kaydet (dogru imza: trade_id, data dict)
    try:
        save_postmortem(trade_id, {
            "symbol":       symbol,
            "result":       result,
            "pnl":          net_pnl,
            "hold_minutes": hold_minutes,
            "mfe_r":        mfe_r,
            "mae_r":        mae_r,
            "close_reason": reason,
        })
    except Exception as e:
        logger.warning(f"[Finalize] save_postmortem hatasi: {e}")

    # AI'a ogret
    if ai_engine:
        try:
            ai_engine.learn_from_trade(
                symbol=symbol,
                result=result,
                pnl=net_pnl,
                setup_quality=quality,
                hold_minutes=hold_minutes,
                mfe_r=mfe_r,
                mae_r=mae_r,
                trade_id=trade_id
            )
        except Exception as e:
            logger.warning(f"[Finalize] AI learn hatasi: {e}")

    # Telegram bildirimi
    try:
        tg.send_trade_close(t, net_pnl, reason)
    except Exception as e:
        logger.warning(f"[Finalize] Telegram kapanma bildirimi hatasi: {e}")

    logger.info(
        f"[Execution] KAPANDI #{trade_id} {symbol} {direction} | "
        f"reason={reason} pnl={net_pnl:.3f}$ hold={hold_minutes}dk result={result}"
    )
