"""
n8n_bridge.py — AX ↔ n8n köprüsü (sadece raporlama/monitoring)

n8n'in yapacakları:
  - Günlük rapor tetikleme  (/n8n/daily_report)
  - Haftalık rapor tetikleme (/n8n/weekly_report)
  - Health ping             (/n8n/health)
  - Crash alert             (/n8n/crash_alert  POST)
  - Backup job              (/n8n/backup)

n8n KARAR VERMEZ. pause/resume/finish sadece Telegram'dan yapılır.
"""

import logging
import threading
from datetime import datetime, timezone

import requests
from flask import Blueprint, jsonify, request

import config
from database import get_conn

logger = logging.getLogger(__name__)

N8N_SECRET = config.N8N_WEBHOOK_URL   # secret olarak webhook url'ini kullan
_SECRET    = "ax-n8n-2026"            # .env'den alınabilir, şimdilik sabit

n8n_bp = Blueprint("n8n", __name__, url_prefix="/n8n")


# ── Auth ──────────────────────────────────────────────────────────────────────

def _auth() -> bool:
    return request.headers.get("X-AX-Secret") == _SECRET


def _unauth():
    return jsonify({"ok": False, "error": "Unauthorized"}), 401


# ── Bot → n8n (outbound, fire-and-forget) ────────────────────────────────────

def _post_to_n8n(payload: dict):
    """n8n webhook'una asenkron POST at."""
    url = config.N8N_WEBHOOK_URL
    if not url:
        return
    def _send():
        try:
            requests.post(url, json=payload,
                          headers={"X-AX-Secret": _SECRET},
                          timeout=5)
        except Exception as e:
            logger.debug(f"[n8n] webhook hata: {e}")
    threading.Thread(target=_send, daemon=True).start()


def ping_health():
    """60 saniyede bir health durumunu n8n'e gönder."""
    conn = get_conn()
    c = conn.cursor()
    c.execute("SELECT * FROM system_state WHERE id=1")
    row = c.fetchone()
    c.execute("SELECT COUNT(*) FROM trades WHERE status='OPEN'")
    open_cnt = c.fetchone()[0]
    c.execute("SELECT paper_balance FROM paper_account WHERE id=1")
    bal = float((c.fetchone() or [250])[0])
    conn.close()

    state = dict(row) if row else {}
    _post_to_n8n({
        "event":           "health_ping",
        "timestamp":       datetime.now(timezone.utc).isoformat(),
        "bot_status":      state.get("bot_status", "unknown"),
        "circuit_breaker": bool(state.get("circuit_breaker_active")),
        "open_trades":     open_cnt,
        "balance":         bal,
    })


def post_crash_alert(error: str):
    """Kritik hata olduğunda n8n'e bildir."""
    _post_to_n8n({
        "event":     "crash_alert",
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "error":     error[:500],
    })


def post_daily_summary():
    """Günlük özeti n8n'e gönder (backup/arşiv için)."""
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    conn = get_conn()
    c = conn.cursor()
    c.execute("""
        SELECT COUNT(*),
               SUM(CASE WHEN result='WIN' THEN 1 ELSE 0 END),
               ROUND(SUM(net_pnl),4),
               ROUND(AVG(r_multiple),3)
        FROM trades
        WHERE status NOT IN ('OPEN','TP1_HIT','TP2_HIT','RUNNER_ACTIVE')
          AND close_time >= ?
    """, (today,))
    row = c.fetchone()
    conn.close()
    _post_to_n8n({
        "event":       "daily_summary",
        "date":        today,
        "total":       row[0] or 0,
        "wins":        row[1] or 0,
        "total_pnl":   row[2] or 0,
        "avg_r":       row[3] or 0,
    })


# ── n8n → Bot (inbound webhooks) ─────────────────────────────────────────────

@n8n_bp.route("/health", methods=["GET"])
def n8n_health():
    """n8n health check — bot canlı mı?"""
    if not _auth():
        return _unauth()
    conn = get_conn()
    c = conn.cursor()
    c.execute("SELECT last_heartbeat, bot_status FROM system_state WHERE id=1")
    row = c.fetchone()
    conn.close()
    if row:
        hb = row["last_heartbeat"] or ""
        status = row["bot_status"] or "unknown"
    else:
        hb, status = "", "unknown"
    return jsonify({
        "ok":            True,
        "bot_status":    status,
        "last_heartbeat": hb,
        "timestamp":     datetime.now(timezone.utc).isoformat(),
    })


@n8n_bp.route("/daily_report", methods=["POST"])
def n8n_daily_report():
    """n8n günlük raporu Telegram'a göndermeyi tetikler."""
    if not _auth():
        return _unauth()
    try:
        from telegram_manager import notify_daily_report
        threading.Thread(target=notify_daily_report, daemon=True).start()
        logger.info("[n8n] Günlük rapor tetiklendi.")
        return jsonify({"ok": True, "message": "daily_report tetiklendi"})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500


@n8n_bp.route("/weekly_report", methods=["POST"])
def n8n_weekly_report():
    """n8n haftalık raporu Telegram'a göndermeyi tetikler."""
    if not _auth():
        return _unauth()
    try:
        from telegram_manager import notify_weekly_report
        threading.Thread(target=notify_weekly_report, daemon=True).start()
        logger.info("[n8n] Haftalık rapor tetiklendi.")
        return jsonify({"ok": True, "message": "weekly_report tetiklendi"})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500


@n8n_bp.route("/crash_alert", methods=["POST"])
def n8n_crash_alert():
    """n8n crash alert aldı, Telegram'a ilet."""
    if not _auth():
        return _unauth()
    data = request.get_json() or {}
    msg  = data.get("message", "Bilinmeyen crash")
    try:
        from telegram_manager import notify_critical
        notify_critical(msg)
        return jsonify({"ok": True})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500


@n8n_bp.route("/backup", methods=["POST"])
def n8n_backup():
    """n8n backup job tetiklendiğinde DB snapshot al."""
    if not _auth():
        return _unauth()
    try:
        import shutil, os
        src = config.DB_PATH
        dst = config.DB_PATH + f".backup_{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M')}"
        shutil.copy2(src, dst)
        logger.info(f"[n8n] DB backup: {dst}")
        return jsonify({"ok": True, "backup": os.path.basename(dst)})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500
