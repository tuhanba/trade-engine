"""
execution_engine.py — Paper order açma, takip, kapatma.

Faz 1: EXECUTION_MODE=paper
- Gerçekçi fee + slippage simülasyonu
- TP1/TP2/runner parçalı çıkış
- Trailing stop (runner için)
- Tüm DB güncellemeleri burada
"""

import logging
from datetime import datetime, timezone
from typing import Optional

import config
from database import get_conn
from market_scan import round_qty, round_price, check_min_notional

logger = logging.getLogger(__name__)

# Binance futures taker fee
FEE_RATE    = 0.0004   # %0.04
SLIP_RATE   = 0.0005   # %0.05 simüle slippage


# ---------- Yardımcılar ----------

def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _apply_slippage(price: float, direction: str, is_entry: bool) -> float:
    """Alış → fiyat biraz yükselir, satış → fiyat biraz düşer."""
    if direction == "LONG":
        return price * (1 + SLIP_RATE) if is_entry else price * (1 - SLIP_RATE)
    else:
        return price * (1 - SLIP_RATE) if is_entry else price * (1 + SLIP_RATE)


def _fee(qty: float, price: float) -> float:
    return qty * price * FEE_RATE


def _paper_balance() -> float:
    conn = get_conn()
    c = conn.cursor()
    c.execute("SELECT paper_balance FROM paper_account WHERE id=1")
    row = c.fetchone()
    conn.close()
    return float(row[0]) if row else 250.0


def _update_balance(delta: float):
    conn = get_conn()
    conn.execute(
        "UPDATE paper_account SET paper_balance = paper_balance + ?, updated_at=? WHERE id=1",
        (round(delta, 6), _now())
    )
    conn.commit()
    conn.close()


def _get_trade(trade_id: int) -> Optional[dict]:
    conn = get_conn()
    c = conn.cursor()
    c.execute("SELECT * FROM trades WHERE id=?", (trade_id,))
    row = c.fetchone()
    conn.close()
    return dict(row) if row else None


# ---------- Trade aç ----------

def open_trade(candidate: dict) -> Optional[int]:
    """
    Paper trade aç. Başarılı olursa trade_id döndür, aksi hâlde None.
    candidate: signal_engine veya ai_brain'den gelen dict
    """
    symbol    = candidate["symbol"]
    direction = candidate["direction"]
    entry_raw = candidate["entry"]
    sl        = candidate["sl"]
    tp1       = candidate["tp1"]
    tp2       = candidate["tp2"]
    runner    = candidate.get("runner_target", tp2 * 1.5)

    # Slippage uygula
    entry = _apply_slippage(entry_raw, direction, is_entry=True)
    entry = round_price(symbol, entry)

    sl_dist = abs(entry - sl)
    if sl_dist <= 0:
        logger.warning(f"[Exec] {symbol} sl_dist=0, trade atlandı.")
        return None

    balance     = _paper_balance()
    risk_amount = balance * config.RISK_PCT / 100
    total_qty   = risk_amount / sl_dist

    # LOT_SIZE'a yuvarla
    total_qty = round_qty(symbol, total_qty)
    if total_qty <= 0:
        logger.warning(f"[Exec] {symbol} qty=0 sonuç, atlandı.")
        return None

    # Min notional kontrolü
    if not check_min_notional(symbol, total_qty, entry):
        logger.warning(f"[Exec] {symbol} min_notional geçilemiyor.")
        return None

    # Parçalı çıkış miktarları
    qty_tp1    = round_qty(symbol, total_qty * config.TP1_CLOSE_PCT / 100)
    qty_tp2    = round_qty(symbol, total_qty * config.TP2_CLOSE_PCT / 100)
    qty_runner = round_qty(symbol, total_qty - qty_tp1 - qty_tp2)

    if qty_runner < 0:
        qty_runner = 0.0

    # Entry fee
    entry_fee = _fee(total_qty, entry)

    conn = get_conn()
    c = conn.cursor()
    c.execute("""
        INSERT INTO trades
            (symbol, direction, entry, sl, tp1, tp2, runner_target,
             qty, qty_tp1, qty_tp2, qty_runner,
             realized_pnl, unrealized_pnl, net_pnl,
             risk_usdt, status, environment, ax_mode,
             setup_type, ai_reason, linked_candidate_id,
             open_time, params_version)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
    """, (
        symbol, direction,
        round(entry, 8), round(sl, 8),
        round(tp1, 8), round(tp2, 8), round(runner, 8),
        total_qty, qty_tp1, qty_tp2, qty_runner,
        round(-entry_fee, 6), 0.0, round(-entry_fee, 6),
        round(risk_amount, 4),
        "OPEN",
        config.EXECUTION_MODE,
        config.AX_MODE,
        candidate.get("setup_type"),
        candidate.get("ai_reason") or candidate.get("reason"),
        candidate.get("linked_candidate_id"),
        _now(), 1
    ))
    trade_id = c.lastrowid
    conn.commit()
    conn.close()

    logger.info(f"[Exec] OPEN #{trade_id} {symbol} {direction} "
                f"entry={entry} qty={total_qty} SL={sl} TP1={tp1} TP2={tp2}")
    return trade_id


# ---------- Trade güncelle (fiyat takibi) ----------

def check_trade(trade_id: int, current_price: float, atr: float = 0.0) -> str:
    """
    Mevcut fiyata göre trade durumunu güncelle.
    Döndürür: yeni status string'i
    """
    trade = _get_trade(trade_id)
    if not trade:
        return "NOT_FOUND"

    status    = trade["status"]
    if status in ("CLOSED_WIN", "CLOSED_LOSS", "CLOSED_MANUAL", "CLOSED_TRAIL"):
        return status

    direction  = trade["direction"]
    entry      = trade["entry"]
    sl         = trade["sl"]
    tp1        = trade["tp1"]
    tp2        = trade["tp2"]
    trail_stop = trade["trail_stop"]
    qty_tp1    = trade["qty_tp1"]
    qty_tp2    = trade["qty_tp2"]
    qty_runner = trade["qty_runner"]

    long = direction == "LONG"

    # ── SL kontrolü ──────────────────────────────────────────────────────────
    sl_hit = current_price <= sl if long else current_price >= sl
    if sl_hit:
        return _close_sl(trade, current_price)

    # ── TP1 kontrolü ─────────────────────────────────────────────────────────
    if status == "OPEN":
        tp1_hit = current_price >= tp1 if long else current_price <= tp1
        if tp1_hit and qty_tp1 > 0:
            return _hit_tp1(trade, current_price)

    # ── TP2 kontrolü ─────────────────────────────────────────────────────────
    if status == "TP1_HIT":
        tp2_hit = current_price >= tp2 if long else current_price <= tp2
        if tp2_hit and qty_tp2 > 0:
            return _hit_tp2(trade, current_price)

    # ── Trailing stop (runner) ────────────────────────────────────────────────
    if status in ("TP1_HIT", "TP2_HIT", "RUNNER_ACTIVE"):
        if atr > 0 and qty_runner > 0:
            _update_trail(trade, current_price, atr)
            trade = _get_trade(trade_id)   # tekrar oku
            trail_stop = trade["trail_stop"]

        if trail_stop:
            trail_hit = current_price <= trail_stop if long else current_price >= trail_stop
            if trail_hit and qty_runner > 0:
                return _close_trail(trade, current_price)

    return status


# ---------- Kısmi çıkış fonksiyonları ----------

def _pnl_for_qty(direction: str, entry: float, exit_price: float, qty: float) -> float:
    gross = (exit_price - entry) * qty if direction == "LONG" else (entry - exit_price) * qty
    fees  = _fee(qty, entry) + _fee(qty, exit_price)
    return round(gross - fees, 6)


def _hit_tp1(trade: dict, price: float) -> str:
    exit_p = _apply_slippage(price, trade["direction"], is_entry=False)
    exit_p = round_price(trade["symbol"], exit_p)
    pnl    = _pnl_for_qty(trade["direction"], trade["entry"], exit_p, trade["qty_tp1"])

    conn = get_conn()
    conn.execute("""
        UPDATE trades SET status='TP1_HIT',
            realized_pnl = realized_pnl + ?,
            net_pnl      = net_pnl + ?,
            trail_stop   = ?
        WHERE id=?
    """, (pnl, pnl, round(trade["entry"], 8), trade["id"]))
    conn.commit()
    conn.close()
    _update_balance(pnl)

    logger.info(f"[Exec] TP1 #{trade['id']} {trade['symbol']} exit={exit_p} pnl={pnl:+.4f}")
    return "TP1_HIT"


def _hit_tp2(trade: dict, price: float) -> str:
    exit_p = _apply_slippage(price, trade["direction"], is_entry=False)
    exit_p = round_price(trade["symbol"], exit_p)
    pnl    = _pnl_for_qty(trade["direction"], trade["entry"], exit_p, trade["qty_tp2"])

    conn = get_conn()
    conn.execute("""
        UPDATE trades SET status='TP2_HIT',
            realized_pnl = realized_pnl + ?,
            net_pnl      = net_pnl + ?
        WHERE id=?
    """, (pnl, pnl, trade["id"]))
    conn.commit()
    conn.close()
    _update_balance(pnl)

    logger.info(f"[Exec] TP2 #{trade['id']} {trade['symbol']} exit={exit_p} pnl={pnl:+.4f}")
    return "TP2_HIT"


def _close_sl(trade: dict, price: float) -> str:
    exit_p = _apply_slippage(price, trade["direction"], is_entry=False)
    exit_p = round_price(trade["symbol"], exit_p)

    # Kalan qty: tp1/tp2 zaten kapandıysa sadece runner kalır
    status = trade["status"]
    if status == "OPEN":
        remaining = trade["qty"]
    elif status == "TP1_HIT":
        remaining = trade["qty_tp2"] + trade["qty_runner"]
    else:
        remaining = trade["qty_runner"]

    pnl = _pnl_for_qty(trade["direction"], trade["entry"], exit_p, remaining)
    _finalize(trade["id"], "CLOSED_LOSS", exit_p, pnl)
    logger.info(f"[Exec] SL #{trade['id']} {trade['symbol']} exit={exit_p} pnl={pnl:+.4f}")
    return "CLOSED_LOSS"


def _close_trail(trade: dict, price: float) -> str:
    exit_p = _apply_slippage(price, trade["direction"], is_entry=False)
    exit_p = round_price(trade["symbol"], exit_p)
    pnl    = _pnl_for_qty(trade["direction"], trade["entry"], exit_p, trade["qty_runner"])
    _finalize(trade["id"], "CLOSED_TRAIL", exit_p, pnl)
    logger.info(f"[Exec] TRAIL #{trade['id']} {trade['symbol']} exit={exit_p} pnl={pnl:+.4f}")
    return "CLOSED_TRAIL"


def close_trade_manual(trade_id: int, current_price: float) -> str:
    """Manuel kapat (Telegram /finish komutu gibi)."""
    trade = _get_trade(trade_id)
    if not trade:
        return "NOT_FOUND"
    exit_p = round_price(trade["symbol"], current_price)
    status = trade["status"]
    if status == "OPEN":
        qty = trade["qty"]
    elif status == "TP1_HIT":
        qty = trade["qty_tp2"] + trade["qty_runner"]
    else:
        qty = trade["qty_runner"]
    pnl = _pnl_for_qty(trade["direction"], trade["entry"], exit_p, qty)
    _finalize(trade_id, "CLOSED_MANUAL", exit_p, pnl)
    return "CLOSED_MANUAL"


def _update_trail(trade: dict, current_price: float, atr: float):
    """Trailing stop'u sadece kazanç yönünde güncelle."""
    long = trade["direction"] == "LONG"
    new_trail = (current_price - atr * config.TRAIL_ATR_MULT if long
                 else current_price + atr * config.TRAIL_ATR_MULT)
    new_trail = round_price(trade["symbol"], new_trail)

    old_trail = trade["trail_stop"]
    if old_trail is None:
        update = True
    else:
        update = new_trail > old_trail if long else new_trail < old_trail

    if update:
        conn = get_conn()
        conn.execute(
            "UPDATE trades SET trail_stop=?, status='RUNNER_ACTIVE' WHERE id=?",
            (new_trail, trade["id"])
        )
        conn.commit()
        conn.close()


def _finalize(trade_id: int, final_status: str, exit_price: float, last_pnl: float):
    """Trade'i kapat, toplam net_pnl hesapla, bakiyeyi güncelle."""
    trade = _get_trade(trade_id)
    if not trade:
        return

    realized = (trade["realized_pnl"] or 0) + last_pnl
    sl_dist  = abs(trade["entry"] - trade["sl"])
    r_mult   = round(realized / (sl_dist * trade["qty"] + 1e-10), 4) if sl_dist else 0

    try:
        ot  = datetime.fromisoformat(trade["open_time"].replace("Z", "+00:00"))
        dur = (datetime.now(timezone.utc) - ot).total_seconds() / 60
    except Exception:
        dur = 0

    conn = get_conn()
    conn.execute("""
        UPDATE trades
        SET status=?, exit_price=?,
            realized_pnl=?, net_pnl=?,
            r_multiple=?, close_time=?, duration_min=?,
            result=?
        WHERE id=?
    """, (
        final_status, round(exit_price, 8),
        round(realized, 6), round(realized, 6),
        r_mult,
        _now(), round(dur, 1),
        "WIN" if realized > 0 else "LOSS",
        trade_id
    ))
    conn.commit()
    conn.close()
    _update_balance(last_pnl)

    logger.info(f"[Exec] CLOSED #{trade_id} status={final_status} "
                f"net={realized:+.4f} R={r_mult}")
