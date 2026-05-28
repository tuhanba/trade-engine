"""
dashboard_service.py — AX Dashboard Service v5.0 (Production)
==============================================================
Flask API'ye veri sağlar.
Crash olmaz, hata durumunda güvenli default döner.
Tüm veriler tek yerden yönetilir.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone

import config
import database
from telegram_delivery import TelegramDelivery

logger = logging.getLogger("ax.dashboard_service")

_telegram = None


def _safe_call(fn, default, *args, **kwargs):
    """Crash olmadan güvenli çağrı — exception'ı yakala, default döndür."""
    try:
        result = fn(*args, **kwargs)
        return result if result is not None else default
    except Exception as e:
        logger.error(f"[Dashboard] {fn.__name__} hata: {e}")
        return default

def _get_telegram() -> TelegramDelivery:
    global _telegram
    if _telegram is None:
        _telegram = TelegramDelivery()
    return _telegram


def get_health() -> dict:
    """Sistem sağlık durumu."""
    return _safe_call(_get_health_impl, {"ok": False, "error": "service_unavailable"})


def _get_health_impl() -> dict:
    bot_status = database.get_bot_status()
    telegram = _get_telegram()

    db_ok = False
    try:
        with database.get_conn() as conn:
            conn.execute("SELECT 1")
        db_ok = True
    except Exception:
        pass

    heartbeat = bot_status.get("heartbeat", {}).get("value", "")
    status = bot_status.get("status", {}).get("value", "unknown")
    last_error = bot_status.get("last_error", {}).get("value", "")

    # Bot son aktif mi?
    bot_alive = False
    elapsed_sec = None
    if heartbeat:
        try:
            hb_dt = datetime.fromisoformat(heartbeat.replace("Z", "+00:00"))
            elapsed_sec = (datetime.now(timezone.utc) - hb_dt).total_seconds()
            bot_alive = elapsed_sec < 120  # 2 dakika içinde heartbeat varsa alive
        except Exception:
            pass

    # Circuit breaker kontrolü
    cb_active = False
    try:
        cb_val = database.get_state("circuit_breaker_until")
        if cb_val:
            until = datetime.fromisoformat(cb_val)
            if until.tzinfo is None:
                until = until.replace(tzinfo=timezone.utc)
            cb_active = datetime.now(timezone.utc) < until
    except Exception:
        pass

    return {
        "ok": db_ok,
        "db_connected": db_ok,
        "execution_mode": bot_status.get("tg_execution_mode", {}).get("value") or getattr(config, "EXECUTION_MODE", "paper"),
        "ax_mode": getattr(config, "AX_MODE", "execute"),
        "human_mode": str(bot_status.get("tg_human_mode", {}).get("value")) == "True" if bot_status.get("tg_human_mode", {}).get("value") is not None else bool(getattr(config, "HUMAN_MODE", False)),
        "live_trading_enabled": getattr(config, "LIVE_TRADING_ENABLED", False),
        "dry_run": getattr(config, "DRY_RUN", False),
        "telegram_configured": telegram.is_configured(),
        "bot_status": status,
        "bot_alive": bot_alive,
        "last_seen_seconds": int(elapsed_sec) if elapsed_sec is not None else None,
        "last_heartbeat": heartbeat,
        "last_error": last_error,
        "circuit_breaker_active": cb_active,
        "trade_threshold": getattr(config, "TRADE_THRESHOLD", 55.0),
        "telegram_threshold": getattr(config, "TELEGRAM_THRESHOLD", 35.0),
        "server_time": datetime.now(timezone.utc).isoformat(),
    }


def get_live_trades() -> list:
    """Açık trade'lerin detaylı listesi."""
    return _safe_call(_get_live_trades_impl, [])


def _get_live_trades_impl() -> list:
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
            "id":            t.get("id"),
            "symbol":        t.get("symbol", "?"),
            "side":          t.get("direction") or t.get("side", "?"),
            "entry_price":   float(t.get("entry") or t.get("entry_price") or 0),
            "current_price": float(t.get("current_price") or 0),
            "stop_loss":     float(t.get("sl") or t.get("stop_loss") or 0),
            "tp1":           float(t.get("tp1") or 0),
            "tp2":           float(t.get("tp2") or 0),
            "tp3":           float(t.get("tp3") or 0),
            "leverage":      int(t.get("leverage") or 1),
            "qty":           float(t.get("qty") or t.get("quantity") or 0),
            "notional":      float(t.get("notional_size") or t.get("notional") or 0),
            "margin_used":   float(t.get("margin_used") or 0),
            "risk_usd":      float(t.get("risk_usd") or 0),
            "unrealized_pnl": float(t.get("unrealized_pnl") or 0),
            "realized_pnl":  float(t.get("realized_pnl") or 0),
            "accumulated_pnl": float(t.get("realized_pnl") or t.get("accumulated_pnl") or 0),
            "remaining_qty_pct": float(
                round((float(t.get("remaining_qty") or 0) / float(t.get("qty") or 1)) * 100, 1)
                if t.get("remaining_qty") is not None and float(t.get("qty") or 0) > 0
                else 100.0
            ),
            "total_pnl":     round(
                (t.get("unrealized_pnl") or 0) +
                (t.get("realized_pnl") or t.get("accumulated_pnl") or 0), 4
            ),
            "status":        t.get("status", "open"),
            "opened_at":     t.get("open_time") or t.get("opened_at", ""),
            "setup_quality": t.get("setup_quality", "-"),
            # Exit state
            "tp1_hit":          exit_state.get("tp1_hit", False),
            "tp2_hit":          exit_state.get("tp2_hit", False),
            "trailing_active":  exit_state.get("trailing_active", False),
            "breakeven_set":    exit_state.get("breakeven_set", False),
            "trailing_sl":      exit_state.get("current_sl", 0),
        })
    return result


def get_stats() -> dict:
    """Özet istatistikler (genişletilmiş)."""
    return _safe_call(database.get_dashboard_stats, {})


def get_trades(limit: int = 100) -> list[dict]:
    """Son trade listesi."""
    return database.get_recent_trades(limit)


def get_signals(limit: int = 100) -> list[dict]:
    """Son sinyal adayları listesi."""
    return database.get_recent_signals(limit)


def get_ax_status() -> dict:
    """Bot durumu, mod, heartbeat, circuit breaker."""
    try:
        bot_status = database.get_bot_status()
        heartbeat  = bot_status.get("heartbeat", {}).get("value", "")
        status     = bot_status.get("status", {}).get("value", "unknown")
        bot_alive  = False
        last_seen  = None
        if heartbeat:
            try:
                hb = datetime.fromisoformat(heartbeat.replace("Z", "+00:00"))
                if hb.tzinfo is None:
                    hb = hb.replace(tzinfo=timezone.utc)
                delta = (datetime.now(timezone.utc) - hb).total_seconds()
                last_seen = int(delta)
                bot_alive = delta < 120
            except Exception:
                pass
        cb_until  = database.get_state("circuit_breaker_until") or ""
        cb_active = False
        if cb_until:
            try:
                cb = datetime.fromisoformat(cb_until.replace("Z", "+00:00"))
                if cb.tzinfo is None:
                    cb = cb.replace(tzinfo=timezone.utc)
                cb_active = cb > datetime.now(timezone.utc)
            except Exception:
                pass
        balance = 0.0
        try:
            balance = database.get_paper_balance() or 0.0
        except Exception:
            pass
        return {
            "bot_running": bot_alive, "bot_status": status,
            "heartbeat": heartbeat, "last_seen_seconds": last_seen,
            "execution_mode": bot_status.get("tg_execution_mode", {}).get("value") or getattr(config, "EXECUTION_MODE", "paper"),
            "ax_mode": getattr(config, "AX_MODE", "execute"),
            "human_mode": str(bot_status.get("tg_human_mode", {}).get("value")) == "True" if bot_status.get("tg_human_mode", {}).get("value") is not None else bool(getattr(config, "HUMAN_MODE", False)),
            "paper_mode": getattr(config, "PAPER_MODE", True),
            "circuit_breaker_active": cb_active, "circuit_breaker_until": cb_until,
            "paper_balance": round(balance, 2),
            "initial_balance": getattr(config, "INITIAL_PAPER_BALANCE", 2000.0),
        }
    except Exception as e:
        logger.error("get_ax_status hata: %s", e)
        return {"bot_running": False, "bot_status": "error", "paper_balance": 0.0, "initial_balance": 2000.0}


def get_calendar_data(days: int = 30) -> list:
    """Günlük PnL takvimi."""
    try:
        with database.get_conn() as conn:
            rows = conn.execute("""
                SELECT date(close_time) AS day,
                       COALESCE(SUM(net_pnl),0) AS pnl,
                       COUNT(*) AS trades,
                       SUM(CASE WHEN net_pnl>0 THEN 1 ELSE 0 END) AS wins
                FROM trades
                WHERE close_time IS NOT NULL AND close_time >= date('now',?)
                GROUP BY day ORDER BY day ASC
            """, (f"-{days} days",)).fetchall()
        return [{"day": r["day"], "pnl": round(float(r["pnl"]), 4),
                 "trades": r["trades"], "wins": r["wins"]} for r in rows]
    except Exception as e:
        logger.error("get_calendar_data hata: %s", e)
        return []


def get_weekly_data(weeks: int = 8) -> list:
    """Haftalık PnL özeti."""
    try:
        with database.get_conn() as conn:
            rows = conn.execute("""
                SELECT strftime('%Y-W%W', close_time) AS week,
                       COALESCE(SUM(net_pnl),0) AS pnl,
                       COUNT(*) AS trades,
                       SUM(CASE WHEN net_pnl>0 THEN 1 ELSE 0 END) AS wins
                FROM trades
                WHERE close_time IS NOT NULL AND close_time >= date('now',?)
                GROUP BY week ORDER BY week ASC
            """, (f"-{weeks*7} days",)).fetchall()
        return [{"week": r["week"], "pnl": round(float(r["pnl"]), 4),
                 "trades": r["trades"], "wins": r["wins"],
                 "winrate": round(r["wins"] / r["trades"] * 100, 1) if r["trades"] else 0.0}
                for r in rows]
    except Exception as e:
        logger.error("get_weekly_data hata: %s", e)
        return []


def get_learning_metrics(days: int = 14) -> dict:
    """Ghost + paper learning metrikleri."""
    try:
        with database.get_conn() as conn:
            cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()

            def q(sql, *args):
                return conn.execute(sql, args).fetchone()[0] or 0

            ghost_tp  = q("SELECT COUNT(*) FROM signal_candidates WHERE status='TP_HIT' AND created_at>=?", cutoff)
            ghost_sl  = q("SELECT COUNT(*) FROM signal_candidates WHERE status='SL_HIT' AND created_at>=?", cutoff)
            ghost_pnl = q("SELECT COALESCE(SUM(ghost_pnl),0) FROM signal_candidates WHERE status IN ('TP_HIT','SL_HIT') AND created_at>=?", cutoff)
            p_done    = q("SELECT COUNT(*) FROM paper_results WHERE status='completed' AND created_at>=?", cutoff)
            p_wins    = q("SELECT COUNT(*) FROM paper_results WHERE would_have_won=1 AND created_at>=?", cutoff)

        res = ghost_tp + ghost_sl
        return {
            "days": days,
            "ghost_tp_hits": ghost_tp, "ghost_sl_hits": ghost_sl,
            "ghost_winrate": round(ghost_tp / res * 100, 1) if res else 0.0,
            "ghost_pnl": round(float(ghost_pnl), 4),
            "paper_completed": p_done, "paper_wins": p_wins,
            "paper_winrate": round(p_wins / p_done * 100, 1) if p_done else 0.0,
        }
    except Exception as e:
        logger.error("get_learning_metrics hata: %s", e)
        return {"days": days, "ghost_winrate": 0.0, "paper_winrate": 0.0}
