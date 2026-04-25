"""
n8n_bridge.py — AX ↔ n8n Köprüsü
==================================
• Bot → n8n : trade_open, trade_close, ai_report, alert
• n8n → Bot : /n8n/pause, /n8n/resume, /n8n/status, /n8n/ai_trigger, /n8n/finish
"""

import os
import threading
import logging
import requests
from datetime import datetime, timezone
from flask import Blueprint, request, jsonify

logger = logging.getLogger(__name__)

N8N_BASE        = os.getenv("N8N_BASE_URL", "http://localhost:5678")
N8N_TRADE_OPEN  = os.getenv("N8N_WEBHOOK_TRADE_OPEN",  f"{N8N_BASE}/webhook/ax-trade-open")
N8N_TRADE_CLOSE = os.getenv("N8N_WEBHOOK_TRADE_CLOSE", f"{N8N_BASE}/webhook/ax-trade-close")
N8N_AI_REPORT   = os.getenv("N8N_WEBHOOK_AI_REPORT",   f"{N8N_BASE}/webhook/ax-ai-report")
N8N_ALERT       = os.getenv("N8N_WEBHOOK_ALERT",       f"{N8N_BASE}/webhook/ax-alert")
N8N_SECRET      = os.getenv("N8N_SECRET", "aurvex-ax-secret")

_bot_refs = {
    "tg_manager":      None,
    "get_balance":     None,
    "get_open_trades": None,
    "run_ai_brain":    None,
}

def register_bot(tg_manager=None, get_balance_fn=None,
                 get_open_trades_fn=None, run_ai_brain_fn=None):
    _bot_refs["tg_manager"]       = tg_manager
    _bot_refs["get_balance"]      = get_balance_fn
    _bot_refs["get_open_trades"]  = get_open_trades_fn
    _bot_refs["run_ai_brain"]     = run_ai_brain_fn
    logger.info("[n8n_bridge] Bot referansları kaydedildi.")

# =============================================================================
# BOT → n8n BİLDİRİMLER
# =============================================================================
def _post(url: str, payload: dict, timeout: int = 5):
    try:
        r = requests.post(
            url, json=payload,
            headers={"X-AX-Secret": N8N_SECRET, "Content-Type": "application/json"},
            timeout=timeout
        )
        if r.status_code not in (200, 201):
            logger.warning(f"[n8n_bridge] POST {url} → {r.status_code}")
    except Exception as e:
        logger.debug(f"[n8n_bridge] n8n kapalı olabilir: {e}")

def notify_trade_open(symbol, direction, entry, sl, tp, leverage, ml_score, rr, balance):
    threading.Thread(target=_post, args=(N8N_TRADE_OPEN, {
        "event": "trade_open", "timestamp": datetime.now(timezone.utc).isoformat(),
        "symbol": symbol, "direction": direction, "entry": entry,
        "sl": sl, "tp": tp, "leverage": leverage,
        "ml_score": ml_score, "rr": rr, "balance": balance,
    }), daemon=True).start()

def notify_trade_close(symbol, direction, result, net_pnl, exit_price,
                       hold_minutes, balance, actual_rr=0):
    threading.Thread(target=_post, args=(N8N_TRADE_CLOSE, {
        "event": "trade_close", "timestamp": datetime.now(timezone.utc).isoformat(),
        "symbol": symbol, "direction": direction, "result": result,
        "net_pnl": net_pnl, "exit_price": exit_price,
        "hold_minutes": hold_minutes, "balance": balance, "actual_rr": actual_rr,
    }), daemon=True).start()

def notify_ai_report(insight, win_rate=0, total_pnl=0, changes=None, trades_analyzed=0):
    threading.Thread(target=_post, args=(N8N_AI_REPORT, {
        "event": "ai_report", "timestamp": datetime.now(timezone.utc).isoformat(),
        "insight": insight[:500], "win_rate": win_rate,
        "total_pnl": total_pnl, "changes": changes or [],
        "trades_analyzed": trades_analyzed,
    }), daemon=True).start()

def notify_alert(alert_type, message, severity="warning"):
    threading.Thread(target=_post, args=(N8N_ALERT, {
        "event": "alert", "timestamp": datetime.now(timezone.utc).isoformat(),
        "type": alert_type, "message": message, "severity": severity,
    }), daemon=True).start()

# =============================================================================
# n8n → BOT KONTROL ENDPOINT'LERİ
# =============================================================================
n8n_bp = Blueprint("n8n", __name__, url_prefix="/n8n")

def _auth():
    return request.headers.get("X-AX-Secret") == N8N_SECRET

@n8n_bp.route("/pause", methods=["POST"])
def n8n_pause():
    if not _auth(): return jsonify({"ok": False, "error": "Unauthorized"}), 401
    tg = _bot_refs.get("tg_manager")
    if tg: tg.paused = True
    logger.info("[n8n_bridge] Bot PAUSE edildi.")
    return jsonify({"ok": True, "status": "paused"})

@n8n_bp.route("/resume", methods=["POST"])
def n8n_resume():
    if not _auth(): return jsonify({"ok": False, "error": "Unauthorized"}), 401
    tg = _bot_refs.get("tg_manager")
    if tg: tg.paused = False
    logger.info("[n8n_bridge] Bot RESUME edildi.")
    return jsonify({"ok": True, "status": "running"})

@n8n_bp.route("/finish", methods=["POST"])
def n8n_finish():
    if not _auth(): return jsonify({"ok": False, "error": "Unauthorized"}), 401
    tg = _bot_refs.get("tg_manager")
    if tg: tg.finish_mode = True
    logger.info("[n8n_bridge] Bot FINISH moduna alındı.")
    return jsonify({"ok": True, "status": "finish_mode"})

@n8n_bp.route("/status", methods=["GET"])
def n8n_status():
    if not _auth(): return jsonify({"ok": False, "error": "Unauthorized"}), 401
    tg       = _bot_refs.get("tg_manager")
    bal_fn   = _bot_refs.get("get_balance")
    trades_fn = _bot_refs.get("get_open_trades")
    return jsonify({
        "ok":          True,
        "paused":      tg.paused        if tg else False,
        "finish_mode": tg.finish_mode   if tg else False,
        "balance":     bal_fn()         if bal_fn else 0,
        "open_trades": len(trades_fn()) if trades_fn else 0,
        "timestamp":   datetime.now(timezone.utc).isoformat(),
    })

@n8n_bp.route("/ai_trigger", methods=["POST"])
def n8n_ai_trigger():
    if not _auth(): return jsonify({"ok": False, "error": "Unauthorized"}), 401
    ai_fn = _bot_refs.get("run_ai_brain")
    if ai_fn:
        threading.Thread(target=ai_fn, daemon=True).start()
        logger.info("[n8n_bridge] AI Brain n8n tarafından tetiklendi.")
        return jsonify({"ok": True, "message": "AI Brain başlatıldı"})
    return jsonify({"ok": False, "error": "run_ai_brain yok"}), 500
