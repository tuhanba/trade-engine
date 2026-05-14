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
    except Exception as exc:
        logger.error("Ghost summary hatası: %s", exc)
        return {"total_candidates": 0, "tp_hits": 0, "sl_hits": 0, "pending": 0, "ghost_pnl": 0.0, "ghost_winrate": 0.0}
    finally:
        conn.close()
