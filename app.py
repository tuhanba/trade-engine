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
socketio = SocketIO(app, cors_allowed_origins="*")

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
        return None
    if "0.0.0.0" in _ALLOWED_IPS:
        return None
    client_ip = _get_client_ip()
    if client_ip not in _ALLOWED_IPS:
        logger.warning(f"IP engellendi: {client_ip} (İzin verilenler: {_ALLOWED_IPS})")
        return jsonify({
            "ok": False,
            "error": f"IP Access Denied. Your IP ({client_ip}) is not whitelisted.",
            "client_ip": client_ip
        }), 403
    return None

@app.before_request
def check_access():
    # /api/* ve /stream için IP kontrolü (ALLOWED_IPS set edilmişse)
    if request.path.startswith("/api/") or request.path == "/stream":
        block_response = _check_ip()
        if block_response is not None:
            return block_response
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

@app.route("/api/dashboard_data")
def api_dashboard_data():
    """Faz 6 V2 Dashboard için toplu veri sağlar."""
    try:
        from core.services.macro_service import macro_service
        from database import get_open_trades, get_active_balance
        import dashboard_service
        
        sentiment = macro_service.get_market_sentiment()
        open_trades = get_open_trades()
        real_stats = dashboard_service.get_stats()
        
        daily_pnl = real_stats.get("daily_pnl") or real_stats.get("today_pnl", 0.0)
            
        stats = {
            "total_trades": real_stats.get("total_trades", len(open_trades)),
            "win_rate": real_stats.get("win_rate", 0.0),
            "profit_factor": real_stats.get("profit_factor", 1.0)
        }
        
        try:
            from database import get_market_regime
            regime = get_market_regime() or "NEUTRAL"
        except Exception:
            regime = "NEUTRAL"
            
        return jsonify({
            "market_regime": regime,
            "macro_fng": sentiment.get("fng_value", 50),
            "total_balance": get_active_balance() or 1000.0,
            "daily_pnl": daily_pnl,
            "stats": stats,
            "active_trades": open_trades
        })
    except Exception as e:
        logger.error(f"Dashboard Data API Error: {e}")
        return jsonify({"error": str(e)}), 500

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
        except Exception: return 0
    def _get_safe_status(key):
        try:
            with get_conn() as conn:
                return int(conn.execute("SELECT value FROM bot_status WHERE key=?", (key,)).fetchone()[0])
        except Exception: return 0

    try:
        ax_status = dashboard_service.get_ax_status()
        exec_mode = ax_status.get("execution_mode", "paper")
        stats = dashboard_service.get_stats(exec_mode)
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
                "scanned":   _get_safe_status("pipeline_scanned"),
                "eligible":  _get_safe_status("pipeline_eligible"),
                "trend_ok":  _get_safe_count(
                    "SELECT COUNT(*) FROM signal_events "
                    "WHERE stage='TREND_CHECKED' AND DATE(created_at)=DATE('now')"
                ),
                "trigger_ok": _get_safe_count(
                    "SELECT COUNT(*) FROM signal_events "
                    "WHERE stage='TRIGGER_CHECKED' AND DATE(created_at)=DATE('now')"
                ),
                "risk_ok":   _get_safe_count(
                    "SELECT COUNT(*) FROM signal_events "
                    "WHERE stage='RISK_APPROVED' AND DATE(created_at)=DATE('now')"
                ),
                "risk_reject": _get_safe_count(
                    "SELECT COUNT(*) FROM signal_events "
                    "WHERE stage='RISK_REJECTED' AND DATE(created_at)=DATE('now')"
                ),
                "ai_veto":   _get_safe_count(
                    "SELECT COUNT(*) FROM signal_events "
                    "WHERE stage='AI_VETOED' AND DATE(created_at)=DATE('now')"
                ),
                "executed":  _get_safe_count(
                    "SELECT COUNT(*) FROM signal_events "
                    "WHERE stage='EXECUTED' AND DATE(created_at)=DATE('now')"
                ),
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
        from database import get_active_balance_details, get_paper_balance
        details = get_active_balance_details()
        paper_balance = get_paper_balance()
        return jsonify({"ok": True, "data": {
            "paper_balance":  round(paper_balance, 4),
            "usdt_balance":   round(details.get("total", paper_balance), 4),
            "usdt_available": round(details.get("available", paper_balance), 4),
            "execution_mode": details.get("execution_mode", "paper"),
        }})
    except Exception as exc:
        return _error(str(exc))


@app.route("/api/balance_ledger")
def api_balance_ledger():
    """Retrieve chronological balance ledger entries."""
    try:
        with get_conn() as conn:
            rows = conn.execute(
                "SELECT id, trade_id, symbol, event_type, amount, balance_before, balance_after, created_at "
                "FROM balance_ledger ORDER BY id ASC"
            ).fetchall()
            ledger_data = [dict(row) for row in rows]
        return _ok(ledger_data)
    except Exception as exc:
        return _error(str(exc))


@app.route("/api/settings/update", methods=["POST"])
def api_settings_update():
    """Dynamically updates system settings in system_state or params table."""
    try:
        from flask import request
        req = request.get_json()
        if not req:
            return _error("JSON gövdesi boş")
        
        key = req.get("key")
        val = req.get("value")
        
        if not key:
            return _error("key eksik")
            
        from config import _DYNAMIC_PARAMS_MAP, _AI_PARAMS_MAP
        db_updated = False
        
        if key in _DYNAMIC_PARAMS_MAP:
            db_key, cast_fn = _DYNAMIC_PARAMS_MAP[key]
            with get_conn() as conn:
                conn.execute(
                    "INSERT OR REPLACE INTO system_state (key, value) VALUES (?, ?)",
                    (db_key, str(val))
                )
                conn.commit()
            db_updated = True
            
        elif key in _AI_PARAMS_MAP:
            db_col, cast_fn = _AI_PARAMS_MAP[key]
            with get_conn() as conn:
                row = conn.execute("SELECT id FROM params ORDER BY id DESC LIMIT 1").fetchone()
                if row:
                    param_id = row[0]
                    conn.execute(
                        f"UPDATE params SET {db_col} = ? WHERE id = ?",
                        (val, param_id)
                    )
                    conn.commit()
                    db_updated = True
        else:
            with get_conn() as conn:
                conn.execute(
                    "INSERT OR REPLACE INTO system_state (key, value) VALUES (?, ?)",
                    (key, str(val))
                )
                conn.commit()
            db_updated = True
            
        if db_updated:
            logger.info(f"[API Settings] Successfully updated config {key} to {val}")
            return _ok({"key": key, "value": val, "status": "updated"})
        else:
            return _error(f"Ayar güncellenemedi: {key}")
            
    except Exception as exc:
        logger.error(f"[API Settings] Update error: {exc}", exc_info=True)
        return _error(str(exc))


# ── /api/config/update ────────────────────────────────────────────────────────
@app.route("/api/config/update", methods=["POST"])
def api_config_update():
    try:
        data = request.get_json() or {}
        key = data.get("key")
        value = data.get("value")
        if not key or value is None:
            return jsonify({"ok": False, "error": "Invalid payload: key and value are required"}), 400
        
        # Valid key checking
        allowed_keys = [
            "trade_threshold", "telegram_threshold", "watchlist_threshold", "data_threshold",
            "tg_execution_mode", "tg_human_mode"
        ]
        if key not in allowed_keys:
            return jsonify({"ok": False, "error": f"Unauthorized configuration parameter: {key}"}), 403
        
        # Save to SQLite system_state table
        with get_conn() as conn:
            conn.execute("""
                INSERT INTO system_state (key, value, updated_at)
                VALUES (?, ?, datetime('now'))
                ON CONFLICT(key) DO UPDATE SET value=?, updated_at=datetime('now')
            """, (key, str(value), str(value)))
            
            # If we updated tg_execution_mode, also write to bot_status key 'tg_execution_mode' for fallback
            if key == "tg_execution_mode":
                conn.execute("""
                    INSERT INTO bot_status (key, value, updated_at)
                    VALUES (?, ?, datetime('now'))
                    ON CONFLICT(key) DO UPDATE SET value=?, updated_at=datetime('now')
                """, (key, str(value), str(value)))
            elif key == "tg_human_mode":
                conn.execute("""
                    INSERT INTO bot_status (key, value, updated_at)
                    VALUES (?, ?, datetime('now'))
                    ON CONFLICT(key) DO UPDATE SET value=?, updated_at=datetime('now')
                """, (key, str(value), str(value)))
                
            conn.commit()
            
        logger.info(f"[Config] Updated config '{key}' to '{value}' via Dashboard API")
        return jsonify({"ok": True, "message": f"Successfully updated {key} to {value}"})
    except Exception as e:
        logger.error(f"[Config] Configuration update failed: {e}")
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
            "drawdown_defensive_pct":      getattr(config, "DRAWDOWN_DEFENSIVE_PCT", 5.0),
            "drawdown_lock_pct":           getattr(config, "DRAWDOWN_LOCK_PCT", 10.0),
            "equity_curve_filter_enabled": getattr(config, "EQUITY_CURVE_FILTER_ENABLED", True),
            "mtf_trend_align_enabled":     getattr(config, "MTF_TREND_ALIGN_ENABLED", True),
            "auto_compounding":            getattr(config, "AUTO_COMPOUNDING", True),
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
        from core.ml_signal_scorer import get_scorer  # P1 BUG FIX: doğru import path
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


# ── /api/pnl_heatmap ─────────────────────────────────────────────────────────
@app.route("/api/pnl_heatmap")
def api_pnl_heatmap():
    try:
        days = int(request.args.get("days", 30))
        with get_conn() as conn:
            rows = conn.execute("""
                SELECT symbol,
                       strftime('%H', close_time) AS hour,
                       SUM(net_pnl) AS total_pnl,
                       COUNT(*) AS trade_count
                FROM trades
                WHERE LOWER(status) = 'closed' AND close_time >= datetime('now', ?)
                GROUP BY symbol, hour
            """, (f"-{days} days",)).fetchall()
        
        data = [dict(r) for r in rows]
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
                       best_session,
                       COALESCE(last_updated, updated_at) as updated_at
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
                except Exception: return 0

            def safe_status(key):
                try: return int(conn.execute("SELECT value FROM bot_status WHERE key=?", (key,)).fetchone()[0])
                except Exception: return 0

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


# ── /api/system/maintenance ──────────────────────────────────────────────────
@app.route("/api/system/maintenance", methods=["POST"])
def api_system_maintenance():
    """Veritabanını optimize eder ve Redis önbelleğini temizler."""
    try:
        from core import redis_state
        # 1. SQLite Vacuum & Reindex
        with get_conn() as conn:
            conn.execute("VACUUM")
            conn.execute("REINDEX")
            logger.info("[Maintenance] SQLite veritabanı optimize edildi (VACUUM & REINDEX)")
            
        # 2. Redis Flush
        redis_ok = False
        try:
            if redis_state.available():
                redis_state.flush_db()
                redis_ok = True
                logger.info("[Maintenance] Redis veritabanı temizlendi (flush_db)")
        except Exception as re:
            logger.warning(f"[Maintenance] Redis temizleme hatası: {re}")
            
        return jsonify({
            "ok": True,
            "message": "Sistem bakımı başarıyla tamamlandı. Veritabanı sıkıştırıldı ve Redis temizlendi.",
            "redis_cleared": redis_ok
        })
    except Exception as exc:
        logger.error(f"[Maintenance] Hata: {exc}")
        return jsonify({"ok": False, "error": str(exc)}), 500


# ── /api/ghost-stats ────────────────────────────────────────────────────────
@app.route("/api/ghost-stats")
def api_ghost_stats():
    try:
        from core.ghost_learning import get_ghost_learning_stats, calculate_dynamic_ghost_weight, summarize_ghost_results
        stats = get_ghost_learning_stats()
        g2_summary = summarize_ghost_results()
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
            "total": g2_summary.get("total", 0),
            "ghost_wins": g2_summary.get("wins", 0),
            "ghost_losses": g2_summary.get("losses", 0),
            "ghost_win_rate": g2_summary.get("win_rate", 0),
            "ghost_pnl": g2_summary.get("ghost_pnl", 0.0),
            "top_patterns": g2_summary.get("top_patterns", []),
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
        peak = initial_balance
        for row in daily:
            cum_pnl += float(row[1] or 0)
            current_bal = initial_balance + cum_pnl
            peak = max(peak, current_bal)
            drawdown = ((peak - current_bal) / peak) * 100.0 if peak > 0 else 0.0
            points.append({
                "day":     row[0],
                "pnl":     round(float(row[1] or 0), 4),
                "cum_pnl": round(cum_pnl, 4),
                "balance": round(current_bal, 4),
                "drawdown": round(drawdown, 2),
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


# ── Backtest Manager ────────────────────────────────────────────────────────
import threading
from scripts.backtest_system import BacktestRunner

# Global state for backtester
backtest_state = {
    "status": "idle",       # idle, running, completed, failed
    "progress": 0.0,
    "funnel_stats": {},
    "error": None,
    "results": None
}
backtest_thread = None

def _run_backtest_thread(symbols_list, days, balance, offline):
    global backtest_state
    try:
        from datetime import datetime, timezone, timedelta
        end_time = datetime.now(timezone.utc)
        start_time = end_time - timedelta(days=days)
        start_time = start_time.replace(second=0, microsecond=0)
        end_time = end_time.replace(second=0, microsecond=0)
        
        def progress_callback(progress, funnel_stats, status):
            global backtest_state
            backtest_state["progress"] = progress
            backtest_state["funnel_stats"] = funnel_stats
            if status:
                backtest_state["status"] = "completed" if status == "completed" else "running"

        # Create runner
        runner = BacktestRunner(
            symbols=symbols_list,
            start_time=start_time,
            end_time=end_time,
            initial_balance=balance,
            offline=offline,
            progress_cb=progress_callback
        )
        
        backtest_state["status"] = "running"
        backtest_state["progress"] = 0.0
        backtest_state["funnel_stats"] = {}
        backtest_state["error"] = None
        backtest_state["results"] = None
        
        # Execute run
        runner.run()
        
        # Generate report & get results
        import uuid
        temp_report = f"backtest_report_{uuid.uuid4().hex[:8]}.md"
        results = runner.generate_report(temp_report)
        
        # Cleanup temp report file
        if os.path.exists(temp_report):
            try:
                os.remove(temp_report)
            except Exception:
                pass
                
        backtest_state["results"] = results
        backtest_state["status"] = "completed"
        backtest_state["progress"] = 100.0
    except Exception as e:
        logger.exception("Backtest background thread crash:")
        backtest_state["status"] = "failed"
        backtest_state["error"] = str(e)

@app.route("/api/backtest/run", methods=["POST"])
def api_backtest_run():
    global backtest_thread, backtest_state
    if backtest_state["status"] == "running":
        return jsonify({"ok": False, "error": "A backtest is already running."}), 400
        
    try:
        req = request.json or {}
        symbols_str = req.get("symbols", "BTCUSDT,ETHUSDT,SOLUSDT")
        days = int(req.get("days", 3))
        balance = float(req.get("balance", 2000.0))
        offline = bool(req.get("offline", True))
        
        symbols_list = [s.strip().upper() for s in symbols_str.split(",") if s.strip()]
        if not symbols_list:
            return jsonify({"ok": False, "error": "No symbols provided."}), 400
            
        # Reset state and start thread
        backtest_state = {
            "status": "running",
            "progress": 0.0,
            "funnel_stats": {},
            "error": None,
            "results": None
        }
        
        backtest_thread = threading.Thread(
            target=_run_backtest_thread,
            args=(symbols_list, days, balance, offline),
            daemon=True
        )
        backtest_thread.start()
        
        return jsonify({"ok": True, "message": "Backtest started successfully."})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500

@app.route("/api/backtest/status")
def api_backtest_status():
    global backtest_state
    return jsonify({"ok": True, "state": backtest_state})


# ── Telemetry & Export Endpoints ────────────────────────────────────
@app.route("/api/telemetry/export")
def api_telemetry_export():
    """Download historical trade data as CSV."""
    try:
        check_access()
        import csv
        import io
        
        conn = get_conn()
        cursor = conn.execute("SELECT * FROM trades ORDER BY id DESC")
        rows = cursor.fetchall()
        conn.close()
        
        dest = io.StringIO()
        writer = csv.writer(dest)
        if rows:
            writer.writerow(rows[0].keys())
            for row in rows:
                writer.writerow(list(row))
                
        response = Response(dest.getvalue(), mimetype="text/csv")
        response.headers["Content-Disposition"] = "attachment; filename=aurvex_trade_telemetry.csv"
        return response
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500

@app.route("/api/telemetry/report")
def api_telemetry_report():
    """Print-friendly premium HTML performance report card."""
    try:
        check_access()
        stats = dashboard_service.get_stats()
        closed_trades = get_closed_trades(limit=50, valid_only=False)
        
        # HTML Template string for the report card
        html = f"""<!DOCTYPE html>
<html>
<head>
    <meta charset="utf-8">
    <title>Aurvex AI Telemetry Performance Report</title>
    <style>
        body {{
            background-color: #0c0b11;
            color: #e2e8f0;
            font-family: 'Outfit', 'Inter', -apple-system, sans-serif;
            margin: 0;
            padding: 40px;
        }}
        .container {{
            max-width: 1000px;
            margin: 0 auto;
            border: 1px solid rgba(212, 168, 67, 0.3);
            border-radius: 12px;
            padding: 40px;
            background: linear-gradient(135deg, #120a23 0%, #0a1637 100%);
            box-shadow: 0 10px 30px rgba(0,0,0,0.5);
        }}
        h1 {{
            color: #d4a843;
            margin-top: 0;
            font-size: 32px;
            letter-spacing: 1px;
        }}
        h2 {{
            color: rgba(255,255,255,0.85);
            font-size: 20px;
            border-bottom: 1px solid rgba(255,255,255,0.1);
            padding-bottom: 10px;
            margin-top: 30px;
        }}
        .metrics-grid {{
            display: grid;
            grid-template-columns: repeat(3, 1fr);
            gap: 20px;
            margin-top: 20px;
        }}
        .metric-card {{
            background: rgba(255,255,255,0.03);
            border: 1px solid rgba(255,255,255,0.05);
            border-radius: 8px;
            padding: 20px;
            text-align: center;
        }}
        .metric-title {{
            font-size: 12px;
            color: rgba(212, 168, 67, 0.7);
            text-transform: uppercase;
            letter-spacing: 1px;
        }}
        .metric-value {{
            font-size: 28px;
            font-weight: bold;
            margin-top: 8px;
        }}
        .text-green {{ color: #00e676; }}
        .text-red {{ color: #ef4444; }}
        table {{
            width: 100%;
            border-collapse: collapse;
            margin-top: 20px;
        }}
        th, td {{
            text-align: left;
            padding: 12px 15px;
            border-bottom: 1px solid rgba(255,255,255,0.05);
        }}
        th {{
            background-color: rgba(255,255,255,0.05);
            color: rgba(212, 168, 67, 0.9);
            font-size: 13px;
            text-transform: uppercase;
        }}
        tr:hover {{
            background-color: rgba(255,255,255,0.02);
        }}
        .print-btn {{
            background-color: #d4a843;
            color: #0c0b11;
            border: none;
            padding: 10px 20px;
            font-weight: bold;
            border-radius: 6px;
            cursor: pointer;
            float: right;
        }}
        @media print {{
            body {{ background: white; color: black; padding: 0; }}
            .container {{ border: none; box-shadow: none; background: none; padding: 0; }}
            .print-btn {{ display: none; }}
            th {{ background: #eee; color: black; }}
            td {{ border-bottom: 1px solid #ddd; }}
            .metric-card {{ border: 1px solid #ccc; }}
        }}
    </style>
</head>
<body>
    <div class="container">
        <button class="print-btn" onclick="window.print()">Print Report</button>
        <h1>AURVEX AI</h1>
        <p>Intelligent Trading System · Weekly & Historical Digest Report</p>
        
        <h2>Performance Summary</h2>
        <div class="metrics-grid">
            <div class="metric-card">
                <div class="metric-title">Total Trades</div>
                <div class="metric-value">{stats.get('total_trades', 0)}</div>
            </div>
            <div class="metric-card">
                <div class="metric-title">Win Rate</div>
                <div class="metric-value text-green">{stats.get('win_rate', 0.0):.1f}%</div>
            </div>
            <div class="metric-card">
                <div class="metric-title">Net Cumulative PnL</div>
                <div class="metric-value { 'text-green' if stats.get('total_pnl', 0.0) >= 0 else 'text-red' }">
                    { '+' if stats.get('total_pnl', 0.0) >= 0 else '' }{stats.get('total_pnl', 0.0):.2f}$
                </div>
            </div>
        </div>

        <h2>Recent Closed Positions</h2>
        <table>
            <thead>
                <tr>
                    <th>Symbol</th>
                    <th>Direction</th>
                    <th>Entry Price</th>
                    <th>Exit Price</th>
                    <th>Net PnL ($)</th>
                    <th>Reason</th>
                </tr>
            </thead>
            <tbody>
        """
        for t in closed_trades:
            pnl = float(t.get("net_pnl") or 0.0)
            pnl_class = "text-green" if pnl >= 0 else "text-red"
            pnl_sign = "+" if pnl >= 0 else ""
            
            html += f"""
                <tr>
                    <td><b>{t.get('symbol')}</b></td>
                    <td>{t.get('direction')}</td>
                    <td>{t.get('entry_price', 0.0):.4f}</td>
                    <td>{t.get('close_price', 0.0):.4f}</td>
                    <td class="{pnl_class}">{pnl_sign}{pnl:.2f}$</td>
                    <td>{t.get('close_reason')}</td>
                </tr>
            """
            
        html += """
            </tbody>
        </table>
    </div>
</body>
</html>
        """
        return Response(html, mimetype="text/html")
    except Exception as e:
        return Response(f"Error rendering report: {str(e)}", status=500)


@app.route("/api/friday/chat", methods=["POST"])
def api_friday_chat():
    """Endpoint to chat with Friday CEO from Web dashboard."""
    try:
        body = request.json or {}
        user_message = body.get("message", "").strip()
        if not user_message:
            return jsonify({"ok": False, "error": "Mesaj bos olamaz."}), 400

        from core.friday_ceo import FridayCeo
        import base64
        import re
        
        ceo = FridayCeo()
        reply = ceo.evaluate_and_decide(user_message, send_telegram=False)
        
        # Strip json block to get clean text for voice
        clean_reply = re.sub(r"```json\s*\{.*?\}\s*```", "", reply, flags=re.DOTALL).strip()
        clean_reply = re.sub(r"\{[\s\S]*?\}", "", clean_reply).strip()
        
        voice_base64 = None
        try:
            # Only voice the main textual reply part, split before configurations list if any
            voice_text = clean_reply
            if "⚙️" in voice_text:
                voice_text = voice_text.split("⚙️")[0].strip()
            voice_bytes = ceo.generate_voice_from_text(voice_text)
            if voice_bytes:
                voice_base64 = base64.b64encode(voice_bytes).decode("utf-8")
        except Exception as ve:
            logger.error(f"Failed to generate UI voice: {ve}")

        return jsonify({
            "ok": True,
            "reply": reply,
            "voice": voice_base64
        })
    except Exception as exc:
        logger.error(f"Friday UI Chat error: {exc}")
        return jsonify({"ok": False, "error": str(exc)}), 500


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
