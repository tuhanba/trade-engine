import os, json, sqlite3
from datetime import datetime, timezone
from flask import Flask, render_template, jsonify, request
try:
    from n8n_bridge import n8n_bp
    N8N_AVAILABLE = True
except ImportError:
    N8N_AVAILABLE = False
from flask_socketio import SocketIO
from dotenv import load_dotenv
from config import DB_PATH, AX_MODE, EXECUTION_MODE, PAPER_MODE
from database import (
    init_db, get_trades, get_stats, get_current_params,
    get_open_trades, get_paper_balance, get_conn,
    get_pipeline_summary, get_pipeline_stats,
    get_recent_candidates, get_veto_stats,
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


def _fmt_duration(open_time_str, close_time_str=None):
    try:
        ot = datetime.fromisoformat((open_time_str or "").replace("Z", "+00:00"))
        if close_time_str:
            ct = datetime.fromisoformat((close_time_str or "").replace("Z", "+00:00"))
        else:
            ct = datetime.now(timezone.utc)
        mins = (ct - ot).total_seconds() / 60
        h, m = divmod(int(mins), 60)
        return (f"{h}s {m}dk" if h else f"{m}dk"), round(mins, 1)
    except:
        return "—", 0


@app.route("/")
def index():
    return render_template("index.html")


# ── /api/stats ────────────────────────────────────────────────────────────────
@app.route("/api/stats")
def api_stats():
    try:
        stats = get_stats()

        # Seans istatistikleri — coin_market_memory tablosundan
        try:
            with get_conn() as conn:
                sess_stats = {}
                for sess in ["ASIA", "LONDON", "NEW_YORK"]:
                    row = conn.execute(
                        "SELECT COUNT(*), "
                        "SUM(CASE WHEN result='WIN' THEN 1 ELSE 0 END), "
                        "SUM(CASE WHEN result='WIN' THEN r_multiple ELSE 0 END), "
                        "SUM(CASE WHEN result='LOSS' THEN r_multiple ELSE 0 END) "
                        "FROM coin_market_memory WHERE session=?", (sess,)
                    ).fetchone()
                    total_s = row[0] or 0
                    wins_s  = row[1] or 0
                    sess_stats[sess] = {
                        "total":    total_s,
                        "wins":     wins_s,
                        "losses":   total_s - wins_s,
                        "win_rate": round(wins_s / max(total_s, 1) * 100, 1),
                    }
                stats["session_stats"] = sess_stats
        except Exception:
            stats["session_stats"] = {}

        stats["ml_status"] = {"trained": False, "n_samples": 0}
        return jsonify({"ok": True, "data": stats})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500


# ── /api/pnl_chart ────────────────────────────────────────────────────────────
@app.route("/api/pnl_chart")
def api_pnl_chart():
    try:
        trades = get_trades(limit=200, status="closed")
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
        limit = int(request.args.get("limit", 10))
        # Tüm kapanmış trade sayısını al
        all_trades = get_trades(limit=10000, status="closed")
        total_count = len(all_trades)
        total_pages = max(1, (total_count + limit - 1) // limit)
        page = max(1, min(page, total_pages))
        offset = (page - 1) * limit
        trades = all_trades[offset:offset + limit]
        result = []
        for t in trades:
            dur_str, dur_min = _fmt_duration(t.get("open_time"), t.get("close_time"))
            result.append({**t, "duration_str": dur_str, "duration_min": dur_min})
        return jsonify({
            "ok": True, "data": result,
            "page": page, "total_pages": total_pages,
            "total_count": total_count, "limit": limit
        })
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500


# ── /api/live ─────────────────────────────────────────────────────────────────
@app.route("/api/live")
def api_live():
    try:
        open_trades = get_open_trades()
        live = []
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
                if direction == "LONG":
                    raw_pnl = (mark - entry) * qty
                else:
                    raw_pnl = (entry - mark) * qty
                sl_dist    = abs(entry - sl)
                tp_dist    = abs(tp - entry)
                current_rr = round(raw_pnl / (sl_dist * qty + 1e-10), 3) if sl_dist else 0
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
            except:
                live.append({
                    **t,
                    "current_price": 0, "unrealized_pnl": 0,
                    "unrealized_pct": 0, "current_rr": 0,
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
    """En iyi ve en kötü performanslı coinler — coin_profile tablosundan"""
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
        result = [dict(r) for r in rows]
        return jsonify({"ok": True, "data": result})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500


# ── /api/ml_status ────────────────────────────────────────────────────────────────────────────────
@app.route("/api/ml_status")
def api_ml_status():
    try:
        from ml_signal_scorer import get_scorer
        status = get_scorer().get_status()
        return jsonify({"ok": True, "data": status})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500


# ── /api/logs ─────────────────────────────────────────────────────────────────────────────────────────
@app.route("/api/logs")
def api_logs():
    """
    Son N satır log döndürür.
    Log dosyası yolları (sırayla denenecek):
      1. /root/trade_engine/bot.log
      2. /root/trade_engine/scalp_bot.log
      3. /tmp/scalp_bot.log
    """
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
                    raw_lines = f.readlines()
                raw_lines = raw_lines[-n:]

                # Log satırlarını parse et
                for raw in raw_lines:
                    raw = raw.rstrip("\n")
                    # Seviye tespiti
                    level = "INFO"
                    if re.search(r"\bERROR\b|\bCRITICAL\b|\bException\b|Traceback", raw, re.I):
                        level = "ERROR"
                    elif re.search(r"\bWARNING\b|\bWARN\b", raw, re.I):
                        level = "WARNING"
                    elif re.search(r"\bDEBUG\b", raw, re.I):
                        level = "DEBUG"
                    elif re.search(r"WIN|K\u00c2R|PROFIT|LONG|SHORT|ENTRY|OPEN", raw):
                        level = "TRADE"
                    elif re.search(r"LOSS|STOP|CLOSE|KAPAND", raw):
                        level = "CLOSE"

                    lines_out.append({"text": raw, "level": level})

                return jsonify({
                    "ok":   True,
                    "data": lines_out,
                    "path": path,
                    "total": len(lines_out),
                })
            except Exception as ex:
                continue

    # Log dosyası bulunamadı — DB'den son trade'leri log gibi döndür
    try:
        with get_conn() as conn:
            rows = conn.execute(
                "SELECT symbol, direction, close_reason, net_pnl, close_time "
                "FROM trades WHERE close_time IS NOT NULL ORDER BY id DESC LIMIT ?", (n,)
            ).fetchall()
        for r in reversed(rows):
            r = dict(r)
            pnl = r.get("net_pnl", 0) or 0
            res = "WIN" if pnl > 0 else "LOSS"
            level = "TRADE" if pnl > 0 else "CLOSE"
            lines_out.append({
                "text":  f"[{r.get('close_time','')}] {r.get('symbol','')} {r.get('direction','')} → {r.get('close_reason','?').upper()}  PNL: {pnl:+.4f}$",
                "level": level,
            })
        return jsonify({"ok": True, "data": lines_out, "path": "db_fallback", "total": len(lines_out)})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e), "data": []}), 500



# ── /api/coin_library ─────────────────────────────────────────────────────────────────────────
@app.route("/api/coin_library")
def api_coin_library():
    """Coin Library: tüm coin profilleri ve istatistikleri"""
    try:
        with get_conn() as conn:
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
        return jsonify({"ok": True, "data": [], "note": str(e)})


@app.route("/api/coin_library/<symbol>", methods=["POST"])
def api_coin_library_update(symbol):
    """Coin profilini güncelle (enable/disable, profil değiştir)"""
    try:
        data = request.get_json() or {}
        allowed = ["enabled", "volatility_profile", "sl_atr_mult", "tp_atr_mult",
                   "risk_pct", "max_leverage"]
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
    """30 günlük takvim verisi."""
    try:
        days = int(request.args.get("days", 30))
        data = dash_svc.get_calendar_data(days)
        return jsonify({"ok": True, "data": data})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500


# ── /api/weekly ───────────────────────────────────────────────────────────────
@app.route("/api/weekly")
def api_weekly():
    """Son 8 haftalık özet."""
    try:
        weeks = int(request.args.get("weeks", 8))
        data = dash_svc.get_weekly_data(weeks)
        return jsonify({"ok": True, "data": data})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500


# ── /api/ax_status ────────────────────────────────────────────────────────────
@app.route("/api/ax_status")
def api_ax_status():
    """AX sistem durumu: CB, açık trade, bakiye, bugünkü PnL."""
    try:
        data = dash_svc.get_ax_status()
        return jsonify({"ok": True, "data": data})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500


# ── /api/coin_profiles ────────────────────────────────────────────────────────
@app.route("/api/coin_profiles")
def api_coin_profiles():
    """Tüm coin öğrenme profilleri (coin_profile tablosu)."""
    try:
        with get_conn() as conn:
            rows = conn.execute(
                """
                SELECT symbol, trade_count, win_count, loss_count,
                       ROUND(win_rate*100,1) as win_rate_pct,
                       avg_r, profit_factor, danger_score, fakeout_rate,
                       volatility_profile, preferred_direction, best_session,
                       updated_at
                FROM coin_profile
                ORDER BY trade_count DESC, danger_score DESC
                """
            ).fetchall()
        return jsonify({"ok": True, "data": [dict(r) for r in rows]})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500


# ── /api/signal_stats ─────────────────────────────────────────────────────────
@app.route("/api/signal_stats")
def api_signal_stats():
    """Bugünkü sinyal istatistikleri: ALLOW/VETO/WATCH dağılımı."""
    try:
        days = int(request.args.get("days", 1))
        with get_conn() as conn:
            rows = conn.execute(
                """
                SELECT decision, COUNT(*) as cnt
                FROM signal_candidates
                WHERE created_at >= datetime('now', ?)
                GROUP BY decision
                """,
                (f"-{days} days",),
            ).fetchall()
        stats = {"ALLOW": 0, "VETO": 0, "WATCH": 0, "PENDING": 0, "total": 0}
        for row in rows:
            dec = row["decision"] or "PENDING"
            if dec in stats:
                stats[dec] = row["cnt"]
            stats["total"] += row["cnt"]
        return jsonify({"ok": True, "data": stats})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500


# ── /api/status ───────────────────────────────────────────────────────────────
@app.route("/api/status")
def api_status():
    """Sistem anlık durumu — dashboard status panel için."""
    try:
        data = dash_svc.get_ax_status()
        data.update({
            "ax_mode":        AX_MODE,
            "execution_mode": EXECUTION_MODE,
            "paper_mode":     PAPER_MODE,
        })
        return jsonify({"ok": True, "data": data})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500


# ── /api/summary ──────────────────────────────────────────────────────────────
@app.route("/api/summary")
def api_summary():
    """Özet istatistikler + pipeline + sinyal dağılımı."""
    try:
        stats    = get_stats(hours=24)
        pipeline = get_pipeline_summary(hours=24)
        with get_conn() as conn:
            sig_rows = conn.execute(
                """SELECT decision, COUNT(*) as cnt FROM signal_candidates
                   WHERE created_at >= datetime('now','-1 day') GROUP BY decision"""
            ).fetchall()
        signals = {"ALLOW": 0, "VETO": 0, "WATCH": 0, "total": 0}
        for r in sig_rows:
            dec = r["decision"] or "PENDING"
            if dec in signals:
                signals[dec] = r["cnt"]
            signals["total"] += r["cnt"]
        return jsonify({"ok": True, "data": {
            "stats": stats, "pipeline": pipeline, "signals": signals,
        }})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500


# ── /api/trades/open ──────────────────────────────────────────────────────────
@app.route("/api/trades/open")
def api_trades_open():
    return api_live()


# ── /api/trades/recent ───────────────────────────────────────────────────────
@app.route("/api/trades/recent")
def api_trades_recent():
    return api_trades()


# ── /api/signals/recent ──────────────────────────────────────────────────────
@app.route("/api/signals/recent")
def api_signals_recent():
    """Son N saatin signal candidate'leri."""
    try:
        hours = int(request.args.get("hours", 24))
        limit = int(request.args.get("limit", 50))
        data  = get_recent_candidates(limit=limit, hours=hours)
        return jsonify({"ok": True, "data": data, "total": len(data)})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500


# ── /api/pipeline-stats ───────────────────────────────────────────────────────
@app.route("/api/pipeline-stats")
def api_pipeline_stats():
    """Pipeline istatistikleri — kaç coin tarandı, kaç candidate üretildi, vb."""
    try:
        hours   = int(request.args.get("hours", 24))
        summary = get_pipeline_summary(hours=hours)
        recent  = get_pipeline_stats(limit=20)
        return jsonify({"ok": True, "data": {
            "summary": summary,
            "recent":  recent,
        }})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500


# ── /api/ai/params ────────────────────────────────────────────────────────────
@app.route("/api/ai/params")
def api_ai_params():
    return api_params()


# ── /api/performance ─────────────────────────────────────────────────────────
@app.route("/api/performance")
def api_performance():
    try:
        hours = int(request.args.get("hours", 168))
        stats = get_stats(hours=hours)
        bal   = get_paper_balance()
        stats["balance"] = round(bal, 4)
        return jsonify({"ok": True, "data": stats})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500


# ── /api/session-performance ──────────────────────────────────────────────────
@app.route("/api/session-performance")
def api_session_performance():
    try:
        with get_conn() as conn:
            rows = conn.execute(
                """SELECT session,
                          COUNT(*) as total,
                          SUM(CASE WHEN result='WIN' THEN 1 ELSE 0 END) as wins,
                          SUM(r_multiple) as total_r
                   FROM coin_market_memory
                   GROUP BY session"""
            ).fetchall()
        data = []
        for r in rows:
            total = r["total"] or 0
            wins  = r["wins"] or 0
            data.append({
                "session":   r["session"] or "UNKNOWN",
                "total":     total,
                "wins":      wins,
                "losses":    total - wins,
                "win_rate":  round(wins / max(total, 1) * 100, 1),
                "total_r":   round(r["total_r"] or 0, 2),
            })
        return jsonify({"ok": True, "data": data})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500


# ── /api/calendar-pnl ────────────────────────────────────────────────────────
@app.route("/api/calendar-pnl")
def api_calendar_pnl():
    return api_daily_pnl()


# ── /api/weekly-pnl ──────────────────────────────────────────────────────────
@app.route("/api/weekly-pnl")
def api_weekly_pnl():
    return api_weekly()


# ── /api/equity-curve ────────────────────────────────────────────────────────
@app.route("/api/equity-curve")
def api_equity_curve():
    return api_pnl_chart()


# ── /api/winrate-rr ──────────────────────────────────────────────────────────
@app.route("/api/winrate-rr")
def api_winrate_rr():
    """Win rate vs RR grafiği için veri. Expectancy = WR*avgWin - (1-WR)*avgLoss."""
    try:
        stats = get_stats(hours=720)
        win_rate = stats.get("win_rate", 0)
        avg_win  = stats.get("avg_r", 0)
        avg_loss = 1.0
        expectancy = (win_rate * avg_win) - ((1 - win_rate) * avg_loss)
        return jsonify({"ok": True, "data": {
            "win_rate":   round(win_rate * 100, 1),
            "avg_rr":     round(stats.get("avg_r", 0), 2),
            "expectancy": round(expectancy, 3),
            "trade_count":stats.get("total", 0),
            "break_even_line": [
                {"wr": w, "rr": round(w / max(100 - w, 1), 2)}
                for w in range(30, 75, 5)
            ],
            "target_zone": {"wr_min": 45, "wr_max": 55, "rr_min": 1.5, "rr_max": 2.5},
            "has_data": stats.get("total", 0) > 0,
        }})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500


# ── /api/coin-performance ────────────────────────────────────────────────────
@app.route("/api/coin-performance")
def api_coin_performance():
    return api_coin_stats()


# ── /api/veto-stats ──────────────────────────────────────────────────────────
@app.route("/api/veto-stats")
def api_veto_stats():
    """Veto sebeplerinin dağılımı."""
    try:
        hours = int(request.args.get("hours", 24))
        data  = get_veto_stats(hours=hours)
        return jsonify({"ok": True, "data": data})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500


# ── /api/health ───────────────────────────────────────────────────────────────
@app.route("/api/health")
def api_health():
    """Sistem sağlık kontrolü."""
    import time
    health = {
        "status":      "ok",
        "timestamp":   datetime.now(timezone.utc).isoformat(),
        "checks":      {},
    }
    # DB kontrolü
    try:
        with get_conn() as conn:
            conn.execute("SELECT 1").fetchone()
        health["checks"]["db"] = "ok"
    except Exception as e:
        health["checks"]["db"] = f"error: {e}"
        health["status"] = "degraded"

    # Son tarama zamanı
    try:
        pipeline = get_pipeline_summary(hours=1)
        last_scan = pipeline.get("last_scan_time")
        health["checks"]["last_scan"] = last_scan or "no_scan_yet"
    except Exception:
        health["checks"]["last_scan"] = "unknown"

    # Bakiye
    try:
        bal = get_paper_balance()
        health["checks"]["balance"] = round(bal, 2)
    except Exception:
        health["checks"]["balance"] = "error"

    # Açık trade sayısı
    try:
        ot = get_open_trades()
        health["checks"]["open_trades"] = len(ot)
    except Exception:
        health["checks"]["open_trades"] = "error"

    return jsonify({"ok": True, "data": health})


if __name__ == "__main__":
    socketio.run(app, host="0.0.0.0", port=5000, debug=False, use_reloader=False, allow_unsafe_werkzeug=True)
