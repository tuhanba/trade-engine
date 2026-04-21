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
from database import init_db, get_trades, get_stats, get_current_params
from binance.client import Client

load_dotenv()
app = Flask(__name__, template_folder="templates", static_folder="static")
app.config["SECRET_KEY"] = os.getenv("SECRET_KEY", "scalp2026")
socketio = SocketIO(app, cors_allowed_origins="*")
if N8N_AVAILABLE:
    app.register_blueprint(n8n_bp)
client = Client(os.getenv("BINANCE_API_KEY", ""), os.getenv("BINANCE_API_SECRET", ""))

init_db()

DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "trading.db")


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
        # Session stats from pattern_memory
        try:
            conn = sqlite3.connect(DB_PATH)
            c = conn.cursor()
            sess_stats = {}
            for sess in ["ASIA", "LONDON", "NEWYORK"]:
                c.execute(
                    "SELECT COUNT(*), SUM(CASE WHEN result='WIN' THEN 1 ELSE 0 END), "
                    "SUM(CASE WHEN result='WIN' THEN net_pnl ELSE 0 END), "
                    "SUM(CASE WHEN result='LOSS' THEN net_pnl ELSE 0 END) "
                    "FROM pattern_memory WHERE session=?", (sess,)
                )
                row = c.fetchone()
                total_s = row[0] or 0
                wins_s  = row[1] or 0
                pnl_w   = row[2] or 0
                pnl_l   = row[3] or 0
                sess_stats[sess] = {
                    "total":    total_s,
                    "wins":     wins_s,
                    "losses":   total_s - wins_s,
                    "win_rate": round(wins_s / (total_s + 1e-10) * 100, 1),
                    "pnl":      round(pnl_w + pnl_l, 3),
                }
            conn.close()
            stats["session_stats"] = sess_stats
        except:
            pass

        # ML model durumu
        try:
            from ml_signal_scorer import get_scorer
            ml_status = get_scorer().get_status()
            stats["ml_status"] = ml_status
        except:
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
        open_trades = get_trades(limit=20, status="OPEN")
        live = []
        total_unrealized = 0.0

        for t in open_trades:
            symbol    = t["symbol"]
            entry     = t["entry"] or 0
            sl        = t["sl"] or 0
            tp        = t["tp"] or 0
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
        # Paper mode bakiyesi
        try:
            conn = sqlite3.connect(DB_PATH)
            c = conn.cursor()
            c.execute("SELECT paper_balance FROM paper_account LIMIT 1")
            row = c.fetchone()
            conn.close()
            paper_balance = float(row[0]) if row else 250.0
        except:
            paper_balance = 250.0

        # Binance bakiyesi (opsiyonel)
        usdt_balance = 0
        try:
            account = client.futures_account_balance()
            for b in account:
                if b["asset"] == "USDT":
                    usdt_balance = float(b["balance"])
                    break
        except:
            pass

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
    """En iyi ve en kötü performanslı coinler"""
    try:
        conn = sqlite3.connect(DB_PATH)
        c = conn.cursor()
        c.execute("""
            SELECT symbol,
                   COUNT(*) as total,
                   SUM(CASE WHEN result='WIN' THEN 1 ELSE 0 END) as wins,
                   SUM(net_pnl) as total_pnl,
                   AVG(CASE WHEN result='WIN' THEN net_pnl END) as avg_win,
                   AVG(CASE WHEN result='LOSS' THEN net_pnl END) as avg_loss
            FROM pattern_memory
            WHERE result IN ('WIN','LOSS')
            GROUP BY symbol
            HAVING total >= 3
            ORDER BY total_pnl DESC
            LIMIT 20
        """)
        rows = c.fetchall()
        conn.close()
        result = []
        for row in rows:
            sym, total, wins, pnl, avg_w, avg_l = row
            result.append({
                "symbol":   sym,
                "total":    total or 0,
                "wins":     wins or 0,
                "losses":   (total or 0) - (wins or 0),
                "win_rate": round((wins or 0) / max(total, 1) * 100, 1),
                "total_pnl": round(pnl or 0, 3),
                "avg_win":  round(avg_w or 0, 3),
                "avg_loss": round(avg_l or 0, 3),
            })
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
        "/root/trade_engine/bot.log",
        "/root/trade_engine/scalp_bot.log",
        "/tmp/scalp_bot.log",
        os.path.join(os.path.dirname(os.path.abspath(__file__)), "bot.log"),
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
        conn = sqlite3.connect(DB_PATH)
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            "SELECT symbol, direction, result, net_pnl, created_at "
            "FROM pattern_memory ORDER BY id DESC LIMIT ?", (n,)
        ).fetchall()
        conn.close()
        for r in reversed(rows):
            r = dict(r)
            pnl = r.get("net_pnl", 0) or 0
            res = r.get("result", "")
            level = "TRADE" if res == "WIN" else "CLOSE" if res == "LOSS" else "INFO"
            lines_out.append({
                "text":  f"[{r.get('created_at','')}] {r.get('symbol','')} {r.get('direction','')} → {res}  PNL: {pnl:+.4f}$",
                "level": level,
            })
        return jsonify({"ok": True, "data": lines_out, "path": "db_fallback", "total": len(lines_out)})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e), "data": []}), 500


# ── /api/reset — Sadece trades siler, bakiye ve cumulative stats korur ─────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────────
    socketio.run(app, host="0.0.0.0", port=5000, debug=False, allow_unsafe_werkzeug=True)