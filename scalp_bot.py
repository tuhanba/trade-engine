"""
scalp_bot.py — AX koordinatör döngüsü

Sadece orkestrasyon yapar, iş mantığı modüllerde:
  market_scan -> signal_engine -> ai_brain -> execution_engine
  -> coin_library -> database
"""

import logging
import os
import time
from datetime import datetime, timezone, timedelta
from logging.handlers import RotatingFileHandler

import config
from database import get_conn, init_db
from market_scan import scan_market
from signal_engine import scan_signals
from ai_brain import evaluate
from execution_engine import open_trade, check_trade
from coin_library import (
    seed_initial_profiles, update_coin_profile, log_coin_memory,
)
try:
    import telegram_manager as _tg
    _TG = True
except Exception:
    _TG = False

# ---------- Logging ----------
_log_file = os.path.join(config.LOG_DIR, "ax_bot.log")
_rot_handler = RotatingFileHandler(
    _log_file, maxBytes=10 * 1024 * 1024, backupCount=7, encoding="utf-8"
)
_rot_handler.setFormatter(
    logging.Formatter("%(asctime)s [%(levelname)s] %(name)s - %(message)s")
)
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s - %(message)s",
    handlers=[logging.StreamHandler(), _rot_handler],
)
logger = logging.getLogger("scalp_bot")

LOOP_INTERVAL   = 30
HEALTH_INTERVAL = 60


# ---------- DB yardımcıları ----------

def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _get_state() -> dict:
    conn = get_conn()
    c = conn.cursor()
    c.execute("SELECT * FROM system_state WHERE id=1")
    row = c.fetchone()
    conn.close()
    return dict(row) if row else {}


def _set_state(**kwargs):
    sets = ", ".join(f"{k}=?" for k in kwargs)
    vals = list(kwargs.values()) + [_now(), 1]
    conn = get_conn()
    conn.execute(
        f"UPDATE system_state SET {sets}, updated_at=? WHERE id=?", vals
    )
    conn.commit()
    conn.close()


def _heartbeat():
    conn = get_conn()
    conn.execute(
        "UPDATE system_state SET last_heartbeat=? WHERE id=1", (_now(),)
    )
    conn.commit()
    conn.close()


def _write_candidate(candidate: dict, decision: dict) -> int:
    conn = get_conn()
    c = conn.cursor()
    c.execute("""
        INSERT INTO signal_candidates
            (symbol, direction, entry, sl, tp1, tp2, runner_target,
             rr, expected_mfe_r, score, confidence,
             decision, veto_reason, session,
             ax_mode, execution_mode, created_at)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
    """, (
        candidate["symbol"],
        candidate["direction"],
        candidate["entry"],
        candidate["sl"],
        candidate["tp1"],
        candidate["tp2"],
        candidate.get("runner_target"),
        candidate["rr"],
        candidate["expected_mfe_r"],
        decision["score"],
        decision["confidence"],
        decision["decision"],
        decision.get("veto_reason"),
        candidate.get("session"),
        config.AX_MODE,
        config.EXECUTION_MODE,
        _now(),
    ))
    cid = c.lastrowid
    conn.commit()
    conn.close()
    return cid


def _open_trades() -> list:
    conn = get_conn()
    c = conn.cursor()
    c.execute("""
        SELECT * FROM trades
        WHERE status NOT IN
            ('CLOSED_WIN','CLOSED_LOSS','CLOSED_MANUAL','CLOSED_TRAIL','WIN','LOSS')
    """)
    rows = [dict(r) for r in c.fetchall()]
    conn.close()
    return rows


def _get_trade(trade_id: int) -> dict | None:
    conn = get_conn()
    c = conn.cursor()
    c.execute("SELECT * FROM trades WHERE id=?", (trade_id,))
    row = c.fetchone()
    conn.close()
    return dict(row) if row else None


def _ticker_price(symbol: str) -> float:
    try:
        from market_scan import _get_client
        t = _get_client().futures_symbol_ticker(symbol=symbol)
        return float(t["price"])
    except Exception as e:
        logger.error(f"[Bot] ticker {symbol}: {e}")
        return 0.0


def _current_atr(symbol: str) -> float:
    try:
        from market_scan import _get_client
        from binance.client import Client
        from signal_engine import _atr
        klines = _get_client().futures_klines(
            symbol=symbol,
            interval=Client.KLINE_INTERVAL_15MINUTE,
            limit=20,
        )
        return _atr(klines, 14)
    except Exception:
        return 0.0


# ---------- Circuit breaker ----------

def _check_circuit_breaker():
    state = _get_state()
    if not state.get("circuit_breaker_active"):
        return
    until = state.get("circuit_breaker_until", "")
    if until and _now() >= until:
        _set_state(circuit_breaker_active=0, consecutive_losses=0)
        logger.info("[Bot] Circuit breaker sona erdi, sıfırlandı.")


def _record_result(result: str):
    state  = _get_state()
    losses = state.get("consecutive_losses", 0)
    losses = losses + 1 if result == "LOSS" else 0

    if losses >= config.CIRCUIT_BREAKER_LOSSES:
        until = (
            datetime.now(timezone.utc)
            + timedelta(minutes=config.CIRCUIT_BREAKER_MINUTES)
        ).isoformat()
        _set_state(
            consecutive_losses=losses,
            circuit_breaker_active=1,
            circuit_breaker_until=until,
        )
        logger.warning(
            f"[Bot] CIRCUIT BREAKER aktif — {config.CIRCUIT_BREAKER_MINUTES}dk"
        )
        if _TG:
            try:
                _tg.notify_circuit_breaker(until)
            except Exception:
                pass
    else:
        _set_state(consecutive_losses=losses)


# ---------- Post-trade ----------

def _run_postmortem(trade: dict):
    try:
        entry     = trade.get("entry", 0) or 0
        exit_p    = trade.get("exit_price") or entry
        sl        = trade.get("sl", entry) or entry
        tp2       = trade.get("tp2", entry) or entry
        direction = trade.get("direction", "LONG")

        sl_dist = abs(entry - sl) or 1e-10

        if direction == "LONG":
            mfe = max(0, exit_p - entry)
            mae = max(0, entry - sl)
        else:
            mfe = max(0, entry - exit_p)
            mae = max(0, exit_p - entry)

        mfe_r      = round(mfe / sl_dist, 4)
        best_tp    = round(abs(tp2 - entry) / sl_dist, 4)
        efficiency = round(mfe_r / best_tp, 4) if best_tp else 0
        missed     = round(best_tp - mfe_r, 4)

        conn = get_conn()
        conn.execute("""
            INSERT OR REPLACE INTO trade_postmortem
                (trade_id, symbol, direction,
                 mfe, mae, efficiency, missed_gain,
                 hold_minutes, best_possible_tp, exit_quality, created_at)
            VALUES (?,?,?,?,?,?,?,?,?,?,?)
        """, (
            trade["id"],
            trade.get("symbol"),
            direction,
            round(mfe, 6),
            round(mae, 6),
            efficiency,
            missed,
            trade.get("duration_min", 0),
            best_tp,
            efficiency,
            _now(),
        ))
        conn.commit()
        conn.close()
    except Exception as e:
        logger.error(f"[Bot] postmortem hata: {e}")


def _after_close(trade: dict):
    result = trade.get("result", "LOSS")
    _record_result(result)
    _run_postmortem(trade)

    update_coin_profile(trade["symbol"], {
        "result":     result,
        "r_multiple": trade.get("r_multiple", 0),
        "mfe":        0,
        "mae":        0,
        "direction":  trade.get("direction"),
    })
    log_coin_memory(
        symbol=trade["symbol"],
        session="UNKNOWN",
        market_regime="UNKNOWN",
        direction=trade.get("direction", ""),
        result=result,
        r_multiple=trade.get("r_multiple", 0),
        mfe=0,
        mae=0,
    )
    if _TG:
        try:
            _tg.notify_trade_close(trade)
        except Exception:
            pass
    logger.info(f"[Bot] Post-trade: #{trade['id']} {result}")


# ---------- Scan & signal ----------

def _scan_and_signal():
    state = _get_state()
    if state.get("bot_status") == "paused":
        logger.info("[Bot] Duraklatıldı.")
        return

    coins = scan_market()
    if not coins:
        return

    candidates = scan_signals(coins)
    if not candidates:
        return

    for candidate in candidates:
        decision = evaluate(candidate)
        candidate["ai_reason"] = decision["reason"]

        cid = _write_candidate(candidate, decision)
        candidate["linked_candidate_id"] = cid

        if decision["decision"] == "WATCH":
            if _TG:
                try:
                    _tg.notify_signal_alert(candidate, decision)
                except Exception:
                    pass
            continue

        if decision["decision"] != "ALLOW":
            logger.debug(
                f"[Bot] {candidate['symbol']} "
                f"{decision['decision']} — {decision.get('veto_reason')}"
            )
            continue

        if _TG:
            try:
                _tg.notify_signal_alert(candidate, decision)
            except Exception:
                pass

        tid = open_trade(candidate)
        if tid:
            conn = get_conn()
            conn.execute(
                "UPDATE signal_candidates SET linked_trade_id=? WHERE id=?",
                (tid, cid),
            )
            conn.commit()
            conn.close()
            if _TG:
                try:
                    t = _get_trade(tid)
                    if t:
                        _tg.notify_trade_open(t, candidate)
                except Exception:
                    pass


# ---------- Trade takip ----------

def _track_open_trades():
    trades = _open_trades()
    for trade in trades:
        price = _ticker_price(trade["symbol"])
        if price <= 0:
            continue

        old_status = trade["status"]
        atr        = _current_atr(trade["symbol"])
        new_status = check_trade(trade["id"], price, atr)

        closed = {
            "CLOSED_WIN", "CLOSED_LOSS",
            "CLOSED_MANUAL", "CLOSED_TRAIL",
        }

        # TP1 hit bildirimi
        if _TG and new_status == "TP1_HIT" and old_status == "OPEN":
            fresh = _get_trade(trade["id"])
            if fresh:
                try:
                    partial = float(fresh.get("realized_pnl") or 0)
                    _tg.notify_tp_hit(trade["id"], "TP1", price, partial)
                except Exception:
                    pass

        # TP2 hit bildirimi
        elif _TG and new_status == "TP2_HIT" and old_status == "TP1_HIT":
            fresh = _get_trade(trade["id"])
            if fresh:
                try:
                    partial = float(fresh.get("realized_pnl") or 0)
                    _tg.notify_tp_hit(trade["id"], "TP2", price, partial)
                except Exception:
                    pass

        if new_status in closed and old_status not in closed:
            fresh = _get_trade(trade["id"])
            if fresh:
                _after_close(fresh)


# ---------- Ana döngü ----------

def run():
    logger.info("=" * 55)
    logger.info(
        f"AX Bot — mode={config.AX_MODE} env={config.EXECUTION_MODE}"
    )
    logger.info("=" * 55)

    init_db()
    seed_initial_profiles()
    if _TG:
        try:
            _tg.start_polling()
        except Exception as e:
            logger.warning(f"[Bot] Telegram başlatılamadı: {e}")
    _set_state(
        bot_status="running",
        ax_mode=config.AX_MODE,
        execution_mode=config.EXECUTION_MODE,
    )

    last_health = time.time()

    while True:
        try:
            _check_circuit_breaker()
            _scan_and_signal()
            _track_open_trades()

            if time.time() - last_health >= HEALTH_INTERVAL:
                _heartbeat()
                last_health = time.time()

        except KeyboardInterrupt:
            logger.info("[Bot] Durduruldu.")
            _set_state(bot_status="stopped")
            break
        except Exception as e:
            logger.error(f"[Bot] Döngü hatası: {e}", exc_info=True)

        time.sleep(LOOP_INTERVAL)


if __name__ == "__main__":
    run()
