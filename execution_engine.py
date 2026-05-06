"""
execution_engine.py — AX Paper Execution Motoru v5.0 (LIVE-READY)
=================================================================
Trade Lifecycle:
  open → tp1_hit → tp2_hit/runner → closed

KURALLAR:
- Local _calc_qty / _calc_pnl YOK. Tümü core/accounting.py üzerinden.
- TP1 sadece qty_tp1 kapatır. TP2 sadece qty_tp2 kapatır.
- Final/SL/timeout/trailing sadece remaining_qty kapatır.
- partial_closes ve balance_ledger doğru yazılır.
- trades.net_pnl = TP1 + TP2 + runner/final net toplamı.
- duration_seconds = close_time - open_time (saniye).
- LIVE_TRADING_ENABLED default False. DRY_RUN default True.
"""
import math
import logging
from datetime import datetime, timezone

from config import (
    RISK_PCT, TP1_CLOSE_PCT, TP2_CLOSE_PCT, RUNNER_CLOSE_PCT,
    TRAIL_ATR_MULT, EXECUTION_MODE, MAX_HOLD_MINUTES,
    BREAKEVEN_ENABLED, BREAKEVEN_OFFSET_PCT,
    LIVE_TRADING_ENABLED, DRY_RUN, DEFAULT_FEE_RATE,
    MAX_OPEN_TRADES, MAX_CONSECUTIVE_LOSSES, COIN_COOLDOWN_MINUTES,
    DAILY_MAX_LOSS_PCT, MAX_CORRELATED_TRADES,
)
from core.accounting import (
    calculate_pnl, calculate_fee, calculate_notional_and_margin,
    calculate_position_size, calculate_partial_close_pnl,
    calculate_runner_unrealized_pnl, calculate_close_pnl,
    calculate_r_multiple, calculate_max_loss_after_fee,
    calculate_margin_loss_pct, validate_trade_risk,
)
from database import (
    save_trade, update_trade, close_trade as db_close_trade,
    get_open_trades, get_paper_balance, add_ledger_entry,
    save_partial_close, get_stats, get_trade_by_id,
    save_trade_event,
)

logger = logging.getLogger(__name__)


# ─────────────────────────────────────────────────────────────────────────────
# YARDIMCI
# ─────────────────────────────────────────────────────────────────────────────

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
                    "step_size":    float(lot.get("stepSize", "0.001")),
                    "min_qty":      float(lot.get("minQty", "0.001")),
                    "tick_size":    float(price_f.get("tickSize", "0.01")),
                    "min_notional": float(filters.get("MIN_NOTIONAL", {}).get("notional", "5.0")),
                }
    except Exception as e:
        logger.warning(f"[Execution] Filtre alınamadı {symbol}: {e}")
    return {"step_size": 0.001, "min_qty": 0.001, "tick_size": 0.01, "min_notional": 5.0}


# ─────────────────────────────────────────────────────────────────────────────
# RISK KONTROLLERI
# ─────────────────────────────────────────────────────────────────────────────

def _check_daily_loss(balance: float) -> bool:
    """Günlük kayıp limiti kontrolü."""
    try:
        from database import get_conn
        with get_conn() as conn:
            today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
            row = conn.execute(
                "SELECT SUM(net_pnl) FROM trades WHERE DATE(close_time)=? AND status='closed'",
                (today,)
            ).fetchone()
            daily_loss = abs(min(0, row[0] or 0))
            max_loss = balance * (DAILY_MAX_LOSS_PCT / 100)
            return daily_loss < max_loss
    except Exception:
        return True


def _check_consecutive_losses() -> bool:
    """Ardışık kayıp kontrolü."""
    try:
        from database import get_conn
        with get_conn() as conn:
            rows = conn.execute(
                "SELECT net_pnl FROM trades WHERE status='closed' ORDER BY id DESC LIMIT ?",
                (MAX_CONSECUTIVE_LOSSES,)
            ).fetchall()
            if len(rows) < MAX_CONSECUTIVE_LOSSES:
                return True
            return not all(r[0] <= 0 for r in rows)
    except Exception:
        return True


def _check_coin_cooldown(symbol: str) -> bool:
    """Coin cooldown kontrolü."""
    try:
        from database import get_conn
        with get_conn() as conn:
            row = conn.execute("""
                SELECT close_time FROM trades
                WHERE symbol=? AND status='closed'
                ORDER BY id DESC LIMIT 1
            """, (symbol,)).fetchone()
            if not row or not row[0]:
                return True
            closed = datetime.fromisoformat(str(row[0]).replace("Z", "+00:00"))
            if closed.tzinfo is None:
                closed = closed.replace(tzinfo=timezone.utc)
            elapsed = (datetime.now(timezone.utc) - closed).total_seconds() / 60
            return elapsed >= COIN_COOLDOWN_MINUTES
    except Exception:
        return True


# ─────────────────────────────────────────────────────────────────────────────
# TRADE AÇ
# ─────────────────────────────────────────────────────────────────────────────

def open_trade(client, signal: dict, ax_decision: dict = None,
               risk_pct: float = None) -> int | None:
    """
    Paper trade aç. Tüm hesaplamalar core/accounting.py üzerinden.
    """
    symbol    = signal["symbol"]
    direction = signal["direction"]
    entry     = signal["entry"]
    sl        = signal["sl"]
    tp1       = signal["tp1"]
    tp2       = signal.get("tp2", tp1 * 1.02 if direction == "LONG" else tp1 * 0.98)
    tp3       = signal.get("tp3", signal.get("runner_target", tp2 * 1.01 if direction == "LONG" else tp2 * 0.99))
    leverage  = signal.get("leverage", 10)

    if not direction or not entry or not sl:
        return None

    # Risk kontrolleri
    open_trades = get_open_trades()
    if len(open_trades) >= MAX_OPEN_TRADES:
        logger.warning(f"[Execution] Max açık trade limiti: {MAX_OPEN_TRADES}")
        return None

    # Aynı coin kontrolü
    if any(t["symbol"] == symbol for t in open_trades):
        logger.warning(f"[Execution] {symbol} zaten açık trade var")
        return None

    balance = get_paper_balance()

    if not _check_daily_loss(balance):
        logger.warning("[Execution] Günlük kayıp limiti aşıldı")
        return None

    if not _check_consecutive_losses():
        logger.warning("[Execution] Ardışık kayıp limiti aşıldı")
        return None

    if not _check_coin_cooldown(symbol):
        logger.warning(f"[Execution] {symbol} cooldown süresi dolmadı")
        return None

    # Trade risk doğrulama
    rp = risk_pct or RISK_PCT
    is_safe, reason = validate_trade_risk(balance, entry, sl, leverage, rp, DEFAULT_FEE_RATE)
    if not is_safe:
        logger.warning(f"[Execution] Risk red: {reason}")
        return None

    # Pozisyon büyüklüğü hesapla
    filters = _get_filters(client, symbol)
    pos = calculate_position_size(balance, rp, entry, sl, leverage, DEFAULT_FEE_RATE, filters)
    if not pos.get("valid", False):
        logger.warning(f"[Execution] Pozisyon hesaplanamadı: {pos.get('reason')}")
        return None

    now = datetime.now(timezone.utc).isoformat()

    trade = {
        "symbol":           symbol,
        "direction":        direction,
        "status":           "open",
        "entry":            entry,
        "sl":               sl,
        "tp1":              tp1,
        "tp2":              tp2,
        "tp3":              tp3,
        "original_qty":     pos["qty"],
        "remaining_qty":    pos["qty"],
        "qty_tp1":          pos["qty_tp1"],
        "qty_tp2":          pos["qty_tp2"],
        "qty_runner":       pos["qty_runner"],
        "leverage":         leverage,
        "notional_size":    pos["notional_size"],
        "margin_used":      pos["margin_used"],
        "risk_pct":         rp,
        "risk_usd":         pos["risk_usd"],
        "max_loss_after_fee": pos["max_loss_after_fee"],
        "open_fee":         pos["open_fee"],
        "fee_rate":         DEFAULT_FEE_RATE,
        "total_fee":        pos["open_fee"],
        "setup_quality":    signal.get("setup_quality", signal.get("quality")),
        "final_score":      signal.get("final_score", signal.get("score")),
        "market_regime":    signal.get("market_regime"),
        "open_time":        now,
    }

    trade_id = save_trade(trade)

    # Ledger: open fee
    add_ledger_entry(trade_id, symbol, "OPEN_FEE", -pos["open_fee"],
                     f"Entry fee {symbol}")

    # Telegram açılış bildirimi
    try:
        from telegram_delivery import send_trade_open
        send_trade_open({
            **trade, "id": trade_id,
            "confidence": signal.get("confidence", 0.8),
            "reason": signal.get("reason", ""),
            "rr": signal.get("rr", 0),
        })
    except Exception as e:
        logger.warning(f"[Execution] Telegram open hatası: {e}")

    logger.info(
        f"[Execution] AÇILDI #{trade_id} {symbol} {direction} "
        f"entry={entry:.6f} sl={sl:.6f} qty={pos['qty']} risk=${pos['risk_usd']:.2f}"
    )
    return trade_id


# ─────────────────────────────────────────────────────────────────────────────
# TRADE MONİTÖRÜ
# ─────────────────────────────────────────────────────────────────────────────

def monitor_trades(client) -> list:
    """Tüm açık trade'leri kontrol et."""
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
    """Tek trade'i kontrol et. Kapandıysa True döner."""
    trade_id  = t["id"]
    symbol    = t["symbol"]
    direction = t["direction"]
    status    = t["status"]
    entry     = t["entry"]
    sl        = t["sl"]
    tp1       = t["tp1"]
    tp2       = t.get("tp2")
    fee_rate  = t.get("fee_rate", DEFAULT_FEE_RATE)

    qty_tp1    = t.get("qty_tp1") or 0
    qty_tp2    = t.get("qty_tp2") or 0
    remaining  = t.get("remaining_qty") or t.get("original_qty", 0)

    price = _get_price(client, symbol)
    if not price:
        return False

    is_long = direction == "LONG"

    # ── SL Kontrolü ──────────────────────────────────────────────────────
    sl_hit = (is_long and price <= sl) or (not is_long and price >= sl)
    if sl_hit:
        return _handle_final_close(trade_id, t, price, "SL", fee_rate)

    # ── TP1 Kontrolü ─────────────────────────────────────────────────────
    if status == "open" and qty_tp1 > 0:
        tp1_hit = (is_long and price >= tp1) or (not is_long and price <= tp1)
        if tp1_hit:
            return _handle_tp1(trade_id, t, tp1, fee_rate, is_long)

    # ── TP2 Kontrolü ─────────────────────────────────────────────────────
    if status == "tp1_hit" and qty_tp2 > 0 and tp2:
        tp2_hit = (is_long and price >= tp2) or (not is_long and price <= tp2)
        if tp2_hit:
            return _handle_tp2(client, trade_id, t, tp2, fee_rate, is_long)

    # ── Runner: Trailing Stop ────────────────────────────────────────────
    if status == "runner":
        return _handle_runner(client, trade_id, t, price, fee_rate, is_long)

    # ── Max Hold Süresi ──────────────────────────────────────────────────
    open_t = t.get("open_time")
    if open_t:
        try:
            opened = datetime.fromisoformat(str(open_t).replace("Z", "+00:00"))
            if opened.tzinfo is None:
                opened = opened.replace(tzinfo=timezone.utc)
            elapsed = (datetime.now(timezone.utc) - opened).total_seconds() / 60
            if elapsed > MAX_HOLD_MINUTES:
                return _handle_final_close(trade_id, t, price, "timeout", fee_rate)
        except Exception:
            pass

    return False


# ─────────────────────────────────────────────────────────────────────────────
# TP/SL/FINAL HANDLERS
# ─────────────────────────────────────────────────────────────────────────────

def _handle_tp1(trade_id, t, tp1_price, fee_rate, is_long):
    """TP1: sadece qty_tp1 kapatır."""
    symbol = t["symbol"]
    entry = t["entry"]
    qty_tp1 = t.get("qty_tp1", 0)
    if qty_tp1 <= 0:
        return False

    net_pnl, fee = calculate_partial_close_pnl(t["direction"], entry, tp1_price, qty_tp1, fee_rate)
    remaining = (t.get("remaining_qty") or t.get("original_qty", 0)) - qty_tp1

    # Breakeven SL
    try:
        offset = entry * (BREAKEVEN_OFFSET_PCT / 100)
        new_sl = (entry + offset) if is_long else (entry - offset)
    except Exception:
        new_sl = entry

    if not BREAKEVEN_ENABLED:
        new_sl = entry

    update_trade(trade_id, {
        "status":       "tp1_hit",
        "tp1_hit":      1,
        "realized_pnl": net_pnl,
        "total_fee":    (t.get("total_fee") or 0) + fee,
        "remaining_qty": remaining,
        "sl":           round(new_sl, 8),
    })

    # Partial close kaydı
    save_partial_close(trade_id, symbol, "TP1", qty_tp1, tp1_price, net_pnl, fee)

    # Ledger
    add_ledger_entry(trade_id, symbol, "TP1", net_pnl,
                     f"TP1 {symbol} qty={qty_tp1}")

    # Telegram
    try:
        from telegram_delivery import send_tp_hit
        send_tp_hit(symbol, 1, net_pnl, remaining)
    except Exception:
        pass

    logger.info(f"[Execution] TP1 #{trade_id} {symbol} +{net_pnl:.3f}$ BE→{new_sl:.6f}")
    return False


def _handle_tp2(client, trade_id, t, tp2_price, fee_rate, is_long):
    """TP2: sadece qty_tp2 kapatır, runner başlatır."""
    symbol = t["symbol"]
    entry = t["entry"]
    qty_tp2 = t.get("qty_tp2", 0)
    if qty_tp2 <= 0:
        return False

    net_pnl, fee = calculate_partial_close_pnl(t["direction"], entry, tp2_price, qty_tp2, fee_rate)
    realized = (t.get("realized_pnl") or 0) + net_pnl
    remaining = (t.get("remaining_qty") or 0) - qty_tp2

    # Trail stop koy
    atr_val = _get_atr(client, symbol)
    if is_long:
        trail = tp2_price - atr_val * TRAIL_ATR_MULT
    else:
        trail = tp2_price + atr_val * TRAIL_ATR_MULT

    update_trade(trade_id, {
        "status":       "runner",
        "tp2_hit":      1,
        "realized_pnl": realized,
        "total_fee":    (t.get("total_fee") or 0) + fee,
        "remaining_qty": remaining,
    })

    save_partial_close(trade_id, symbol, "TP2", qty_tp2, tp2_price, net_pnl, fee)
    add_ledger_entry(trade_id, symbol, "TP2", net_pnl,
                     f"TP2 {symbol} qty={qty_tp2}")

    try:
        from telegram_delivery import send_tp_hit
        send_tp_hit(symbol, 2, net_pnl, remaining)
    except Exception:
        pass

    logger.info(f"[Execution] TP2 #{trade_id} {symbol} +{net_pnl:.3f}$ → RUNNER trail={trail:.6f}")
    return False


def _handle_runner(client, trade_id, t, price, fee_rate, is_long):
    """Runner fazı: trailing stop güncelle, vurulursa kapat."""
    symbol = t["symbol"]
    entry = t["entry"]
    remaining = t.get("remaining_qty", 0)
    if remaining <= 0:
        return _handle_final_close(trade_id, t, price, "runner_empty", fee_rate)

    atr_val = _get_atr(client, symbol)
    trail = t.get("trail_stop") or t.get("sl") or entry

    if is_long:
        new_trail = price - atr_val * TRAIL_ATR_MULT
        trail = max(trail, new_trail)
    else:
        new_trail = price + atr_val * TRAIL_ATR_MULT
        trail = min(trail, new_trail)

    update_trade(trade_id, {"sl": trail})

    trail_hit = (is_long and price <= trail) or (not is_long and price >= trail)
    if trail_hit:
        return _handle_final_close(trade_id, t, price, "trail", fee_rate)

    return False


def _handle_final_close(trade_id, t, close_price, reason, fee_rate):
    """Final kapanış: remaining_qty için PnL hesapla, trade'i kapat."""
    symbol = t["symbol"]
    direction = t["direction"]
    entry = t["entry"]
    remaining = t.get("remaining_qty") or t.get("original_qty", 0)
    realized = t.get("realized_pnl") or 0
    risk_usd = t.get("risk_usd") or 0

    # Runner/final PnL
    if remaining > 0:
        runner_pnl, runner_fee = calculate_partial_close_pnl(
            direction, entry, close_price, remaining, fee_rate
        )
    else:
        runner_pnl, runner_fee = 0.0, 0.0

    # Toplam net PnL = TP1 + TP2 + runner
    total_net_pnl = calculate_close_pnl(realized, runner_pnl)
    total_fee = (t.get("total_fee") or 0) + runner_fee
    r_multiple = calculate_r_multiple(total_net_pnl, risk_usd)

    # DB kapat
    db_close_trade(trade_id, total_net_pnl, total_fee, reason, r_multiple)

    # Partial close kaydı
    if remaining > 0:
        save_partial_close(trade_id, symbol, reason.upper(), remaining,
                           close_price, runner_pnl, runner_fee)

    # Ledger: sadece runner/final PnL (TP1/TP2 zaten yazıldı)
    add_ledger_entry(trade_id, symbol, reason.upper(), runner_pnl,
                     f"{reason} {symbol} qty={remaining}")

    # Telegram kapanış
    try:
        open_t = t.get("open_time", "")
        opened = datetime.fromisoformat(str(open_t).replace("Z", "+00:00"))
        if opened.tzinfo is None:
            opened = opened.replace(tzinfo=timezone.utc)
        hold_sec = int((datetime.now(timezone.utc) - opened).total_seconds())
        hold_min = hold_sec / 60
        if hold_min >= 60:
            duration_str = f"{hold_min/60:.1f}sa"
        else:
            duration_str = f"{hold_min:.0f}dk"
    except Exception:
        duration_str = "?"
        hold_min = 0

    try:
        from telegram_delivery import send_trade_close
        balance = get_paper_balance()
        send_trade_close(symbol, total_net_pnl, total_fee, reason,
                         duration_str, direction=direction,
                         r_multiple=r_multiple, balance_after=balance)
    except Exception as e:
        logger.warning(f"[Execution] Telegram close hatası: {e}")

    # AI Öğrenme
    result = "WIN" if total_net_pnl > 0 else "LOSS"
    try:
        from core.ai_decision_engine import AIDecisionEngine
        from config import DB_PATH
        ai = AIDecisionEngine(db_path=DB_PATH)
        ai.learn_from_outcome(symbol, total_net_pnl, reason)
    except Exception as e:
        logger.warning(f"[Execution] AI learn hatası: {e}")

    logger.info(
        f"[Execution] KAPANDI #{trade_id} {symbol} {direction} "
        f"{reason.upper()} pnl={total_net_pnl:+.3f}$ R={r_multiple:.2f}"
    )
    return True


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
        try:
            return float(df["close"].iloc[-1]) * 0.005
        except Exception:
            return 0.01
