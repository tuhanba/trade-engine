"""
app.py — AX Trade Engine Dashboard API v5.0 (Production)
=========================================================
Flask tabanlı REST API + Server-Sent Events (SSE) ile
gerçek zamanlı veri akışı.

Endpoints:
  GET /                    → Dashboard HTML
  GET /api/health          → Sistem sağlık
  GET /api/live            → Açık trade'ler
  GET /api/stats           → İstatistikler
  GET /api/trades          → Son trade'ler
  GET /api/signals         → Son sinyaller
  GET /api/learning        → Ghost learning özeti
  GET /api/partial-closes/<id> → Trade partial close'ları
  GET /stream              → SSE real-time stream
"""

from __future__ import annotations

import json
import logging
import time
from datetime import datetime, timezone

from flask import Flask, Response, jsonify, render_template, stream_with_context

import config
import database
import dashboard_service

logger = logging.getLogger("ax.app")

app = Flask(__name__)
app.secret_key = getattr(config, "SECRET_KEY", "ax_secret_2026")


# ── CORS ─────────────────────────────────────────────────────────────

@app.after_request
def add_cors_headers(response):
    response.headers["Access-Control-Allow-Origin"] = "*"
    response.headers["Access-Control-Allow-Headers"] = "Content-Type, Authorization"
    response.headers["Access-Control-Allow-Methods"] = "GET, OPTIONS"
    return response


# ── Yardımcı ─────────────────────────────────────────────────────────

def _ok(data):
    return jsonify({"ok": True, "data": data, "ts": datetime.now(timezone.utc).isoformat()})


def _error(msg: str, code: int = 500):
    return jsonify({"ok": False, "error": str(msg)}), code


# ── Dashboard HTML ───────────────────────────────────────────────────

@app.route("/")
def index():
    """Dashboard HTML sayfası."""
    try:
        return render_template("index.html")
    except Exception as exc:
        return f"<h1>Dashboard</h1><p>Template yüklenemedi: {exc}</p>"


# ── Core API Endpoints ───────────────────────────────────────────────

@app.route("/api/health")
def api_health():
    """Sistem sağlık durumu."""
    try:
        return _ok(dashboard_service.get_health())
    except Exception as exc:
        return _error(str(exc))


@app.route("/api/live")
def api_live():
    """Açık trade'ler (gerçek zamanlı)."""
    try:
        return _ok(dashboard_service.get_live_trades())
    except Exception as exc:
        return _error(str(exc))


@app.route("/api/stats")
def api_stats():
    """İstatistikler."""
    try:
        return _ok(dashboard_service.get_stats())
    except Exception as exc:
        return _error(str(exc))


@app.route("/api/trades")
def api_trades():
    """Son trade'ler (limit=100)."""
    try:
        return _ok(dashboard_service.get_trades())
    except Exception as exc:
        return _error(str(exc))


@app.route("/api/signals")
def api_signals():
    """Son sinyal adayları."""
    try:
        return _ok(dashboard_service.get_signals())
    except Exception as exc:
        return _error(str(exc))


@app.route("/api/learning")
def api_learning():
    """Ghost learning özeti."""
    try:
        from core.ai_decision_engine import get_learning_summary
        return _ok(get_learning_summary())
    except Exception as exc:
        return _error(str(exc))


@app.route("/api/partial-closes/<int:trade_id>")
def api_partial_closes(trade_id: int):
    """Belirli trade'in partial close'larını döner."""
    try:
        closes = database.get_partial_closes(trade_id)
        return _ok(closes)
    except Exception as exc:
        return _error(str(exc))


# ── SSE Real-time Stream ─────────────────────────────────────────────

@app.route("/stream")
def stream():
    """
    Server-Sent Events ile gerçek zamanlı veri akışı.
    Dashboard bu endpoint'e bağlanır, her 5sn'de güncelleme alır.
    """
    def event_generator():
        while True:
            try:
                # Tüm dashboard verisini tek seferde çek
                payload = {
                    "health": dashboard_service.get_health(),
                    "stats": dashboard_service.get_stats(),
                    "live": dashboard_service.get_live_trades(),
                    "trades": dashboard_service.get_trades(20),
                    "signals": dashboard_service.get_signals(20),
                    "ts": datetime.now(timezone.utc).isoformat(),
                }
                data = json.dumps(payload)
                yield f"data: {data}\n\n"
            except Exception as exc:
                logger.error("SSE stream hatası: %s", exc)
                yield f"data: {json.dumps({'error': str(exc)})}\n\n"
            time.sleep(5)

    return Response(
        stream_with_context(event_generator()),
        mimetype="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
            "Connection": "keep-alive",
        },
    )


# ── Server başlatma ─────────────────────────────────────────────────

def main():
    """DB'yi hazırla ve Flask server'ı başlat."""
    database.init_db()
    database.migrate_db()
    logger.info(
        "Dashboard API başlatılıyor %s:%s",
        config.FLASK_HOST, config.FLASK_PORT,
    )
    app.run(
        host=config.FLASK_HOST,
        port=config.FLASK_PORT,
        debug=False,
        threaded=True,
    )


if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s – %(message)s",
    )
    main()
