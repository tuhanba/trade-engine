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
        def run(self, app, **kw):
            kw.pop("log_output", None)
            app.run(**kw)

import config
import database
import dashboard_service
from database import get_conn, get_closed_trades, get_open_trades, get_paper_balance

logger = logging.getLogger("ax.app")

N8N_AVAILABLE = False

app = Flask(__name__)
app.secret_key = getattr(config, "SECRET_KEY", "ax_secret_2026")
socketio = SocketIO(app)

# ── IP Whitelist ──────────────────────────────────────────────────────
# Varsayılan: devre dışı (boş string veya ALLOWED_IPS env var set edilmemiş)
# Etkinleştirmek için: ALLOWED_IPS=192.168.1.100,10.0.0.1
import os as _os

_ALLOWED_IPS = set(filter(None, _os.getenv("ALLOWED_IPS", "").split(",")))

def _get_client_ip() -> str:
    """Nginx proxy veya doğrudan bağlantı için gerçek IP'yi döner."""
    forwarded_for = request.headers.get("X-Forwarded-For", "")
    if forwarded_for:
        return forwarded_for.split(",")[0].strip()
    real_ip = request.headers.get("X-Real-IP", "")
    if real_ip:
        return real_ip
    return request.remote_addr or "0.0.0.0"

def _check_ip():
    """IP whitelist — sadece ALLOWED_IPS env var set edilmişse aktif."""
    if not _ALLOWED_IPS:
        return  # Whitelist devre dışı (varsayılan)
    if "0.0.0.0" in _ALLOWED_IPS:
        return  # Açık erişim
    client_ip = _get_client_ip()
    if client_ip not in _ALLOWED_IPS:
        logger.warning(f"IP engellendi: {client_ip} (İzin verilenler: {_ALLOWED_IPS})")
        from flask import abort
        abort(403)

@app.before_request
def check_access():
    # /api/* ve /stream için IP kontrolü (ALLOWED_IPS set edilmişse)
    if request.path.startswith("/api/") or request.path == "/stream":
        _check_ip()
    # /  (dashboard) herkese açık

# ── CORS ─────────────────────────────────────────────────────────────

@app.after_request
def add_cors_headers(response):
    response.headers["Access-Control-Allow-Origin"] = "*"
    response.headers["Access-Control-Allow-Headers"] = "Content-Type, Authorization"
    response.headers["Access-Control-Allow-Methods"] = "GET, OPTIONS"
    if request.path.startswith("/api/"):
        response.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
        response.headers["Pragma"] = "no-cache"
        response.headers["Expires"] = "0"
    return response


# ── Yardımcı ─────────────────────────────────────────────────────────

def _ok(data):
    return jsonify({"ok": True, "data": data, "ts": datetime.now(timezone.utc).isoformat()})


def _error(msg: str, code: int = 200):
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
        health_data = dashboard_service.get_health()
        resp = {
            "ok": health_data.get("db_connected", False),
            "db_connected": health_data.get("db_connected", False),
            "data": health_data,
            "ts": datetime.now(timezone.utc).isoformat(),
        }
        return jsonify(resp)
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
    def _get_safe_count(sql, params=()):
        try:
            with get_conn() as conn:
                return conn.execute(sql, params).fetchone()[0] or 0
        except: return 0
    def _get_safe_status(key):
        try:
            with get_conn() as conn:
                return int(conn.execute("SELECT value FROM bot_status WHERE key=?", (key,)).fetchone()[0])
        except: return 0

    try:
        stats = dashboard_service.get_stats()
        ax_status = dashboard_service.get_ax_status()
        return _ok({
            **stats,
            "balance":         stats.get("balance", ax_status.get("paper_balance", 0)),
            "initial_balance": stats.get("initial_balance", ax_status.get("initial_balance", 500.0)),
            "bot_running":     ax_status.get("bot_running", False),
            "execution_mode":  ax_status.get("execution_mode", "paper"),
            # BUG FIX: Frontend için zorunlu alanlar — get_dashboard_stats'dan gelir
            "win_trades":      stats.get("win_trades", 0),
            "loss_trades":     stats.get("loss_trades", 0),
            "win_rate":        stats.get("win_rate", stats.get("winrate", 0)),
            "today_pnl":       stats.get("today_pnl", 0),
            "funnel": {
                "scanned": _get_safe_status("pipeline_scanned"),
                "candidate": _get_safe_status("pipeline_candidate"),
                "watchlist": _get_safe_count("SELECT COUNT(*) FROM signal_candidates WHERE status NOT IN ('NEW','rejected')"),
                "telegram": _get_safe_count("SELECT COUNT(*) FROM telegram_messages WHERE status IN ('queued','sent')"),
                "trade": stats.get("total_trades", 0),
                "wins": stats.get("win_trades", 0),
                "losses": stats.get("loss_trades", 0)
            }
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
        with get_conn() as conn:
            gs_total = conn.execute("SELECT COUNT(*) FROM ghost_signals").fetchone()[0]
            gs_sim   = conn.execute("SELECT COUNT(*) FROM ghost_signals WHERE simulated=1").fetchone()[0]
            gr_wins  = conn.execute("SELECT COUNT(*) FROM ghost_results WHERE virtual_outcome='WIN'").fetchone()[0]
            gr_loss  = conn.execute("SELECT COUNT(*) FROM ghost_results WHERE virtual_outcome='LOSS'").fetchone()[0]
            gr_avg_r = conn.execute("SELECT AVG(virtual_pnl_r) FROM ghost_results WHERE virtual_outcome IN ('WIN','LOSS')").fetchone()[0] or 0
            try:
                suggestions = conn.execute(
                    "SELECT COUNT(*) FROM ghost_suggestions WHERE applied=0"
                ).fetchone()[0]
            except Exception:
                suggestions = 0

            try:
                top_patterns = conn.execute("""
                    SELECT g.trigger_type, g.coin,
                           COUNT(*) as n,
                           SUM(CASE WHEN r.virtual_outcome='WIN' THEN 1.0 ELSE 0 END)*100/COUNT(*) as wr,
                           AVG(r.virtual_pnl_r) as avg_r
                    FROM ghost_signals g
                    JOIN ghost_results r ON g.id=r.ghost_id
                    WHERE r.virtual_outcome IN ('WIN','LOSS')
                    GROUP BY g.trigger_type, g.coin
                    HAVING COUNT(*) >= 3
                    ORDER BY avg_r DESC LIMIT 5
                """).fetchall()
            except Exception:
                top_patterns = []

        vwr = round(gr_wins / (gr_wins + gr_loss) * 100, 1) if (gr_wins + gr_loss) > 0 else 0
        return _ok({
            "ghost_total": gs_total,
            "ghost_simulated": gs_sim,
            "ghost_pending": gs_total - gs_sim,
            "virtual_wins": gr_wins,
            "virtual_losses": gr_loss,
            "virtual_wr": vwr,
            "avg_r": round(gr_avg_r, 3),
            "pending_suggestions": suggestions,
            "top_patterns": [
                {"trigger": r[0], "coin": r[1], "count": r[2],
                 "wr": round(r[3], 1), "avg_r": round(r[4], 2)}
                for r in top_patterns
            ],
        })
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
        return jsonify({"ok": True, "data": {
            "sl_atr_mult":        getattr(config, "SL_ATR_MULT", 1.8),
            "tp1_r":              getattr(config, "TP1_R", 1.5),
            "tp2_r":              getattr(config, "TP2_R", 2.5),
            "tp3_r":              getattr(config, "TP3_R", 4.0),
            "min_rr":             getattr(config, "MIN_RR", 1.5),
            "min_sl_pct":         getattr(config, "MIN_SL_PCT", 0.015),
            "risk_pct":           getattr(config, "RISK_PCT", 1.0),
            "max_open_trades":    getattr(config, "MAX_OPEN_TRADES", 5),
            "trade_threshold":    getattr(config, "TRADE_THRESHOLD", 55.0),
            "telegram_threshold": getattr(config, "TELEGRAM_THRESHOLD", 35.0),
            "data_threshold":     getattr(config, "DATA_THRESHOLD", 20.0),
            "scan_interval":      getattr(config, "SCAN_INTERVAL", 45),
            "max_leverage":       getattr(config, "MAX_LEVERAGE", 10),
            "execution_mode":     getattr(config, "EXECUTION_MODE", "paper"),
            "human_mode":         getattr(config, "HUMAN_MODE", False),
            "adx_min":            getattr(config, "ADX_MIN_THRESHOLD", 18),
            "version":            "v6.0",
        }})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500


# ── /api/coin_stats ───────────────────────────────────────────────────────────
@app.route("/api/coin_stats")
def api_coin_stats():
    try:
        with get_conn() as conn:
            has_table = conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name='coin_profiles'"
            ).fetchone()
            if not has_table:
                return jsonify({"ok": True, "data": [], "note": "coin_profiles tablosu henüz oluşturulmadı"})
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
        return jsonify({"ok": True, "data": [], "error": str(e)})


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
        os.path.join(os.path.dirname(os.path.abspath(__file__)), "db", "bot.log"),
        os.path.join(os.path.dirname(os.path.abspath(__file__)), "bot.log"),
        os.path.join(getattr(config, "LOG_DIR", "logs"), "ax_bot.log"),
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
                with open(path, "rb") as _f:
                    _f.seek(0, 2)
                    _fsize = _f.tell()
                    _read = min(100_000, _fsize)
                    _f.seek(-_read, 2)
                    _raw = _f.read().decode("utf-8", errors="replace")
                raw_lines = _raw.splitlines()[-n:]
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
                return jsonify({
                    "ok": True,
                    "data": {
                        "lines": [l["text"] for l in lines_out],
                        "items": lines_out,
                    },
                    "path": path,
                    "total": len(lines_out)
                })
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
        return jsonify({
            "ok": True,
            "data": {
                "lines": [l["text"] for l in lines_out],
                "items": lines_out,
            },
            "path": "db_fallback",
            "total": len(lines_out)
        })
    except Exception as e:
        return jsonify({"ok": False, "error": str(e), "data": []}), 500

# ── /api/telegram_logs ────────────────────────────────────────────────────────
@app.route("/api/telegram_logs")
def api_telegram_logs():
    try:
        n = min(int(request.args.get("n", 60)), 200)
        with get_conn() as conn:
            rows = conn.execute(
                "SELECT symbol, text, status, created_at "
                "FROM telegram_messages "
                "ORDER BY id DESC LIMIT ?", (n,)
            ).fetchall()
        
        lines_out = []
        for r in reversed(rows):
            r = dict(r)
            sym = r.get("symbol", "")
            status = r.get("status", "")
            t = r.get("created_at", "")
            txt = r.get("text", "").replace("\n", " - ")
            lines_out.append({
                "text": f"[{t}] {sym} ({status.upper()}): {txt}",
                "level": "TRADE" if status == "sent" else "WARNING" if status == "queued" else "ERROR"
            })
            
        return jsonify({
            "ok": True,
            "data": {
                "lines": [l["text"] for l in lines_out],
                "items": lines_out,
            },
            "total": len(lines_out)
        })
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
            has_table = conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name='coin_profiles'"
            ).fetchone()
            if not has_table:
                return jsonify({"ok": True, "data": [], "note": "coin_profiles tablosu henüz oluşturulmadı"})
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
        return jsonify({"ok": True, "data": [], "error": str(e)})


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
            def safe_count(sql, params=()):
                try: return conn.execute(sql, params).fetchone()[0] or 0
                except: return 0

            def safe_status(key):
                try: return int(conn.execute("SELECT value FROM bot_status WHERE key=?", (key,)).fetchone()[0])
                except: return 0

            scanned   = safe_status("pipeline_scanned")
            candidate = safe_status("pipeline_candidate")
            watchlist = safe_count(
                "SELECT COUNT(*) FROM signal_candidates WHERE status NOT IN ('NEW','rejected')"
            )
            telegram  = safe_count(
                "SELECT COUNT(*) FROM telegram_messages WHERE status IN ('queued','sent')"
            )
            trade   = safe_count("SELECT COUNT(*) FROM trades")
            wins    = safe_count(
                "SELECT COUNT(*) FROM trades WHERE net_pnl > 0 AND close_time IS NOT NULL"
            )
            losses  = safe_count(
                "SELECT COUNT(*) FROM trades WHERE net_pnl <= 0 AND close_time IS NOT NULL"
            )
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
        # DB'den paper account durumu döndür
        with get_conn() as conn:
            bal = conn.execute(
                "SELECT balance FROM paper_account LIMIT 1"
            ).fetchone()
            open_count = conn.execute(
                "SELECT COUNT(*) FROM trades WHERE LOWER(status) IN ('open','tp1_hit','runner')"
            ).fetchone()[0]
            closed_count = conn.execute(
                "SELECT COUNT(*) FROM trades WHERE LOWER(status)='closed'"
            ).fetchone()[0]
        return jsonify({
            "ok": True,
            "data": {
                "balance": float(bal[0]) if bal else 500.0,
                "open_trades": open_count,
                "closed_trades": closed_count,
            }
        })
    except Exception as e:
        return jsonify({"ok": True, "data": {"balance": 500.0}, "error": str(e)})


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
                LEFT JOIN paper_results pr
                    ON (sc.id = CAST(pr.candidate_id AS INTEGER)
                        OR (sc.uuid IS NOT NULL AND sc.uuid = pr.signal_id))
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
            try:
                ghost = conn.execute("""
                    SELECT tracked_from,
                           COUNT(*) as total,
                           SUM(CASE WHEN would_have_won=1 THEN 1 ELSE 0 END) as wins,
                           SUM(CASE WHEN hit_stop_first=1 THEN 1 ELSE 0 END) as losses,
                           ROUND(AVG(max_favorable_excursion),3) as avg_mfe
                    FROM paper_results
                    WHERE status='finalized'
                    GROUP BY tracked_from
                """).fetchall()
            except Exception:
                ghost = []
            bal = conn.execute(
                "SELECT balance FROM paper_account LIMIT 1"
            ).fetchone()
        return jsonify({
            "ok": True,
            "balance": float(bal[0]) if bal else 500.0,
            "ghost_tracking": [dict(zip(
                ["tracked_from","total","wins","losses","avg_mfe"], r
            )) for r in ghost],
        })
    except Exception as e:
        return jsonify({"ok": True, "balance": 500.0, "ghost_tracking": [], "error": str(e)})


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


# ── /api/heatmap ──────────────────────────────────────────────────────────────
@app.route("/api/heatmap")
def api_heatmap():
    try:
        with get_conn() as conn:
            rows = conn.execute("""
                SELECT 
                    symbol,
                    strftime('%H', close_time) as hour_str,
                    SUM(net_pnl) as total_pnl
                FROM trades
                WHERE status = 'closed' AND close_time IS NOT NULL
                GROUP BY symbol, strftime('%H', close_time)
            """).fetchall()
            
            data = []
            for r in rows:
                hour = r["hour_str"]
                if hour is not None:
                    data.append({
                        "symbol": r["symbol"],
                        "hour": int(hour),
                        "pnl": float(r["total_pnl"] or 0)
                    })
        return jsonify({"ok": True, "data": data})
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
        err_count = 0
        while True:
            try:
                payload = {
                    "health": dashboard_service.get_health(),
                    "live":   dashboard_service.get_live_trades(),
                    "stats":  dashboard_service.get_stats(),
                    "ts":     int(time.time()),
                }
                yield f"data: {json.dumps(payload)}\n\n"
                err_count = 0
            except GeneratorExit:
                return
            except Exception as exc:
                err_count += 1
                logger.warning("SSE hata #%d: %s", err_count, exc)
                try:
                    yield f"data: {json.dumps({'error': str(exc), 'ts': int(time.time())})}\n\n"
                except Exception:
                    return
                if err_count >= 10:
                    return
            try:
                time.sleep(1)
            except GeneratorExit:
                return

    return Response(
        stream_with_context(event_generator()),
        mimetype="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
            "Connection": "keep-alive",
        },
    )


# ── /api/diagnostics ────────────────────────────────────────────────────────
@app.route("/api/diagnostics")
def api_diagnostics():
    try:
        from core.signal_diagnostics import get_summary
        return jsonify({"ok": True, "data": get_summary()})
    except Exception as e:
        return _error(str(e))


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
            initial_balance = 500.0
            for q in [
                "SELECT balance FROM balance_ledger ORDER BY id ASC LIMIT 1",
                "SELECT balance FROM paper_account ORDER BY id ASC LIMIT 1",
            ]:
                try:
                    row = conn.execute(q).fetchone()
                    if row:
                        initial_balance = float(row[0])
                        break
                except Exception:
                    continue

            try:
                daily = conn.execute("""
                    SELECT DATE(close_time) as day,
                           SUM(COALESCE(net_pnl, realized_pnl, 0)) as daily_pnl
                    FROM trades
                    WHERE LOWER(status)='closed'
                      AND close_time >= DATE('now', '-30 days')
                      AND close_time IS NOT NULL
                    GROUP BY day ORDER BY day
                """).fetchall()
            except Exception:
                daily = []

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

        return jsonify({
            "ok": True,
            "initial_balance": initial_balance,
            "current_balance": round(initial_balance + cum_pnl, 4),
            "total_pnl": round(cum_pnl, 4),
            "pct_change": round((cum_pnl / initial_balance * 100), 2) if initial_balance > 0 else 0,
            "points": points,
        })
    except Exception as e:
        logger.error(f"[API] /api/equity-curve hatasi: {e}")
        return jsonify({"ok": True, "points": [], "total_pnl": 0, "error": str(e)})


# ── Server başlatma ─────────────────────────────────────────────────

def main():
    """DB'yi hazırla ve Flask server'ı başlat."""
    database.init_db()
    database.migrate_db()
    logger.info(
        "Dashboard API başlatılıyor %s:%s",
        config.FLASK_HOST, config.FLASK_PORT,
    )
    # WebSocket event manager'ı başlat — dashboard real-time için şart
    try:
        from websocket_events import initialize_websocket_events
        initialize_websocket_events(socketio)
        logger.info("WebSocket event manager başlatıldı")
    except Exception as _wse:
        logger.warning(f"WebSocket event manager başlatılamadı: {_wse}")
    socketio.run(
        app,
        host=config.FLASK_HOST,
        port=config.FLASK_PORT,
        debug=False,
        log_output=False,
    )


if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s – %(message)s",
    )
    main()
