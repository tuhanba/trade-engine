"""
dashboard_service.py — AX Dashboard Snapshot Service
=====================================================
Her 5 dakikada bir daily_summary + weekly_summary tablolarını günceller.
Scalp bot ile birlikte arka planda çalışır ya da bağımsız servis olarak.
"""

import logging
import threading
import time
from datetime import datetime, timezone, timedelta

from database import (
    get_conn, save_daily_summary, save_weekly_summary,
    get_paper_balance,
)

logger = logging.getLogger(__name__)

_UPDATE_INTERVAL = 300  # 5 dakika
_running = False
_thread: threading.Thread | None = None


# ─────────────────────────────────────────────────────────────────────────────
# DAILY ÖZET
# ─────────────────────────────────────────────────────────────────────────────

def compute_daily(date_str: str | None = None) -> dict | None:
    """Verilen tarih (YYYY-MM-DD) için günlük özeti hesapla ve kaydet."""
    if date_str is None:
        date_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    try:
        with get_conn() as conn:
            rows = conn.execute(
                """
                SELECT net_pnl, close_reason, entry, sl
                FROM trades
                WHERE DATE(close_time) = ? AND status IN ('closed_win','closed_loss','sl','trail','timeout','tp1_hit','runner','open','closed')
                  AND close_time IS NOT NULL
                """,
                (date_str,),
            ).fetchall()

            if not rows:
                rows = conn.execute(
                    """
                    SELECT net_pnl, close_reason, entry, sl
                    FROM trades
                    WHERE DATE(close_time) = ?
                      AND close_time IS NOT NULL
                    """,
                    (date_str,),
                ).fetchall()

        trade_count = len(rows)
        if trade_count == 0:
            return None

        pnls = [r[0] or 0 for r in rows]
        wins  = sum(1 for r in rows if (r[0] or 0) > 0)
        losses = trade_count - wins
        net_pnl = sum(pnls)
        win_rate = wins / trade_count if trade_count else 0

        # Max drawdown: en derin peak-to-trough
        running = 0
        peak = 0
        max_dd = 0
        for p in pnls:
            running += p
            if running > peak:
                peak = running
            dd = peak - running
            if dd > max_dd:
                max_dd = dd

        balance = get_paper_balance()

        data = {
            "date":        date_str,
            "trade_count": trade_count,
            "win_count":   wins,
            "loss_count":  losses,
            "win_rate":    round(win_rate, 4),
            "gross_pnl":   round(net_pnl, 4),
            "net_pnl":     round(net_pnl, 4),
            "avg_r":       0,
            "max_drawdown": round(max_dd, 4),
            "balance_eod": round(balance, 4),
        }
        save_daily_summary(data)
        return data
    except Exception as e:
        logger.error(f"[Dashboard] compute_daily hatası: {e}")
        return None


# ─────────────────────────────────────────────────────────────────────────────
# WEEKLY ÖZET
# ─────────────────────────────────────────────────────────────────────────────

def compute_weekly(week_start: str | None = None) -> dict | None:
    """
    Verilen haftanın başlangıç tarihi (YYYY-MM-DD, Pazartesi) için
    haftalık özeti hesapla.
    """
    if week_start is None:
        today = datetime.now(timezone.utc).date()
        week_start_date = today - timedelta(days=today.weekday())
        week_start = week_start_date.isoformat()

    week_start_date = datetime.fromisoformat(week_start).date()
    week_end_date = week_start_date + timedelta(days=6)

    try:
        with get_conn() as conn:
            rows = conn.execute(
                """
                SELECT DATE(close_time) as cdate, net_pnl
                FROM trades
                WHERE DATE(close_time) BETWEEN ? AND ?
                  AND close_time IS NOT NULL
                """,
                (week_start, week_end_date.isoformat()),
            ).fetchall()

        if not rows:
            return None

        trade_count = len(rows)
        pnls   = [r[1] or 0 for r in rows]
        wins   = sum(1 for p in pnls if p > 0)
        losses = trade_count - wins
        net_pnl = sum(pnls)
        win_rate = wins / trade_count if trade_count else 0

        # Best / worst day
        daily: dict[str, float] = {}
        for cdate, pnl in rows:
            daily[cdate] = daily.get(cdate, 0) + (pnl or 0)
        best_day  = max(daily, key=daily.get) if daily else None
        worst_day = min(daily, key=daily.get) if daily else None

        data = {
            "week_start":  week_start,
            "trade_count": trade_count,
            "win_count":   wins,
            "loss_count":  losses,
            "win_rate":    round(win_rate, 4),
            "net_pnl":     round(net_pnl, 4),
            "avg_r":       0,
            "best_day":    best_day,
            "worst_day":   worst_day,
        }
        save_weekly_summary(data)
        return data
    except Exception as e:
        logger.error(f"[Dashboard] compute_weekly hatası: {e}")
        return None


# ─────────────────────────────────────────────────────────────────────────────
# 30 GÜN TAKVİM
# ─────────────────────────────────────────────────────────────────────────────

def get_calendar_data(days: int = 30) -> list[dict]:
    """Son N günlük PnL verisi — dashboard calendar için."""
    try:
        with get_conn() as conn:
            rows = conn.execute(
                """
                SELECT date, net_pnl, trade_count, win_count, loss_count
                FROM daily_summary
                ORDER BY date DESC
                LIMIT ?
                """,
                (days,),
            ).fetchall()

        return [
            {
                "date":        r[0],
                "net_pnl":     round(r[1] or 0, 4),
                "trade_count": r[2] or 0,
                "win_count":   r[3] or 0,
                "loss_count":  r[4] or 0,
            }
            for r in reversed(rows)
        ]
    except Exception as e:
        logger.error(f"[Dashboard] get_calendar_data hatası: {e}")
        return []


def get_weekly_data(weeks: int = 8) -> list[dict]:
    """Son N haftanın özeti."""
    try:
        with get_conn() as conn:
            rows = conn.execute(
                """
                SELECT week_start, net_pnl, trade_count, win_count,
                       loss_count, win_rate, best_day, worst_day
                FROM weekly_summary
                ORDER BY week_start DESC
                LIMIT ?
                """,
                (weeks,),
            ).fetchall()
        return [
            {
                "week_start":  r[0],
                "net_pnl":     round(r[1] or 0, 4),
                "trade_count": r[2] or 0,
                "win_count":   r[3] or 0,
                "loss_count":  r[4] or 0,
                "win_rate":    round((r[5] or 0) * 100, 1),
                "best_day":    0,
                "worst_day":   0,
            }
            for r in reversed(rows)
        ]
    except Exception as e:
        logger.error(f"[Dashboard] get_weekly_data hatası: {e}")
        return []


# ─────────────────────────────────────────────────────────────────────────────
# AX STATUS
# ─────────────────────────────────────────────────────────────────────────────

def get_ax_status() -> dict:
    """AX sistemi anlık durumu."""
    try:
        from database import get_state, get_open_trades, get_paper_balance

        cb_until = get_state("circuit_breaker_until")
        cb_active = False
        cb_until_str = None
        if cb_until:
            try:
                cb_dt = datetime.fromisoformat(cb_until)
                if cb_dt > datetime.now(timezone.utc):
                    cb_active = True
                    cb_until_str = cb_until
            except Exception:
                pass

        open_trades = get_open_trades()
        balance = get_paper_balance()

        paused_val = get_state("paused")
        paused = paused_val == "1"

        mode = get_state("execution_mode") or "paper"

        with get_conn() as conn:
            today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
            row = conn.execute(
                "SELECT COUNT(*), SUM(net_pnl) FROM trades WHERE DATE(close_time)=? AND close_time IS NOT NULL",
                (today,),
            ).fetchone()
            today_trades = row[0] or 0
            today_pnl    = round(row[1] or 0, 4)

            row2 = conn.execute(
                "SELECT COUNT(*) FROM signal_candidates WHERE DATE(created_at)=?",
                (today,),
            ).fetchone()
            today_signals = row2[0] or 0

            row3 = conn.execute(
                "SELECT COUNT(*) FROM signal_candidates WHERE DATE(created_at)=? AND decision='ALLOW'",
                (today,),
            ).fetchone()
            today_allowed = row3[0] or 0

        # Bot heartbeat — son 2 dakika içinde kalp atışı yoksa offline say
        bot_hb = get_state("bot_heartbeat_at") or ""
        bot_running = False
        if bot_hb:
            try:
                hb_dt = datetime.fromisoformat(bot_hb.replace("Z", "+00:00"))
                if hb_dt.tzinfo is None:
                    hb_dt = hb_dt.replace(tzinfo=timezone.utc)
                bot_running = (datetime.now(timezone.utc) - hb_dt).total_seconds() < 120
            except Exception:
                pass

        from config import DRY_RUN, LIVE_TRADING_ENABLED
        paper_safety = (
            "SECURE" if mode == "paper" and DRY_RUN and not LIVE_TRADING_ENABLED else "RISK"
        )

        return {
            "circuit_breaker_active": cb_active,
            "circuit_breaker_until":  cb_until_str,
            "paused":                 paused,
            "mode":                   mode,
            "execution_mode":         mode,
            "open_trades":            len(open_trades),
            "balance":                round(balance, 4),
            "paper_balance":          round(balance, 4),
            "today_trades":           today_trades,
            "today_pnl":              today_pnl,
            "today_signals":          today_signals,
            "today_allowed":          today_allowed,
            "bot_running":            bot_running,
            "dry_run":                DRY_RUN,
            "live_trading":           LIVE_TRADING_ENABLED,
            "paper_safety_status":    paper_safety,
            "last_scan_time":         get_state("last_scan_time") or "-",
            "last_monitor_time":      get_state("last_trade_monitor_at") or "-",
        }
    except Exception as e:
        logger.error(f"[Dashboard] get_ax_status hatası: {e}")
        return {"error": str(e)}


# ─────────────────────────────────────────────────────────────────────────────
# SİNYAL KARTLARİ — Dashboard ve Telegram aynı kaynaktan beslenir
# ─────────────────────────────────────────────────────────────────────────────

_SCORE_COLS = (
    "final_score", "technical_score", "ml_score", "ai_score",
    "risk_score", "cold_start_score", "score_source", "score_confidence",
)

def _normalize_candidate_row(row: dict) -> dict:
    """DB satırını dashboard kartı için normalize eder."""
    from core.score_engine import normalize_signal_scores
    out = dict(row)
    # Canonical aliases
    out.setdefault("stop_loss", out.get("stop") or out.get("sl"))
    out.setdefault("tp3", out.get("runner_target") or out.get("tp3"))
    out.setdefault("risk_percent", out.get("risk_pct") or out.get("risk_percent"))
    out.setdefault("entry", out.get("entry_zone") or out.get("entry"))
    out.setdefault("setup_quality", out.get("quality") or out.get("setup_quality", "D"))
    normalize_signal_scores(out)
    return out


def get_signal_candidates(lifecycle_stage: str | None = None,
                          decision: str | None = None,
                          limit: int = 50) -> list[dict]:
    """
    signal_candidates tablosundan score alanları dahil sinyal kartlarını döner.
    lifecycle_stage: APPROVED_FOR_TELEGRAM, REJECTED, AI_CHECKED, vb.
    decision: ALLOW, VETO, WATCH
    """
    try:
        conditions = ["1=1"]
        params: list = []
        if lifecycle_stage:
            conditions.append("lifecycle_stage = ?")
            params.append(lifecycle_stage)
        if decision:
            conditions.append("decision = ?")
            params.append(decision)
        where = " AND ".join(conditions)

        with get_conn() as conn:
            rows = conn.execute(
                f"""
                SELECT id, signal_id, symbol, direction, entry, stop, tp1, tp2, tp3,
                       rr, risk_amount, position_size, notional, leverage_suggestion,
                       final_score, technical_score, ml_score, ai_score, risk_score,
                       cold_start_score, score_source, score_confidence,
                       quality, setup_quality, reason, decision, lifecycle_stage,
                       execution_status, created_at
                FROM signal_candidates
                WHERE {where}
                ORDER BY created_at DESC
                LIMIT ?
                """,
                (*params, limit),
            ).fetchall()

        return [_normalize_candidate_row(dict(r)) for r in rows]
    except Exception as e:
        logger.error(f"[Dashboard] get_signal_candidates hatası: {e}")
        return []


def get_active_signal_candidates(limit: int = 30) -> list[dict]:
    """Telegram'a gönderilmiş veya trade açılmış aktif adaylar."""
    try:
        with get_conn() as conn:
            rows = conn.execute(
                """
                SELECT id, signal_id, symbol, direction, entry, stop, tp1, tp2, tp3,
                       rr, risk_amount, position_size, notional, leverage_suggestion,
                       final_score, technical_score, ml_score, ai_score, risk_score,
                       cold_start_score, score_source, score_confidence,
                       quality, setup_quality, reason, decision, lifecycle_stage,
                       execution_status, created_at
                FROM signal_candidates
                WHERE lifecycle_stage IN (
                    'APPROVED_FOR_TELEGRAM','APPROVED_FOR_TRADE','OPENED','MANAGED'
                )
                ORDER BY created_at DESC
                LIMIT ?
                """,
                (limit,),
            ).fetchall()
        return [_normalize_candidate_row(dict(r)) for r in rows]
    except Exception as e:
        logger.error(f"[Dashboard] get_active_signal_candidates hatası: {e}")
        return []


def get_watchlist_candidates(limit: int = 30) -> list[dict]:
    """Watch/watchlist kararı almış adaylar."""
    try:
        with get_conn() as conn:
            rows = conn.execute(
                """
                SELECT id, signal_id, symbol, direction, entry, stop, tp1, tp2, tp3,
                       rr, risk_amount, leverage_suggestion,
                       final_score, technical_score, ml_score, ai_score, risk_score,
                       score_source, score_confidence, quality, setup_quality,
                       reason, decision, lifecycle_stage, created_at
                FROM signal_candidates
                WHERE decision = 'WATCH'
                  AND lifecycle_stage NOT IN ('REJECTED','CLOSED')
                ORDER BY created_at DESC
                LIMIT ?
                """,
                (limit,),
            ).fetchall()
        return [_normalize_candidate_row(dict(r)) for r in rows]
    except Exception as e:
        logger.error(f"[Dashboard] get_watchlist_candidates hatası: {e}")
        return []


def get_rejected_candidates(limit: int = 30) -> list[dict]:
    """Son reddedilmiş adaylar."""
    try:
        with get_conn() as conn:
            rows = conn.execute(
                """
                SELECT id, signal_id, symbol, direction, entry,
                       final_score, technical_score, ml_score, ai_score, risk_score,
                       score_source, quality, setup_quality,
                       reason, reject_reason, ai_veto_reason, decision,
                       lifecycle_stage, created_at
                FROM signal_candidates
                WHERE lifecycle_stage = 'REJECTED' OR decision = 'VETO'
                ORDER BY created_at DESC
                LIMIT ?
                """,
                (limit,),
            ).fetchall()
        return [_normalize_candidate_row(dict(r)) for r in rows]
    except Exception as e:
        logger.error(f"[Dashboard] get_rejected_candidates hatası: {e}")
        return []


def get_performance_summary(days: int = 30) -> dict:
    """
    Expectancy, profit_factor, avg_R, win_rate, max_drawdown_R özeti.
    Dashboard ve /performance/summary endpoint'i için.
    """
    try:
        since = (datetime.now(timezone.utc) - timedelta(days=days)).strftime("%Y-%m-%d")
        with get_conn() as conn:
            rows = conn.execute(
                """
                SELECT net_pnl, r_multiple, close_reason
                FROM trades
                WHERE DATE(close_time) >= ?
                  AND close_time IS NOT NULL
                """,
                (since,),
            ).fetchall()

        if not rows:
            return {"days": days, "trade_count": 0}

        pnls  = [r[0] or 0 for r in rows]
        rs    = [r[1] or 0 for r in rows]
        wins  = sum(1 for p in pnls if p > 0)
        total = len(pnls)
        losses = total - wins
        win_rate = wins / total if total else 0

        gross_win  = sum(p for p in pnls if p > 0)
        gross_loss = abs(sum(p for p in pnls if p < 0))
        profit_factor = gross_win / gross_loss if gross_loss > 0 else (float("inf") if gross_win > 0 else 0)

        avg_win_r  = (sum(r for r in rs if r > 0) / wins) if wins > 0 else 0
        avg_loss_r = (sum(r for r in rs if r < 0) / losses) if losses > 0 else 0
        expectancy_r = (win_rate * avg_win_r) + ((1 - win_rate) * avg_loss_r)

        running = 0
        peak = 0
        max_dd = 0
        for p in pnls:
            running += p
            if running > peak:
                peak = running
            dd = peak - running
            if dd > max_dd:
                max_dd = dd

        # Score distribution from signal_candidates
        with get_conn() as conn:
            dist_rows = conn.execute(
                """
                SELECT
                    CASE
                        WHEN final_score >= 90 THEN 'S'
                        WHEN final_score >= 82 THEN 'A+'
                        WHEN final_score >= 75 THEN 'A'
                        WHEN final_score >= 65 THEN 'B'
                        WHEN final_score >= 50 THEN 'C'
                        ELSE 'D'
                    END as grade,
                    COUNT(*) as cnt
                FROM signal_candidates
                WHERE created_at >= datetime('now', ?)
                  AND final_score IS NOT NULL
                GROUP BY grade
                """,
                (f"-{days} days",),
            ).fetchall()
        score_dist = {r[0]: r[1] for r in dist_rows}

        return {
            "days": days,
            "trade_count": total,
            "win_count": wins,
            "loss_count": losses,
            "win_rate": round(win_rate, 4),
            "profit_factor": round(profit_factor, 3),
            "expectancy_r": round(expectancy_r, 4),
            "avg_r": round(sum(rs) / total if total else 0, 4),
            "avg_win_r": round(avg_win_r, 4),
            "avg_loss_r": round(avg_loss_r, 4),
            "max_drawdown_usd": round(max_dd, 4),
            "gross_pnl": round(sum(pnls), 4),
            "score_distribution": score_dist,
        }
    except Exception as e:
        logger.error(f"[Dashboard] get_performance_summary hatası: {e}")
        return {"error": str(e)}


# ─────────────────────────────────────────────────────────────────────────────
# ARKAPLAN SERVİSİ
# ─────────────────────────────────────────────────────────────────────────────

def _run_loop():
    global _running
    logger.info("[Dashboard] Servis başladı.")
    while _running:
        try:
            today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
            compute_daily(today)
            compute_weekly()
            logger.debug("[Dashboard] Snapshot güncellendi.")
        except Exception as e:
            logger.error(f"[Dashboard] Loop hatası: {e}")
        time.sleep(_UPDATE_INTERVAL)
    logger.info("[Dashboard] Servis durdu.")


def start():
    global _running, _thread
    if _running:
        return
    _running = True
    _thread = threading.Thread(target=_run_loop, daemon=True, name="dashboard-svc")
    _thread.start()


def stop():
    global _running
    _running = False


def get_learning_metrics(days: int = 14) -> dict:
    """
    Paper outcome özetleri: kaçırılan fırsatlar (skip yanlıştı),
    veto sonrası kazanılan hypotetik, Telegram'da bildirilmiş ama açılmamış ve SL önce olanlar vb.
    """
    try:
        with get_conn() as conn:
            since_iso = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
            total_paper = conn.execute(
                "SELECT COUNT(*) FROM paper_results WHERE created_at >= ?", (since_iso,),
            ).fetchone()[0]

            finalized = conn.execute(
                """SELECT COUNT(*) FROM paper_results
                   WHERE created_at >= ? AND status='completed'""",
                (since_iso,),
            ).fetchone()[0]

            missed = conn.execute(
                """SELECT COUNT(*) FROM paper_results
                   WHERE created_at >= ? AND status='completed' AND skip_decision_correct=0""",
                (since_iso,),
            ).fetchone()[0]

            rejected_but_successful = conn.execute(
                """SELECT COUNT(*) FROM paper_results
                   WHERE created_at >= ?
                     AND status='completed' AND tracked_from='candidate'
                     AND would_have_won=1""",
                (since_iso,),
            ).fetchone()[0]

            approved_hypo_fail = conn.execute(
                """SELECT COUNT(*) FROM paper_results
                   WHERE created_at >= ?
                     AND status='completed' AND tracked_from='telegram_gap'
                     AND setup_worked=0 AND hit_stop_first=1""",
                (since_iso,),
            ).fetchone()[0]

            avg_mfe_hit = conn.execute(
                """SELECT AVG(max_favorable_excursion) FROM paper_results
                   WHERE created_at >= ? AND status='completed' AND hit_tp=1""",
                (since_iso,),
            ).fetchone()[0]

            avg_mae_sl = conn.execute(
                """SELECT AVG(max_adverse_excursion) FROM paper_results
                   WHERE created_at >= ? AND status='completed' AND hit_stop_first=1""",
                (since_iso,),
            ).fetchone()[0]

        return {
            "window_days": days,
            "paper_rows": int(total_paper or 0),
            "paper_finalized": int(finalized or 0),
            "missed_opportunities_skip_wrong": int(missed or 0),
            "rejected_candidate_but_hypo_win": int(rejected_but_successful or 0),
            "telegram_announced_but_hypo_stop_first": int(approved_hypo_fail or 0),
            "avg_mfe_R_on_tp_hit": round(float(avg_mfe_hit or 0), 4),
            "avg_mae_R_on_stop_first": round(float(avg_mae_sl or 0), 4),
        }
    except Exception as e:
        logger.warning(f"[Dashboard] learning metrics: {e}")
        return {"error": str(e)}


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    start()
    try:
        while True:
            time.sleep(10)
    except KeyboardInterrupt:
        stop()
