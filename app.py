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
import os
import logging
import time
from datetime import datetime, timezone

from flask import Flask, Response, jsonify, render_template, request, stream_with_context

try:
    from flask_socketio import SocketIO
    SOCKETIO_AVAILABLE = True
except ImportError:
    SOCKETIO_AVAILABLE = False
    class SocketIO:
        def __init__(self, app, **kw): pass
        def emit(self, *a, **kw): pass
        def run(self, app, **kw): app.run(**kw)

import config
import database
import dashboard_service
from database import get_conn, get_closed_trades, get_open_trades, get_paper_balance

logger = logging.getLogger("ax.app")

N8N_AVAILABLE = False

app = Flask(__name__)
app.secret_key = getattr(config, "SECRET_KEY", "ax_secret_2026")
socketio = SocketIO(app)


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


def _fmt_duration(open_time=None, close_time=None):
    """İki zaman damgası arasındaki süreyi biçimlendirir."""
    try:
        if not open_time:
            return "", 0
        fmt = "%Y-%m-%d %H:%M:%S"
        t0 = datetime.strptime(str(open_time)[:19], fmt).replace(tzinfo=timezone.utc)
        t1 = datetime.strptime(str(close_time)[:19], fmt).replace(tzinfo=timezone.utc) if close_time else datetime.now(timezone.utc)
        mins = int((t1 - t0).total_seconds() / 60)
        if mins < 60:
            return f"{mins}dk", mins
        h, m = divmod(mins, 60)
        return f"{h}s{m}dk", mins
    except Exception:
        return "", 0


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
        stats = dashboard_service.get_stats()
        ax_status = dashboard_service.get_ax_status()
        return _ok({
            **stats,
            "balance": stats.get("balance", ax_status.get("paper_balance", 0)),
            "initial_balance": stats.get("initial_balance", ax_status.get("initial_balance", 500.0)),
            "bot_running": ax_status.get("bot_running", False),
            "execution_mode": ax_status.get("execution_mode", "paper"),
        })
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
@app.route("/api/scalp_signals")
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


# ── /api/balance ──────────────────────────────────────────────────────────────
@app.route("/api/balance")
def api_balance():
    try:
        paper_balance = get_paper_balance()
        return jsonify({"ok": True, "data": {
            "paper_balance":  round(paper_balance, 4),
            "usdt_balance":   round(paper_balance, 4),
            "usdt_available": round(paper_balance, 4),
        }})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500


# ── /api/params ───────────────────────────────────────────────────────────────
@app.route("/api/params")
def api_params():
    try:
        p = None
        if not p:
            p = {
                "sl_atr_mult": 1.5, "tp_atr_mult": 2.5,
                "rsi5_min": 40, "rsi5_max": 70,
                "rsi1_min": 40, "rsi1_max": 68,
                "vol_ratio_min": 1.8, "min_volume_m": 5.0,
                "min_change_pct": 1.5, "risk_pct": 1.0, "version": 1,
                "ai_reason": "Parametre bulunamadı.",
            }
        return jsonify({"ok": True, "data": p})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500


# ── /api/coin_stats ───────────────────────────────────────────────────────────
@app.route("/api/coin_stats")
def api_coin_stats():
    try:
        with get_conn() as conn:
            rows = conn.execute("""
                SELECT symbol, sample_size as trade_count,
                       ROUND(win_rate*100,1) as win_rate_pct,
                       avg_r, profit_factor, danger_score
                FROM coin_profiles
                WHERE sample_size >= 3
                ORDER BY profit_factor DESC
                LIMIT 20
            """).fetchall()
        return jsonify({"ok": True, "data": [dict(r) for r in rows]})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500


# ── /api/ml_status ────────────────────────────────────────────────────────────
@app.route("/api/ml_status")
def api_ml_status():
    try:
        from ml_signal_scorer import get_scorer
        status = get_scorer().get_status()
        return jsonify({"ok": True, "data": status})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500


# ── /api/logs ─────────────────────────────────────────────────────────────────
@app.route("/api/logs")
def api_logs():
    import re
    LOG_PATHS = [
        "/root/trade_engine/logs/ax_bot.log",
        "/root/trade_engine/logs/bot.log",
        "/root/trade_engine/bot.log",
        "/root/trade_engine/scalp_bot.log",
        os.path.join(os.path.dirname(os.path.abspath(__file__)), "logs", "ax_bot.log"),
    ]
    n = min(int(request.args.get("n", 80)), 300)
    lines_out = []

    for path in LOG_PATHS:
        if os.path.exists(path):
            try:
                with open(path, "r", encoding="utf-8", errors="replace") as f:
                    raw_lines = f.readlines()[-n:]
                for raw in raw_lines:
                    raw = raw.rstrip("\n")
                    level = "INFO"
                    if re.search(r"\bERROR\b|\bCRITICAL\b|\bException\b|Traceback", raw, re.I):
                        level = "ERROR"
                    elif re.search(r"\bWARNING\b|\bWARN\b", raw, re.I):
                        level = "WARNING"
                    elif re.search(r"\bDEBUG\b", raw, re.I):
                        level = "DEBUG"
                    elif re.search(r"WIN|KÂR|PROFIT|LONG|SHORT|ENTRY|OPEN", raw):
                        level = "TRADE"
                    elif re.search(r"LOSS|STOP|CLOSE|KAPAND", raw):
                        level = "CLOSE"
                    lines_out.append({"text": raw, "level": level})
                return jsonify({"ok": True, "data": lines_out, "path": path, "total": len(lines_out)})
            except Exception:
                continue

    # Fallback: DB'den son trade'ler
    try:
        with get_conn() as conn:
            rows = conn.execute(
                "SELECT symbol, direction, close_reason, net_pnl, close_time "
                "FROM trades WHERE close_time IS NOT NULL ORDER BY id DESC LIMIT ?", (n,)
            ).fetchall()
        for r in reversed(rows):
            r = dict(r)
            pnl = r.get("net_pnl", 0) or 0
            level = "TRADE" if pnl > 0 else "CLOSE"
            lines_out.append({
                "text": f"[{r.get('close_time','')}] {r.get('symbol','')} "
                        f"{r.get('direction','')} → {r.get('close_reason','?').upper()} "
                        f"PNL: {pnl:+.4f}$",
                "level": level,
            })
        return jsonify({"ok": True, "data": lines_out, "path": "db_fallback", "total": len(lines_out)})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e), "data": []}), 500


# ── /api/coin_library ─────────────────────────────────────────────────────────
@app.route("/api/coin_library")
def api_coin_library():
    try:
        with get_conn() as conn:
            has_table = conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name='coin_params'"
            ).fetchone()
            if not has_table:
                return jsonify({"ok": True, "data": [], "note": "coin_params tablosu henüz oluşturulmadı"})
            rows = conn.execute("""
                SELECT symbol,
                       COALESCE(volatility_profile, 'normal') as profile,
                       sl_atr_mult, tp_atr_mult, risk_pct, max_leverage,
                       enabled, updated_at
                FROM coin_params
                ORDER BY symbol ASC
            """).fetchall()
        return jsonify({"ok": True, "data": [dict(r) for r in rows], "total": len(rows)})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500


@app.route("/api/coin_library/<symbol>", methods=["POST"])
def api_coin_library_update(symbol):
    try:
        data    = request.get_json() or {}
        allowed = ["enabled", "volatility_profile", "sl_atr_mult", "tp_atr_mult", "risk_pct", "max_leverage"]
        updates = {k: v for k, v in data.items() if k in allowed}
        if not updates:
            return jsonify({"ok": False, "error": "Güncellenecek alan yok"}), 400
        sets = ", ".join(f"{k}=?" for k in updates)
        vals = list(updates.values()) + [symbol]
        with get_conn() as conn:
            conn.execute(f"UPDATE coin_params SET {sets} WHERE symbol=?", vals)
        return jsonify({"ok": True, "symbol": symbol, "updated": updates})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500


# ── /api/daily_pnl ────────────────────────────────────────────────────────────
@app.route("/api/daily_pnl")
def api_daily_pnl():
    try:
        days = int(request.args.get("days", 30))
        data = dashboard_service.get_calendar_data(days)
        return jsonify({"ok": True, "data": data})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500


# ── /api/weekly ───────────────────────────────────────────────────────────────
@app.route("/api/weekly")
def api_weekly():
    try:
        weeks = int(request.args.get("weeks", 8))
        data  = dashboard_service.get_weekly_data(weeks)
        return jsonify({"ok": True, "data": data})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500


# ── /api/ax_status ────────────────────────────────────────────────────────────
@app.route("/api/ax_status")
def api_ax_status():
    try:
        data = dashboard_service.get_ax_status()
        return jsonify({"ok": True, "data": data})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500


# ── /api/coin_profiles ────────────────────────────────────────────────────────
@app.route("/api/coin_profiles")
def api_coin_profiles():
    try:
        with get_conn() as conn:
            rows = conn.execute("""
                SELECT symbol, sample_size as trade_count,
                       ROUND(win_rate*100,1) as win_rate_pct,
                       avg_r, profit_factor, danger_score, fakeout_rate,
                       best_session, last_updated as updated_at
                FROM coin_profiles
                ORDER BY sample_size DESC, danger_score DESC
            """).fetchall()
        return jsonify({"ok": True, "data": [dict(r) for r in rows]})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500


# ── /api/signal_stats ─────────────────────────────────────────────────────────
@app.route("/api/signal_stats")
def api_signal_stats():
    """ALLOW/VETO/WATCH dağılımı — 'decision' sütunu kullanılır."""
    try:
        days = int(request.args.get("days", 1))
        with get_conn() as conn:
            rows = conn.execute(
                "SELECT decision, COUNT(*) as cnt "
                "FROM signal_candidates "
                "WHERE created_at >= datetime('now', ?) "
                "GROUP BY decision",
                (f"-{days} days",),
            ).fetchall()
        stats = {"ALLOW": 0, "VETO": 0, "WATCH": 0, "total": 0}
        for dec, cnt in rows:
            if dec in stats:
                stats[dec] = cnt
            stats["total"] += cnt
        return jsonify({"ok": True, "data": stats})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500


# ── /api/signal_funnel ────────────────────────────────────────────────────────
@app.route("/api/signal_funnel")
def api_signal_funnel():
    try:
        with get_conn() as conn:
            scanned = conn.execute("SELECT COUNT(*) FROM scanned_coins").fetchone()[0] or 0
            candidate = conn.execute("SELECT COUNT(*) FROM candidate_signals").fetchone()[0] or 0
            watchlist = conn.execute(
                "SELECT COUNT(*) FROM candidate_signals WHERE lifecycle_stage IN ('APPROVED_FOR_WATCHLIST','APPROVED_FOR_TELEGRAM','APPROVED_FOR_TRADE','OPENED','MANAGED','CLOSED')"
            ).fetchone()[0] or 0
            telegram = conn.execute(
                "SELECT COUNT(*) FROM telegram_messages WHERE status IN ('queued','sent')"
            ).fetchone()[0] or 0
            trade = conn.execute("SELECT COUNT(*) FROM trades").fetchone()[0] or 0
            wins = conn.execute("SELECT COUNT(*) FROM trades WHERE net_pnl > 0 AND close_time IS NOT NULL").fetchone()[0] or 0
            losses = conn.execute("SELECT COUNT(*) FROM trades WHERE net_pnl <= 0 AND close_time IS NOT NULL").fetchone()[0] or 0
        try:
            from dashboard_service import get_learning_metrics
            learned = get_learning_metrics(days=int(request.args.get("days", "14")))
        except Exception:
            learned = {}

        return jsonify({
            "ok": True,
            "data": {
                "scanned": scanned,
                "candidate": candidate,
                "watchlist": watchlist,
                "telegram": telegram,
                "telegram_sent": telegram,
                "trade": trade,
                "wins": wins,
                "losses": losses,
                "funnel_labels": "Scanned → Candidate → Watchlist → Telegram → Executed → Win/Loss",
            },
            "learning": learned,
        })
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500


# ── /api/learning_metrics ─────────────────────────────────────────────────────
@app.route("/api/learning_metrics")
def api_learning_metrics():
    try:
        from dashboard_service import get_learning_metrics
        days = int(request.args.get("days", "14"))
        data = get_learning_metrics(days=days)
        return jsonify({"ok": True, "data": data})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500


# ── /api/scalp_signal_stats ───────────────────────────────────────────────────
@app.route("/api/scalp_signal_stats")
def api_scalp_signal_stats():
    """A+/A/B/C dağılımı."""
    try:
        from database import get_daily_signal_count
        stats = get_daily_signal_count()
        return jsonify({"ok": True, "data": stats})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500


# ── /api/paper_state ──────────────────────────────────────────────────────────
@app.route("/api/paper_state")
def api_paper_state():
    try:
        state_file = os.path.join(os.path.dirname(os.path.abspath(__file__)), "paper_state.json")
        if os.path.exists(state_file):
            with open(state_file) as f:
                state = json.load(f)
            return jsonify({"ok": True, "data": state})
        return jsonify({"ok": False, "error": "State file not found"}), 404
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500


# ── /api/history ──────────────────────────────────────────────────────────────
@app.route("/api/history")
def api_history():
    """Kapalı trade geçmişi — sayfalı."""
    try:
        page  = int(request.args.get("page", 1))
        limit = int(request.args.get("limit", request.args.get("per_page", 20)))
        all_trades  = get_closed_trades(limit=10000, valid_only=False)
        total_count = len(all_trades)
        total_pages = max(1, (total_count + limit - 1) // limit)
        page   = max(1, min(page, total_pages))
        offset = (page - 1) * limit
        trades = all_trades[offset:offset + limit]
        result = []
        for t in trades:
            dur_str, dur_min = _fmt_duration(t.get("open_time"), t.get("close_time"))
            result.append({**t, "duration_str": dur_str, "duration_min": dur_min})
        return jsonify({
            "ok": True, "data": result,
            "page": page, "total_pages": total_pages,
            "total_count": total_count, "limit": limit,
        })
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500


# ── /api/signal_archive ───────────────────────────────────────────────────────
@app.route("/api/signal_archive")
def api_signal_archive():
    """Geçmiş sinyal adayları — sayfalı."""
    try:
        page  = int(request.args.get("page", 1))
        limit = int(request.args.get("limit", 10))

        with get_conn() as conn:
            total_count = conn.execute("SELECT COUNT(*) FROM signal_candidates").fetchone()[0]
            total_pages = max(1, (total_count + limit - 1) // limit)
            page = max(1, min(page, total_pages))
            offset = (page - 1) * limit

            rows = conn.execute("""
                SELECT
                    sc.*,
                    pr.max_favorable_excursion as mfe,
                    pr.max_adverse_excursion as mae,
                    pr.would_have_won
                FROM signal_candidates sc
                LEFT JOIN paper_results pr ON sc.uuid = pr.signal_id
                ORDER BY sc.id DESC
                LIMIT ? OFFSET ?
            """, (limit, offset)).fetchall()

            signals = [dict(r) for r in rows]

        return jsonify({
            "ok": True,
            "data": signals,
            "page": page,
            "total_pages": total_pages,
            "total_count": total_count,
            "limit": limit,
        })
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500


# ── /api/paper/stats ──────────────────────────────────────────────────────────
@app.route("/api/paper/stats")
def api_paper_stats():
    try:
        with get_conn() as conn:
            ghost = conn.execute("""
                SELECT tracked_from, COUNT(*) as total,
                       SUM(CASE WHEN outcome LIKE 'TP%' THEN 1 ELSE 0 END) as wins,
                       SUM(CASE WHEN outcome='SL' THEN 1 ELSE 0 END) as losses,
                       ROUND(AVG(outcome_pnl_r),3) as avg_r
                FROM paper_results WHERE outcome IS NOT NULL
                GROUP BY tracked_from
            """).fetchall()
            bal = conn.execute("SELECT balance FROM paper_account LIMIT 1").fetchone()
        return jsonify({
            "ok": True,
            "balance": float(bal[0]) if bal else 250.0,
            "ghost_tracking": [dict(zip(
                ["tracked_from","total","wins","losses","avg_r"], r
            )) for r in ghost],
        })
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500


# ── /api/circuit_breaker ──────────────────────────────────────────────────────
@app.route("/api/circuit_breaker")
def api_circuit_breaker():
    try:
        with get_conn() as conn:
            row = conn.execute(
                "SELECT value FROM bot_status WHERE key='circuit_breaker_until'"
            ).fetchone()
        cb_until = row[0] if row else None
        active, remaining = False, 0
        if cb_until:
            until = datetime.fromisoformat(cb_until)
            if until.tzinfo is None: until = until.replace(tzinfo=timezone.utc)
            now = datetime.now(timezone.utc)
            if now < until:
                active = True
                remaining = int((until - now).total_seconds() / 60)
        return jsonify({"ok": True, "active": active, "remaining_minutes": remaining})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500


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


# ── /api/ghost-stats ────────────────────────────────────────────────────────
@app.route("/api/ghost-stats")
def api_ghost_stats():
    try:
        from core.ghost_learning import get_ghost_learning_stats, calculate_dynamic_ghost_weight
        stats = get_ghost_learning_stats()
        dynamic_weight = calculate_dynamic_ghost_weight()

        with get_conn() as conn:
            # Son 7 gunluk GHOST_WIN / GHOST_LOSS
            weekly = conn.execute("""
                SELECT DATE(created_at) as day,
                       SUM(CASE WHEN hit_tp=1 THEN 1 ELSE 0 END) as wins,
                       SUM(CASE WHEN hit_stop_first=1 THEN 1 ELSE 0 END) as losses
                FROM paper_results
                WHERE status='finalized'
                  AND created_at >= DATE('now', '-7 days')
                GROUP BY day ORDER BY day
            """).fetchall()

            # Coin bazli ghost dogruluk (en cok correct_skip yapilan coinler)
            top_correct = conn.execute("""
                SELECT symbol,
                       COUNT(*) as total,
                       SUM(CASE WHEN skip_decision_correct=1 THEN 1 ELSE 0 END) as correct
                FROM paper_results
                WHERE status='finalized' AND skip_decision_correct IS NOT NULL
                GROUP BY symbol
                HAVING total >= 3
                ORDER BY (correct * 1.0 / total) DESC, total DESC
                LIMIT 5
            """).fetchall()

        accuracy = 0.0
        total = stats.get("total", 0)
        if total > 0:
            accuracy = round(stats.get("correct_skips", 0) / total * 100, 1)

        return jsonify({
            "ok": True,
            "total": total,
            "ghost_wins": stats.get("ghost_wins", 0),
            "ghost_losses": stats.get("ghost_losses", 0),
            "ghost_win_rate": round(stats.get("ghost_win_rate", 0) * 100, 1),
            "correct_skips": stats.get("correct_skips", 0),
            "accuracy_pct": accuracy,
            "avg_mfe_r": stats.get("avg_mfe", 0),
            "avg_mae_r": stats.get("avg_mae", 0),
            "dynamic_weight": round(dynamic_weight, 3),
            "weekly": [{"day": r[0], "wins": r[1], "losses": r[2]} for r in weekly],
            "top_correct_skips": [
                {
                    "symbol": r[0],
                    "total": r[1],
                    "correct": r[2],
                    "accuracy": round(r[2] / r[1] * 100, 1) if r[1] > 0 else 0,
                }
                for r in top_correct
            ],
        })
    except Exception as e:
        logger.error(f"[API] /api/ghost-stats hatasi: {e}")
        return jsonify({"ok": False, "error": str(e)}), 500


# ── /api/ghost-replay ───────────────────────────────────────────────────────
@app.route("/api/ghost-replay")
def api_ghost_replay():
    """Son 24 saatin ghost sonuclarini veto sebebiyle gruplandirarak dondurur."""
    try:
        with get_conn() as conn:
            rows = conn.execute("""
                SELECT id, symbol, direction,
                       preview_entry, preview_sl, preview_tp1,
                       tracked_from, hit_tp, hit_stop_first,
                       max_favorable_excursion, max_adverse_excursion,
                       first_touch, created_at, finalized_at
                FROM paper_results
                WHERE created_at >= DATETIME('now', '-24 hours')
                ORDER BY created_at DESC
                LIMIT 100
            """).fetchall()

        result = []
        for r in rows:
            outcome = "pending"
            if r["hit_tp"]:
                outcome = "GHOST_WIN"
            elif r["hit_stop_first"]:
                outcome = "GHOST_LOSS"
            elif r["first_touch"] == "neither_horizon":
                outcome = "EXPIRED"

            result.append({
                "id":            r["id"],
                "symbol":        r["symbol"],
                "direction":     r["direction"],
                "entry":         r["preview_entry"],
                "sl":            r["preview_sl"],
                "tp1":           r["preview_tp1"],
                "tracked_from":  r["tracked_from"],
                "outcome":       outcome,
                "mfe_r":         r["max_favorable_excursion"],
                "mae_r":         r["max_adverse_excursion"],
                "created_at":    r["created_at"],
                "finalized_at":  r["finalized_at"],
            })

        return jsonify({"ok": True, "count": len(result), "items": result})
    except Exception as e:
        logger.error(f"[API] /api/ghost-replay hatasi: {e}")
        return jsonify({"ok": False, "error": str(e)}), 500


# ── /api/equity-curve ───────────────────────────────────────────────────────
@app.route("/api/equity-curve")
def api_equity_curve():
    """Son 30 gunluk kumulatif PnL serisi."""
    try:
        with get_conn() as conn:
            initial_balance = 250.0
            bal_row = conn.execute(
                "SELECT balance FROM balance_ledger ORDER BY id ASC LIMIT 1"
            ).fetchone()
            if bal_row:
                initial_balance = float(bal_row[0])

            daily = conn.execute("""
                SELECT DATE(closed_at) as day,
                       SUM(COALESCE(accumulated_pnl, realized_pnl, 0)) as daily_pnl
                FROM trades
                WHERE status='CLOSED'
                  AND closed_at >= DATE('now', '-30 days')
                  AND closed_at IS NOT NULL
                GROUP BY day
                ORDER BY day
            """).fetchall()

        cum_pnl = 0.0
        points = []
        for row in daily:
            cum_pnl += float(row[1] or 0)
            points.append({
                "day":     row[0],
                "pnl":     round(float(row[1] or 0), 4),
                "cum_pnl": round(cum_pnl, 4),
                "balance": round(initial_balance + cum_pnl, 4),
            })

        current_balance = initial_balance + cum_pnl
        pct_change = round((cum_pnl / initial_balance * 100), 2) if initial_balance > 0 else 0

        return jsonify({
            "ok": True,
            "initial_balance": initial_balance,
            "current_balance": round(current_balance, 4),
            "total_pnl": round(cum_pnl, 4),
            "pct_change": pct_change,
            "points": points,
        })
    except Exception as e:
        logger.error(f"[API] /api/equity-curve hatasi: {e}")
        return jsonify({"ok": False, "error": str(e)}), 500


# ── Server başlatma ─────────────────────────────────────────────────

def main():
    """DB'yi hazırla ve Flask server'ı başlat."""
    database.init_db()
    database.migrate_db()
    logger.info(
        "Dashboard API başlatılıyor %s:%s",
        config.FLASK_HOST, config.FLASK_PORT,
    )
    socketio.run(
        app,
        host=config.FLASK_HOST,
        port=config.FLASK_PORT,
        debug=False,
        use_reloader=False,
        log_output=False,
        allow_unsafe_werkzeug=True,
    )


if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s – %(message)s",
    )
    main()
