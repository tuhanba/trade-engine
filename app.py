import os, json, sqlite3, re
from datetime import datetime, timezone
from flask import Flask, render_template, jsonify, request

try:
    from n8n_bridge import n8n_bp
    N8N_AVAILABLE = True
except ImportError:
    N8N_AVAILABLE = False

from flask_socketio import SocketIO
from dotenv import load_dotenv
from database import (
    init_db, get_trades, get_stats, get_current_params,
    get_open_trades, get_paper_balance, get_conn,
)
from binance.client import Client
import dashboard_service as dash_svc

load_dotenv()
app = Flask(__name__, template_folder="templates", static_folder="static")
app.config["SECRET_KEY"] = os.getenv("SECRET_KEY", "scalp2026")
socketio = SocketIO(app, cors_allowed_origins="*", async_mode="threading")
if N8N_AVAILABLE:
    app.register_blueprint(n8n_bp)

client = Client(os.getenv("BINANCE_API_KEY", ""), os.getenv("BINANCE_API_SECRET", ""))

init_db()
dash_svc.start()

DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "trading.db")


def _fmt_duration(open_time_str, close_time_str=None):
    try:
        ot = datetime.fromisoformat((open_time_str or "").replace("Z", "+00:00"))
        ct = (
            datetime.fromisoformat((close_time_str or "").replace("Z", "+00:00"))
            if close_time_str
            else datetime.now(timezone.utc)
        )
        mins = (ct - ot).total_seconds() / 60
        h, m = divmod(int(mins), 60)
        return (f"{h}s {m}dk" if h else f"{m}dk"), round(mins, 1)
    except Exception:
        return "—", 0


# ── / ─────────────────────────────────────────────────────────────────────────
@app.route("/")
def index():
    return render_template("index.html")


# ── /api/stats ────────────────────────────────────────────────────────────────
@app.route("/api/stats")
def api_stats():
    try:
        stats = get_stats()
        try:
            with get_conn() as conn:
                sess_stats = {}
                # Session istatistiklerini trades tablosundan hesapla
                for sess in ["ASIA", "LONDON", "NEW_YORK"]:
                    try:
                        srow = conn.execute(
                            """SELECT COUNT(*),
                               SUM(CASE WHEN net_pnl>0 THEN 1 ELSE 0 END),
                               SUM(net_pnl)
                               FROM trades
                               WHERE session=? AND close_time IS NOT NULL
                               AND status IN ('closed','closed_win','closed_loss','sl','tp1_hit','runner','trail','timeout')""",
                            (sess,)
                        ).fetchone()
                        total_s = srow[0] or 0
                        wins_s  = srow[1] or 0
                        pnl_s   = round(srow[2] or 0, 4)
                    except Exception:
                        # coin_market_memory fallback
                        srow = conn.execute(
                            "SELECT COUNT(*), SUM(CASE WHEN result='WIN' THEN 1 ELSE 0 END), 0 "
                            "FROM coin_market_memory WHERE session=?", (sess,)
                        ).fetchone()
                        total_s = srow[0] or 0
                        wins_s  = srow[1] or 0
                        pnl_s   = 0.0
                    sess_stats[sess] = {
                        "total":    total_s,
                        "wins":     wins_s,
                        "losses":   total_s - wins_s,
                        "win_rate": round(wins_s / max(total_s, 1) * 100, 1),
                        "pnl":      pnl_s,
                    }
                # NEWYORK -> NEW_YORK alias
                if "NEW_YORK" in sess_stats and "NEWYORK" not in sess_stats:
                    sess_stats["NEWYORK"] = sess_stats["NEW_YORK"]
                stats["session_stats"] = sess_stats
        except Exception:
            stats["session_stats"] = {
                "ASIA":     {"total": 0, "wins": 0, "losses": 0, "win_rate": 0.0, "pnl": 0.0},
                "LONDON":   {"total": 0, "wins": 0, "losses": 0, "win_rate": 0.0, "pnl": 0.0},
                "NEW_YORK": {"total": 0, "wins": 0, "losses": 0, "win_rate": 0.0, "pnl": 0.0},
                "NEWYORK":  {"total": 0, "wins": 0, "losses": 0, "win_rate": 0.0, "pnl": 0.0},
            }
        stats["ml_status"] = {"trained": False, "n_samples": 0}
        return jsonify({"ok": True, "data": stats})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500


# ── /api/pnl_chart ────────────────────────────────────────────────────────────
@app.route("/api/pnl_chart")
def api_pnl_chart():
    try:
        trades = get_trades(limit=200)  # tüm trade'ler — açık olanları filtrele
        trades = [t for t in trades if t.get("close_time")]
        cumulative = 0
        points = []
        for t in reversed(trades):
            pnl = t["net_pnl"] or 0
            cumulative += pnl
            points.append({
                "symbol":     t["symbol"],
                "direction":  t["direction"],
                "pnl":        round(pnl, 4),
                "cumulative": round(cumulative, 4),
                "r_multiple": t.get("r_multiple", 0),
                "open_time":  t.get("open_time", ""),
                "close_time": t.get("close_time", ""),
                "status":     t.get("status", ""),
            })
        return jsonify({"ok": True, "data": points})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500


# ── /api/trades ───────────────────────────────────────────────────────────────
@app.route("/api/trades")
def api_trades():
    try:
        page  = int(request.args.get("page", 1))
        # Frontend hem "limit" hem "per_page" gönderebilir
        limit = int(request.args.get("limit", request.args.get("per_page", 10)))
        all_trades  = get_trades(limit=10000)
        all_trades  = [t for t in all_trades if t.get("close_time")]  # sadece kapananlar
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

        closed         = get_trades(limit=500)
        total_realized = sum(t["net_pnl"] or 0 for t in closed if t.get("close_time"))

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
                SELECT symbol, trade_count, win_count, loss_count,
                       ROUND(win_rate*100,1) as win_rate_pct,
                       avg_r, profit_factor, danger_score
                FROM coin_profile
                WHERE trade_count >= 3
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
    _app_dir = os.path.dirname(os.path.abspath(__file__))
    LOG_PATHS = [
        os.path.join(_app_dir, "logs", "ax_bot.log"),
        os.path.join(_app_dir, "logs", "bot.log"),
        os.path.join(_app_dir, "ax_bot.log"),
        os.path.join(_app_dir, "scalp_bot.log"),
        "/root/trade_engine/logs/ax_bot.log",
        "/root/trade_engine/logs/bot.log",
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
        conn = sqlite3.connect(DB_PATH)
        conn.row_factory = sqlite3.Row
        c = conn.cursor()
        c.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='coin_params'")
        if not c.fetchone():
            conn.close()
            return jsonify({"ok": True, "data": [], "note": "coin_params tablosu henüz oluşturulmadı"})
        with get_conn() as conn2:
            rows2 = conn2.execute("""
                SELECT symbol,
                       COALESCE(volatility_profile, 'normal') as profile,
                       sl_atr_mult, tp_atr_mult, risk_pct, max_leverage,
                       enabled, updated_at
                FROM coin_params
                ORDER BY symbol ASC
            """).fetchall()
        conn.close()
        return jsonify({"ok": True, "data": [dict(r) for r in rows2], "total": len(rows2)})
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
                SELECT symbol, trade_count, win_count, loss_count,
                       ROUND(win_rate*100,1) as win_rate_pct,
                       avg_r, profit_factor, danger_score, fakeout_rate,
                       volatility_profile, preferred_direction, best_session,
                       updated_at
                FROM coin_profile
                ORDER BY trade_count DESC, danger_score DESC
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
            # 'decision' sütununu dene, yoksa 'ax_decision' sütununu dene
            try:
                rows = conn.execute(
                    "SELECT decision, COUNT(*) as cnt "
                    "FROM signal_candidates "
                    "WHERE created_at >= datetime('now', ?) "
                    "GROUP BY decision",
                    (f"-{days} days",),
                ).fetchall()
            except Exception:
                rows = conn.execute(
                    "SELECT ax_decision, COUNT(*) as cnt "
                    "FROM signal_candidates "
                    "WHERE created_at >= datetime('now', ?) "
                    "GROUP BY ax_decision",
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
@app.route("/api/health")
def api_health():
    """Watchdog ve monitoring için health check endpoint."""
    try:
        balance = get_paper_balance()
        open_count = len(get_open_trades())
        return jsonify({
            "ok":        True,
            "status":    "healthy",
            "balance":   round(balance, 2),
            "open":      open_count,
            "timestamp": datetime.now(timezone.utc).isoformat(),
        })
    except Exception as e:
        return jsonify({"ok": False, "status": "unhealthy", "error": str(e)}), 500


# ── ENTRY POINT ───────────────────────────────────────────────────────────────
if __name__ == "__main__":
    socketio.run(
        app, host="0.0.0.0", port=5000,
        debug=False, use_reloader=False,
        allow_unsafe_werkzeug=True,
    )
