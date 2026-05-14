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

    # Bot son aktif mi?
    bot_alive = False
    if heartbeat:
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
