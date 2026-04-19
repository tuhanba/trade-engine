import os, json, math, sqlite3
from datetime import datetime, timezone
from flask import Flask, render_template, jsonify, request
from flask_socketio import SocketIO
from dotenv import load_dotenv
from database import init_db, get_trades, get_stats, get_current_params
from binance.client import Client

load_dotenv()
app = Flask(__name__, template_folder="templates", static_folder="static")
app.config["SECRET_KEY"] = os.getenv("SECRET_KEY", "scalp2026")
socketio = SocketIO(app, cors_allowed_origins="*")

PAPER_MODE = os.getenv("PAPER_MODE", "true").lower() in ("true", "1", "yes")
DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "trading.db")

client = Client(os.getenv("BINANCE_API_KEY", ""), os.getenv("BINANCE_API_SECRET", ""))
init_db()

def _get_paper_balance() -> float:
    """paper_account tablosundan güncel bakiyeyi döndürür."""
    try:
        conn = sqlite3.connect(DB_PATH)
        row = conn.execute("SELECT paper_balance FROM paper_account LIMIT 1").fetchone()
        conn.close()
        return float(row[0]) if row else 250.0
    except:
        return 250.0

@app.route("/")
def index():
    return render_template("index.html")

# ── /api/stats ────────────────────────────────────────────────────────────────
@app.route("/api/stats")
def api_stats():
    try:
        stats = get_stats()
        # Ek hesaplamalar
        trades = get_trades(limit=500, status="closed")
        if trades:
            pnls = [t["net_pnl"] or 0 for t in trades]
            cumulative = 0
            peak = 0
            max_dd = 0
            for p in reversed(pnls):
                cumulative += p
                if cumulative > peak:
                    peak = cumulative
                dd = peak - cumulative
                if dd > max_dd:
                    max_dd = dd

            wins   = [p for p in pnls if p > 0]
            losses = [p for p in pnls if p <= 0]
            stats["best_trade"]   = round(max(pnls), 4) if pnls else 0
            stats["worst_trade"]  = round(min(pnls), 4) if pnls else 0
            stats["max_drawdown"] = round(max_dd, 4)
            stats["avg_win"]      = round(sum(wins) / len(wins), 4) if wins else 0
            stats["avg_loss"]     = round(sum(losses) / len(losses), 4) if losses else 0
            stats["profit_factor"] = round(
                abs(sum(wins)) / (abs(sum(losses)) + 1e-10), 2
            ) if losses else 0

            # Session performansı (pattern_memory tablosundan)
            try:
                conn = sqlite3.connect(DB_PATH)
                c = conn.cursor()
                c.execute("""
                    SELECT session,
                           COUNT(*) total,
                           SUM(CASE WHEN result='WIN' THEN 1 ELSE 0 END) wins,
                           ROUND(SUM(net_pnl),4) pnl
                    FROM pattern_memory
                    GROUP BY session
                """)
                rows = c.fetchall()
                conn.close()
                stats["session_stats"] = [
                    {"session": r[0], "total": r[1], "wins": r[2],
                     "win_rate": round(r[2]/(r[1]+1e-10)*100,1), "pnl": r[3]}
                    for r in rows
                ]
            except:
                stats["session_stats"] = []
        else:
            stats["best_trade"]    = 0
            stats["worst_trade"]   = 0
            stats["max_drawdown"]  = 0
            stats["avg_win"]       = 0
            stats["avg_loss"]      = 0
            stats["profit_factor"] = 0
            stats["session_stats"] = []

        # Paper bakiyesini ekle
        if PAPER_MODE:
            stats["paper_balance"] = _get_paper_balance()

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
            })
        return jsonify({"ok": True, "data": points})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500

# ── /api/trades (sayfalandırılmış) ───────────────────────────────────────────
@app.route("/api/trades")
def api_trades():
    try:
        page     = int(request.args.get("page", 1))
        per_page = int(request.args.get("per_page", 10))
        all_trades = get_trades(limit=1000, status="closed")

        # Süre hesapla
        for t in all_trades:
            try:
                ot = datetime.fromisoformat(t["open_time"].replace("Z", "+00:00"))
                ct = datetime.fromisoformat(t["close_time"].replace("Z", "+00:00"))
                dur_min = (ct - ot).total_seconds() / 60
                t["duration_str"] = _fmt_duration(dur_min)
                t["duration_min"] = round(dur_min, 1)
            except:
                t["duration_str"] = "-"
                t["duration_min"] = 0

        total      = len(all_trades)
        total_pages = math.ceil(total / per_page) if total else 1
        start      = (page - 1) * per_page
        end        = start + per_page
        page_data  = all_trades[start:end]

        return jsonify({
            "ok": True,
            "data": page_data,
            "pagination": {
                "page":        page,
                "per_page":    per_page,
                "total":       total,
                "total_pages": total_pages,
            }
        })
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500

def _fmt_duration(minutes):
    if minutes < 1:
        return f"{int(minutes*60)}s"
    elif minutes < 60:
        return f"{int(minutes)}dk"
    else:
        h = int(minutes // 60)
        m = int(minutes % 60)
        return f"{h}s {m}dk"

# ── /api/live ─────────────────────────────────────────────────────────────────
@app.route("/api/live")
def api_live():
    try:
        open_trades = get_trades(limit=50, status="OPEN")
        live = []
        total_unrealized = 0.0

        for t in open_trades:
            symbol    = t["symbol"]
            entry     = t["entry"] or 0
            sl        = t["sl"] or 0
            tp        = t["tp"] or 0
            qty       = t["qty"] or 0
            direction = t["direction"]

            # Pozisyon süresi
            try:
                ot = datetime.fromisoformat(t["open_time"].replace("Z", "+00:00"))
                hold_min = (datetime.now(timezone.utc) - ot).total_seconds() / 60
                t["hold_str"] = _fmt_duration(hold_min)
                t["hold_min"] = round(hold_min, 1)
            except:
                t["hold_str"] = "-"
                t["hold_min"] = 0

            try:
                ticker = client.futures_symbol_ticker(symbol=symbol)
                mark   = float(ticker["price"])
                raw_pnl = (mark - entry) * qty if direction == "LONG" else (entry - mark) * qty
                sl_dist = abs(entry - sl)
                current_rr = round(raw_pnl / (sl_dist * qty + 1e-10), 3) if sl_dist else 0

                # SL'e uzaklık yüzdesi
                if sl_dist > 0:
                    if direction == "LONG":
                        sl_pct = round((mark - sl) / sl_dist * 100, 1)
                    else:
                        sl_pct = round((sl - mark) / sl_dist * 100, 1)
                else:
                    sl_pct = 0

                total_unrealized += raw_pnl
                live.append({**t,
                    "current_price":  round(mark, 6),
                    "unrealized_pnl": round(raw_pnl, 4),
                    "unrealized_pct": round(raw_pnl / (entry * qty + 1e-10) * 100, 2),
                    "current_rr":     current_rr,
                    "sl_distance_pct": sl_pct,
                })
            except:
                live.append({**t,
                    "current_price": 0, "unrealized_pnl": 0,
                    "unrealized_pct": 0, "current_rr": 0, "sl_distance_pct": 0
                })

        closed = get_trades(limit=500, status="closed")
        total_realized = sum(t["net_pnl"] or 0 for t in closed)

        # Paper bakiyesi
        paper_balance = _get_paper_balance() if PAPER_MODE else None

        return jsonify({"ok": True, "data": {
            "live":             live,
            "total_unrealized": round(total_unrealized, 4),
            "total_realized":   round(total_realized, 4),
            "total_pnl":        round(total_unrealized + total_realized, 4),
            "paper_balance":    paper_balance,
            "open_count":       len(live),
        }})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500

# ── /api/balance ──────────────────────────────────────────────────────────────
@app.route("/api/balance")
def api_balance():
    # PAPER_MODE: paper_account tablosundan oku
    if PAPER_MODE:
        try:
            paper_bal = _get_paper_balance()
            # Açık pozisyonların unrealized PNL'sini hesapla
            open_trades = get_trades(limit=50, status="OPEN")
            total_unrealized = 0.0
            for t in open_trades:
                entry     = t["entry"] or 0
                qty       = t["qty"] or 0
                direction = t["direction"]
                symbol    = t["symbol"]
                try:
                    ticker = client.futures_symbol_ticker(symbol=symbol)
                    mark   = float(ticker["price"])
                    raw_pnl = (mark - entry) * qty if direction == "LONG" else (entry - mark) * qty
                    total_unrealized += raw_pnl
                except:
                    pass

            closed = get_trades(limit=500, status="closed")
            total_realized = sum(t["net_pnl"] or 0 for t in closed)

            return jsonify({"ok": True, "data": {
                "paper_mode":      True,
                "usdt_balance":    round(paper_bal, 4),
                "usdt_available":  round(paper_bal, 4),
                "usdt_unrealized": round(total_unrealized, 4),
                "total_realized":  round(total_realized, 4),
                "total_pnl":       round(total_unrealized + total_realized, 4),
                "open_count":      len(open_trades),
            }})
        except Exception as e:
            return jsonify({"ok": False, "error": str(e)}), 500

    # LIVE MODE: Binance API'den oku
    try:
        account = client.futures_account_balance()
        balances = {}
        for b in account:
            if float(b["balance"]) > 0:
                balances[b["asset"]] = {
                    "balance":          float(b["balance"]),
                    "availableBalance": float(b.get("withdrawAvailable", b["balance"])),
                    "crossUnPnl":       float(b.get("crossUnPnl", 0)),
                }
        usdt = balances.get("USDT", {})
        return jsonify({"ok": True, "data": {
            "paper_mode":      False,
            "usdt_balance":    usdt.get("balance", 0),
            "usdt_available":  usdt.get("availableBalance", 0),
            "usdt_unrealized": usdt.get("crossUnPnl", 0),
            "all":             balances,
        }})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500

if __name__ == "__main__":
    socketio.run(app, host="0.0.0.0", port=5000, debug=False, allow_unsafe_werkzeug=True)
