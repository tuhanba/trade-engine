"""
core/paper_tracker.py — AX Paper Tracker v5.0
==============================================
Ghost tracking: açılmayan sinyallerin sonuçlarını takip eder.
"""
from __future__ import annotations
import logging
from core.data_layer import SignalData
from core.accounting import calculate_realized_pnl
import database

logger = logging.getLogger("ax.paper_tracker")


def register_candidate(signal: SignalData, decision: str, reason: str = "") -> None:
    """Açılmayan sinyal adayını DB'ye kaydeder."""
    try:
        database.save_signal_candidate(signal, decision, reason)
        logger.info("Candidate: %s %s → %s", signal.symbol, signal.side, decision)
    except Exception as exc:
        logger.error("Candidate kayıt hatası: %s", exc)


def update_candidate_outcome(symbol: str, current_price: float) -> None:
    """
    Açılmayan adayların güncel durumunu günceller.
    TP/SL vurulmuşsa status güncellenir, ghost_pnl hesaplanır.
    """
    conn = database.get_connection()
    try:
        rows = conn.execute(
            """
            SELECT id, side, entry_price, stop_loss, tp1
            FROM signal_candidates
            WHERE symbol = ? AND status NOT IN ('TP_HIT','SL_HIT','RESOLVED')
            ORDER BY id DESC LIMIT 5
            """,
            (symbol,),
        ).fetchall()

        for row in rows:
            side = row["side"]
            entry = float(row["entry_price"] or 0)
            tp1 = float(row["tp1"] or 0)
            sl = float(row["stop_loss"] or 0)

            if entry <= 0:
                continue

            hit_tp = hit_sl = False
            if side == "LONG":
                hit_tp = current_price >= tp1 if tp1 > 0 else False
                hit_sl = current_price <= sl if sl > 0 else False
            else:
                hit_tp = current_price <= tp1 if tp1 > 0 else False
                hit_sl = current_price >= sl if sl > 0 else False

            if hit_tp or hit_sl:
                status = "TP_HIT" if hit_tp else "SL_HIT"
                exit_price = tp1 if hit_tp else sl
                # Ghost PnL hesapla (qty=1 normalize)
                ghost_pnl = calculate_realized_pnl(side, entry, exit_price, 1.0, 0.0)
                conn.execute(
                    "UPDATE signal_candidates SET status=?, ghost_pnl=? WHERE id=?",
                    (status, ghost_pnl, row["id"]),
                )
                logger.info("Ghost %s %s → %s (pnl=%.4f)", symbol, side, status, ghost_pnl)

        conn.commit()
    except Exception as exc:
        logger.error("Candidate outcome güncelleme hatası: %s", exc)
    finally:
        conn.close()


def summarize_ghost_results() -> dict:
    """Ghost sonuç özeti."""
    conn = database.get_connection()
    try:
        total = conn.execute("SELECT COUNT(*) FROM signal_candidates").fetchone()[0]
        tp_hits = conn.execute("SELECT COUNT(*) FROM signal_candidates WHERE status='TP_HIT'").fetchone()[0]
        sl_hits = conn.execute("SELECT COUNT(*) FROM signal_candidates WHERE status='SL_HIT'").fetchone()[0]
        pending = total - tp_hits - sl_hits
        ghost_pnl = conn.execute("SELECT COALESCE(SUM(ghost_pnl),0) FROM signal_candidates WHERE status IN ('TP_HIT','SL_HIT')").fetchone()[0]

        return {
            "total_candidates": total,
            "tp_hits": tp_hits,
            "sl_hits": sl_hits,
            "pending": pending,
            "ghost_pnl": round(float(ghost_pnl), 4),
            "ghost_winrate": round(tp_hits / (tp_hits + sl_hits) * 100, 1) if (tp_hits + sl_hits) > 0 else 0.0,
        }
<<<<<<< HEAD
    except Exception as exc:
        logger.error("Ghost summary hatası: %s", exc)
        return {"total_candidates": 0, "tp_hits": 0, "sl_hits": 0, "pending": 0, "ghost_pnl": 0.0, "ghost_winrate": 0.0}
    finally:
        conn.close()
=======

    sl_dist = abs(entry - sl) + 1e-12

    cutoff = start_ms + int(horizon_minutes * 60_000)
    filtered = [b for b in kl if float(b[0]) >= float(start_ms) and float(b[0]) <= cutoff]
    if not filtered:
        filtered = kl

    mfe_r = 0.0
    mae_r = 0.0
    first_touch = None
    ttm = 0.0
    hit_tp = 0
    hit_stop_first = 0

    for b in filtered:
        o, h, l = float(b[1]), float(b[2]), float(b[3])
        ts = float(b[0])
        touch = _resolve_bar(direction, h, l, sl, tp1)
        if direction == "LONG":
            mfe_r = max(mfe_r, 0.0, (h - entry) / sl_dist)
            mae_r = max(mae_r, 0.0, (entry - l) / sl_dist)
        else:
            mfe_r = max(mfe_r, 0.0, (entry - l) / sl_dist)
            mae_r = max(mae_r, 0.0, (h - entry) / sl_dist)

        if first_touch is None and touch == "stop":
            first_touch = "stop"
            hit_stop_first = 1
            elapsed = (ts - start_ms) / 60_000.0
            ttm = max(0.0, elapsed + 0.5)
            break
        if first_touch is None and touch == "tp1":
            first_touch = "tp1"
            hit_tp = 1
            elapsed = (ts - start_ms) / 60_000.0
            ttm = max(0.0, elapsed + 0.5)
            break

    if first_touch is None:
        first_touch = "neither_horizon"

    would_have_win = hit_tp == 1 and hit_stop_first == 0

    return {
        "first_touch": first_touch,
        "hit_tp": hit_tp,
        "hit_stop_first": hit_stop_first,
        "setup_worked": would_have_win,
        "would_have_won": would_have_win,
        "ttm": ttm,
        "mfe_r": round(mfe_r, 4),
        "mae_r": round(mae_r, 4),
    }


def finalize_paper_row(client, row: dict) -> bool:
    symbol = row["symbol"]
    direction = row.get("direction") or "LONG"
    entry = float(row["preview_entry"])
    sl = float(row["preview_sl"])
    tp1 = float(row["preview_tp1"])
    horizon = float(row.get("horizon_minutes") or 480.0)
    created = _parse_created_at(row.get("created_at"))
    start_ms = int(created.timestamp() * 1000)
    end_ms = start_ms + int(horizon * 60_000) + 120_000

    try:
        kl = client.futures_klines(
            symbol=symbol,
            interval="1m",
            startTime=start_ms,
            endTime=end_ms,
            limit=1500,
        )
    except Exception as e:
        logger.warning(f"[paper_tracker] kline hatası {symbol}: {e}")
        return False

    out = _simulate_path(kl, direction, entry, sl, tp1, start_ms, horizon)
    tracked_from = row.get("tracked_from") or "candidate"

    if tracked_from in ("candidate", "watchlist", "telegram_gap"):
        skip_dec = 0 if int(out["would_have_won"]) == 1 else 1
    else:
        skip_dec = 1

    if out["first_touch"] == "no_data":
        return False

    now = datetime.now(timezone.utc).isoformat()
    update_paper_result(
        row["id"],
        {
            "hit_tp": out["hit_tp"],
            "hit_stop_first": out["hit_stop_first"],
            "time_to_move_minutes": out["ttm"],
            "max_favorable_excursion": out["mfe_r"],
            "max_adverse_excursion": out["mae_r"],
            "setup_worked": out["setup_worked"],
            "would_have_won": out["would_have_won"],
            "first_touch": out["first_touch"],
            "skip_decision_correct": skip_dec,
            "status": "completed",
            "finalized_at": now,
        },
    )

    try:
        from core.ai_decision_engine import AIDecisionEngine

        AIDecisionEngine(db_path=DB_PATH).learn_from_paper_outcome(
            symbol=symbol,
            tracked_from=tracked_from,
            would_have_won=int(out["would_have_won"]),
            mfe_r=float(out["mfe_r"]),
            mae_r=float(out["mae_r"]),
            first_touch=out["first_touch"],
            skip_correct=int(skip_dec),
        )
    except Exception as e:
        logger.debug(f"[paper_tracker] AI paper learn atlandı: {e}")

    return True


def process_pending_paper_results(client, limit: int = 35) -> int:
    rows = get_pending_paper_results(limit=limit)
    done = 0
    for row in rows:
        try:
            if finalize_paper_row(client, row):
                done += 1
        except Exception as e:
            logger.warning(f"[paper_tracker] finalize hata {row.get('symbol')}: {e}")
    return done
>>>>>>> 0797c70b8640d2006e47a50d5580ffae4606199b
