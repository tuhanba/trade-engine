"""
dashboard_service.py — AX Dashboard Service v5.0 (Production)
==============================================================
Flask API'ye veri sağlar.
Crash olmaz, hata durumunda güvenli default döner.
Tüm veriler tek yerden yönetilir.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone

import config
import database
from telegram_delivery import TelegramDelivery

logger = logging.getLogger("ax.dashboard_service")

_telegram = None

def _get_telegram() -> TelegramDelivery:
    global _telegram
    if _telegram is None:
        _telegram = TelegramDelivery()
    return _telegram


def get_health() -> dict:
    """Sistem sağlık durumu."""
    bot_status = database.get_bot_status()
    telegram = _get_telegram()

    db_ok = False
    try:
        conn = database.get_connection()
        conn.execute("SELECT 1")
        conn.close()
        db_ok = True
    except Exception:
        pass

    heartbeat = bot_status.get("heartbeat", {}).get("value", "")
    status = bot_status.get("status", {}).get("value", "unknown")
    last_error = bot_status.get("last_error", {}).get("value", "")

<<<<<<< HEAD
    # Bot son aktif mi?
    bot_alive = False
    if heartbeat:
=======
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
# ARKAPLAN SERVİSİ
# ─────────────────────────────────────────────────────────────────────────────

def _run_loop():
    global _running
    logger.info("[Dashboard] Servis başladı.")
    while _running:
>>>>>>> 0797c70b8640d2006e47a50d5580ffae4606199b
        try:
            hb_dt = datetime.fromisoformat(heartbeat.replace("Z", "+00:00"))
            elapsed_sec = (datetime.now(timezone.utc) - hb_dt).total_seconds()
            bot_alive = elapsed_sec < 300  # 5 dakika içinde heartbeat varsa alive
        except Exception:
            pass

    return {
        "ok": db_ok,
        "db_connected": db_ok,
        "execution_mode": config.EXECUTION_MODE,
        "live_trading_enabled": config.LIVE_TRADING_ENABLED,
        "dry_run": config.DRY_RUN,
        "telegram_configured": telegram.is_configured(),
        "bot_status": status,
        "bot_alive": bot_alive,
        "last_heartbeat": heartbeat,
        "last_error": last_error,
        "server_time": datetime.now(timezone.utc).isoformat(),
    }


def get_live_trades() -> list[dict]:
    """Açık trade'lerin detaylı listesi."""
    trades = database.get_open_trades()
    result = []
    for t in trades:
        # Metadata'dan exit state parse et
        exit_state = {}
        try:
            meta_raw = t.get("metadata", "")
            if meta_raw and meta_raw.strip().startswith("{"):
                import json
                exit_state = json.loads(meta_raw)
        except Exception:
            pass

        result.append({
            "id": t.get("id"),
            "symbol": t.get("symbol"),
            "side": t.get("side"),
            "entry_price": t.get("entry_price", 0),
            "current_price": t.get("current_price", 0),
            "stop_loss": t.get("stop_loss", 0),
            "tp1": t.get("tp1", 0),
            "tp2": t.get("tp2", 0),
            "tp3": t.get("tp3", 0),
            "leverage": t.get("leverage", 1),
            "margin_used": t.get("margin_used", 0),
            "risk_usd": t.get("risk_usd", 0),
            "unrealized_pnl": t.get("unrealized_pnl", 0),
            "accumulated_pnl": t.get("accumulated_pnl", 0),
            "remaining_qty_pct": t.get("remaining_qty_pct", 100),
            "total_pnl": round(
                (t.get("unrealized_pnl") or 0) + (t.get("accumulated_pnl") or 0), 4
            ),
            "opened_at": t.get("opened_at", ""),
            # Exit state
            "tp1_hit": exit_state.get("tp1_hit", False),
            "tp2_hit": exit_state.get("tp2_hit", False),
            "trailing_active": exit_state.get("trailing_active", False),
            "breakeven_set": exit_state.get("breakeven_set", False),
            "trailing_sl": exit_state.get("current_sl", 0),
        })
    return result


def get_stats() -> dict:
    """Özet istatistikler (genişletilmiş)."""
    return database.get_dashboard_stats()


def get_trades(limit: int = 100) -> list[dict]:
    """Son trade listesi."""
    return database.get_recent_trades(limit)


def get_signals(limit: int = 100) -> list[dict]:
    """Son sinyal adayları listesi."""
    return database.get_recent_signals(limit)
