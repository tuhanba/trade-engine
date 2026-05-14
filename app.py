<<<<<<< HEAD
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
=======
import os, json, re
>>>>>>> 0797c70b8640d2006e47a50d5580ffae4606199b
from datetime import datetime, timezone

from flask import Flask, Response, jsonify, render_template, stream_with_context

<<<<<<< HEAD
import config
import database
import dashboard_service

logger = logging.getLogger("ax.app")

app = Flask(__name__)
app.secret_key = getattr(config, "SECRET_KEY", "ax_secret_2026")
=======
from flask_socketio import SocketIO
from dotenv import load_dotenv
from websocket_events import initialize_websocket_events
from database import (
    init_db, get_stats, get_closed_trades, get_open_trades,
    get_paper_balance, get_conn, get_system_state,
)

# Geriye dönük uyumluluk alias'ları
def get_trades(limit=200, status="closed"):
    return get_closed_trades(limit=limit, valid_only=(status == "closed"))

def get_current_params():
    return None  # Artık system_state üzerinden okunuyor
import dashboard_service as dash_svc

load_dotenv()

_static_folder = os.path.join(os.path.dirname(os.path.abspath(__file__)), "static")
os.makedirs(_static_folder, exist_ok=True)

app = Flask(__name__, template_folder="templates", static_folder="static")
app.config["SECRET_KEY"] = os.getenv("SECRET_KEY", "scalp2026")
socketio = SocketIO(app, cors_allowed_origins="*", async_mode="threading")
event_manager = initialize_websocket_events(socketio)
if N8N_AVAILABLE:
    app.register_blueprint(n8n_bp)

try:
    from binance.client import Client as _BinanceClient
    client = _BinanceClient(
        os.getenv("BINANCE_API_KEY", ""),
        os.getenv("BINANCE_API_SECRET", ""),
    )
except Exception as _e:
    import logging as _logging
    _logging.getLogger(__name__).warning(f"Binance client başlatılamadı: {_e}")
    client = None

init_db()
dash_svc.start()
>>>>>>> 0797c70b8640d2006e47a50d5580ffae4606199b


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

<<<<<<< HEAD
=======

# ── /api/trades ───────────────────────────────────────────────────────────────
@app.route("/api/trades")
def api_trades():
    try:
        page  = int(request.args.get("page", 1))
        # Frontend hem "limit" hem "per_page" gönderebilir
        limit = int(request.args.get("limit", request.args.get("per_page", 10)))
        all_trades  = get_trades(limit=10000, status="closed")
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


# ── /api/live ─────────────────────────────────────────────────────────────────
@app.route("/api/live")
def api_live():
    try:
        open_trades      = get_open_trades()
        live             = []
        total_unrealized = 0.0

        for t in open_trades:
            symbol    = t["symbol"]
            entry     = t["entry"] or 0
            sl        = t["sl"] or 0
            tp        = t.get("tp1") or t.get("tp") or 0
            qty       = t["qty"] or 0
            direction = t["direction"]
            hold_str, hold_min = _fmt_duration(t.get("open_time"))

            try:
                if client is None:
                    raise RuntimeError("Binance client kullanılamıyor")
                ticker = client.futures_symbol_ticker(symbol=symbol)
                mark   = float(ticker["price"])
                raw_pnl = (mark - entry) * qty if direction == "LONG" else (entry - mark) * qty
                sl_dist     = abs(entry - sl)
                tp_dist     = abs(tp - entry)
                current_rr  = round(raw_pnl / (sl_dist * qty + 1e-10), 3) if sl_dist else 0
                sl_dist_pct = round(abs(mark - sl) / (mark + 1e-10) * 100, 2)
                progress    = round(min(abs(mark - entry) / (tp_dist + 1e-10) * 100, 100), 1) if tp_dist else 0
                total_unrealized += raw_pnl
                live.append({
                    **t,
                    "current_price":   round(mark, 6),
                    "unrealized_pnl":  round(raw_pnl, 4),
                    "unrealized_pct":  round(raw_pnl / (entry * qty + 1e-10) * 100, 2),
                    "current_rr":      current_rr,
                    "sl_distance_pct": sl_dist_pct,
                    "tp_progress":     progress,
                    "hold_str":        hold_str,
                    "hold_min":        hold_min,
                })
            except Exception:
                live.append({
                    **t,
                    "current_price": 0, "unrealized_pnl": 0,
                    "unrealized_pct": 0, "current_rr": 0,
                    "sl_distance_pct": 0, "tp_progress": 0,
                    "hold_str": hold_str, "hold_min": hold_min,
                })

        closed         = get_trades(limit=500, status="closed")
        total_realized = sum(t["net_pnl"] or 0 for t in closed)

        return jsonify({"ok": True, "data": {
            "live":             live,
            "total_unrealized": round(total_unrealized, 4),
            "total_realized":   round(total_realized, 4),
            "total_pnl":        round(total_unrealized + total_realized, 4),
            "open_count":       len(live),
        }})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500


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
        p = get_current_params()
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
        data = dash_svc.get_calendar_data(days)
        return jsonify({"ok": True, "data": data})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500


# ── /api/weekly ───────────────────────────────────────────────────────────────
@app.route("/api/weekly")
def api_weekly():
    try:
        weeks = int(request.args.get("weeks", 8))
        data  = dash_svc.get_weekly_data(weeks)
        return jsonify({"ok": True, "data": data})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500


# ── /api/ax_status ────────────────────────────────────────────────────────────
@app.route("/api/ax_status")
def api_ax_status():
    try:
        data = dash_svc.get_ax_status()
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


# ── /api/scalp_signals ────────────────────────────────────────────────────────
@app.route("/api/signals")
@app.route("/api/scalp_signals")
def api_scalp_signals():
    try:
        from database import get_active_scalp_signals
        signals = get_active_scalp_signals(limit=100)
        clean = [s for s in signals if s.get("direction") and s.get("entry_zone", 0) > 0]
        return jsonify({"ok": True, "data": clean, "total": len(clean)})
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


# ── /api/health ───────────────────────────────────────────────────────────────
>>>>>>> 0797c70b8640d2006e47a50d5580ffae4606199b
@app.route("/api/health")
def api_health():
    """Sistem sağlık durumu."""
    try:
        return _ok(dashboard_service.get_health())
    except Exception as exc:
        return _error(str(exc))


<<<<<<< HEAD
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
=======
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
            # Toplam sayıyı al
            total_count = conn.execute("SELECT COUNT(*) FROM signal_candidates").fetchone()[0]
            total_pages = max(1, (total_count + limit - 1) // limit)
            page = max(1, min(page, total_pages))
            offset = (page - 1) * limit
            
            # Verileri çek (paper_results ile join yaparak MFE/MAE bilgilerini de al)
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


# ── ENTRY POINT ───────────────────────────────────────────────────────────────
if __name__ == "__main__":
    socketio.run(
        app, host="0.0.0.0", port=5000,
        debug=False, use_reloader=False,
        allow_unsafe_werkzeug=True,
>>>>>>> 0797c70b8640d2006e47a50d5580ffae4606199b
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
