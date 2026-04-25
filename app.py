"""
app.py — AURVEX Dashboard API
==============================
Flask + SocketIO tabanlı dashboard backend.
Tüm veri get_conn() / database.py üzerinden akar.
"""

import os
import json
import sqlite3
import calendar as cal_lib
from datetime import datetime, timezone, timedelta

from flask import Flask, render_template, jsonify, request
from flask_socketio import SocketIO
from dotenv import load_dotenv

try:
    from n8n_bridge import n8n_bp
    N8N_AVAILABLE = True
except ImportError:
    N8N_AVAILABLE = False

from config import DB_PATH, BINANCE_API_KEY, BINANCE_API_SECRET
from database import (
    init_db, get_trades, get_stats, get_current_params,
    get_open_trades, get_paper_account_balance as get_paper_balance, get_conn,
)
from binance.client import Client
import dashboard_service as dash_svc

load_dotenv()
app = Flask(__name__, template_folder="templates", static_folder="static")
app.config["SECRET_KEY"] = os.getenv("SECRET_KEY", "scalp2026")
socketio = SocketIO(app, cors_allowed_origins="*", async_mode="threading")
if N8N_AVAILABLE:
    app.register_blueprint(n8n_bp)

try:
    client = Client(BINANCE_API_KEY, BINANCE_API_SECRET)
except Exception:
    client = None

init_db()
dash_svc.start()

_DEFAULT_PARAMS = {
    "sl_atr_mult": 1.5, "tp_atr_mult": 2.5,
    "rsi5_min": 40, "rsi5_max": 70,
    "rsi1_min": 40, "rsi1_max": 68,
    "vol_ratio_min": 1.8, "min_volume_m": 5.0,
    "risk_pct": 1.0, "version": 1,
    "ai_reason": "Varsayilan parametre seti.",
}


def _fmt_duration(open_time_str, close_time_str=None):
    try:
        ot = datetime.fromisoformat((open_time_str or "").replace("Z", "+00:00"))
        ct = (datetime.fromisoformat((close_time_str or "").replace("Z", "+00:00"))
              if close_time_str else datetime.now(timezone.utc))
        mins = (ct - ot).total_seconds() / 60
        h, m = divmod(int(mins), 60)
        return (f"{h}s {m}dk" if h else f"{m}dk"), round(mins, 1)
    except Exception:
        return "—", 0


@app.route("/")
def index():
    return render_template("index.html")


# ─────────────────────────────────────────────────────────────────────────────
# /api/stats  — dashboard ana metrikler
# ─────────────────────────────────────────────────────────────────────────────
@app.route("/api/stats")
def api_stats():
    try:
        raw = get_stats()

        # win_rate 0-1 → 0-100  (frontend her zaman yuzde bekler)
        raw["win_rate"] = round((raw.get("win_rate") or 0) * 100, 1)

        # Acik trade sayisi
        raw["open_count"] = len(get_open_trades())

        # Ek metrikler: trades tablosundan hesapla
        closed = get_trades(limit=2000, status="closed")
        if closed:
            pnls   = [t["net_pnl"] or 0 for t in closed]
            wins_t = [t for t in closed if (t["net_pnl"] or 0) > 0]
            loss_t = [t for t in closed if (t["net_pnl"] or 0) <= 0]
            durs   = [t.get("hold_minutes") or 0 for t in closed]
            raw.update({
                "avg_pnl":     round(sum(pnls) / len(pnls), 4),
                "best_trade":  round(max(pnls), 4),
                "worst_trade": round(min(pnls), 4),
                "avg_dur":     round(sum(durs) / max(len(durs), 1), 1),
                "avg_win":     round(sum(t["net_pnl"] for t in wins_t) / max(len(wins_t), 1), 4),
                "avg_loss":    round(sum(t["net_pnl"] for t in loss_t) / max(len(loss_t), 1), 4),
                "avg_rr":      raw.get("avg_r", 0),
            })
        else:
            raw.update({k: 0 for k in
                        ("avg_pnl", "best_trade", "worst_trade",
                         "avg_dur", "avg_win", "avg_loss", "avg_rr")})

        # Parametreler
        raw["params"] = get_current_params() or _DEFAULT_PARAMS

        # Son AI log satiri
        try:
            with get_conn() as conn:
                row = conn.execute(
                    "SELECT reason, created_at FROM ai_logs ORDER BY id DESC LIMIT 1"
                ).fetchone()
                raw["last_ai"] = {"insight": row["reason"], "at": row["created_at"]} if row else {}
        except Exception:
            raw["last_ai"] = {}

        # Seans istatistikleri — frontend key: ASIA, LONDON, NEWYORK (alt cizgisiz)
        try:
            with get_conn() as conn:
                sess_map = {}
                for fe_key, db_keys in [
                    ("ASIA",    ["ASIA"]),
                    ("LONDON",  ["LONDON"]),
                    ("NEWYORK", ["NEWYORK", "NEW_YORK"]),
                    ("OFF",     ["OFF"]),
                ]:
                    ph = ",".join("?" * len(db_keys))
                    row = conn.execute(
                        f"SELECT COUNT(*), "
                        f"SUM(CASE WHEN result='WIN' THEN 1 ELSE 0 END), "
                        f"SUM(r_multiple) "
                        f"FROM coin_market_memory WHERE session IN ({ph})",
                        db_keys
                    ).fetchone()
                    total_s = row[0] or 0
                    wins_s  = row[1] or 0
                    sess_map[fe_key] = {
                        "total":    total_s,
                        "wins":     wins_s,
                        "losses":   total_s - wins_s,
                        "win_rate": round(wins_s / max(total_s, 1) * 100, 1),
                        "avg_r":    round((row[2] or 0) / max(total_s, 1), 2),
                        "pnl":      0,
                    }
            raw["session_stats"] = sess_map
        except Exception:
            raw["session_stats"] = {}

        return jsonify({"ok": True, "data": raw})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500


# ─────────────────────────────────────────────────────────────────────────────
# /api/pnl_chart
# ─────────────────────────────────────────────────────────────────────────────
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
                "r_multiple": t.get("r_multiple") or 0,
                "open_time":  t.get("open_time", ""),
                "close_time": t.get("close_time", ""),
            })
        return jsonify({"ok": True, "data": points})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500


# ─────────────────────────────────────────────────────────────────────────────
# /api/trades  — sayfalanmis trade gecmisi
# ─────────────────────────────────────────────────────────────────────────────
@app.route("/api/trades")
def api_trades():
    try:
        page  = int(request.args.get("page", 1))
        # Frontend per_page gonderir, backward-compat icin limit de kabul edilir
        limit = int(request.args.get("per_page", request.args.get("limit", 10)))
        all_trades  = get_trades(limit=10000, status="closed")
        total_count = len(all_trades)
        total_pages = max(1, (total_count + limit - 1) // limit)
        page        = max(1, min(page, total_pages))
        offset      = (page - 1) * limit
        result = []
        for t in all_trades[offset:offset + limit]:
            dur_str, dur_min = _fmt_duration(t.get("open_time"), t.get("close_time"))
            result.append({
                **t,
                "exit_price":   t.get("close_price"),   # Frontend alias
                "duration_str": dur_str,
                "duration_min": dur_min,
            })
        return jsonify({
            "ok":   True,
            "data": result,
            "pagination": {
                "page":        page,
                "total_pages": total_pages,
                "total":       total_count,
                "limit":       limit,
            },
            "total_count": total_count,
        })
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500


# ─────────────────────────────────────────────────────────────────────────────
# /api/live  — acik pozisyonlar + unrealized PnL
# ─────────────────────────────────────────────────────────────────────────────
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
            tp1       = t.get("tp1") or t.get("tp") or 0
            qty       = t["qty"] or 0
            direction = t["direction"]
            hold_str, hold_min = _fmt_duration(t.get("open_time"))

            try:
                if client:
                    ticker = client.futures_symbol_ticker(symbol=symbol)
                    mark   = float(ticker["price"])
                else:
                    mark = entry
                raw_pnl     = (mark - entry) * qty if direction == "LONG" else (entry - mark) * qty
                sl_dist     = abs(entry - sl)
                tp_dist     = abs(tp1 - entry)
                current_rr  = round(raw_pnl / (sl_dist * qty + 1e-10), 3) if sl_dist else 0
                sl_dist_pct = round(abs(mark - sl) / (mark + 1e-10) * 100, 2)
                progress    = round(min(abs(mark - entry) / (tp_dist + 1e-10) * 100, 100), 1) if tp_dist else 0
                total_unrealized += raw_pnl
                live.append({
                    **t,
                    "tp":              tp1,
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
                    "tp": tp1,
                    "current_price": 0, "unrealized_pnl": 0,
                    "unrealized_pct": 0, "current_rr": 0,
                    "sl_distance_pct": 0, "tp_progress": 0,
                    "hold_str": hold_str, "hold_min": hold_min,
                })

        total_realized = sum(t["net_pnl"] or 0 for t in get_trades(limit=500, status="closed"))
        return jsonify({"ok": True, "data": {
            "live":             live,
            "total_unrealized": round(total_unrealized, 4),
            "total_realized":   round(total_realized, 4),
            "total_pnl":        round(total_unrealized + total_realized, 4),
            "open_count":       len(live),
        }})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500


# ─────────────────────────────────────────────────────────────────────────────
# /api/balance
# ─────────────────────────────────────────────────────────────────────────────
@app.route("/api/balance")
def api_balance():
    try:
        bal = get_paper_balance()
        return jsonify({"ok": True, "data": {
            "paper_balance":  round(bal, 4),
            "usdt_balance":   round(bal, 4),
            "usdt_available": round(bal, 4),
        }})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500


# ─────────────────────────────────────────────────────────────────────────────
# /api/params
# ─────────────────────────────────────────────────────────────────────────────
@app.route("/api/params")
def api_params():
    try:
        return jsonify({"ok": True, "data": get_current_params() or _DEFAULT_PARAMS})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500


# ─────────────────────────────────────────────────────────────────────────────
# /api/coin_stats
# ─────────────────────────────────────────────────────────────────────────────
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


# ─────────────────────────────────────────────────────────────────────────────
# /api/ml_status
# ─────────────────────────────────────────────────────────────────────────────
@app.route("/api/ml_status")
def api_ml_status():
    try:
        from ml_signal_scorer import get_scorer
        return jsonify({"ok": True, "data": get_scorer().get_status()})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500


# ─────────────────────────────────────────────────────────────────────────────
# /api/logs
# ─────────────────────────────────────────────────────────────────────────────
@app.route("/api/logs")
def api_logs():
    import re
    LOG_PATHS = [
        os.path.join(os.path.dirname(os.path.abspath(__file__)), "logs", "ax_bot.log"),
        "/root/trade_engine/logs/ax_bot.log",
        "/root/trade_engine/logs/bot.log",
    ]
    n = min(int(request.args.get("n", 80)), 300)
    for path in LOG_PATHS:
        if os.path.exists(path):
            try:
                with open(path, "r", encoding="utf-8", errors="replace") as f:
                    raw_lines = f.readlines()[-n:]
                lines_out = []
                for raw in raw_lines:
                    raw = raw.rstrip("\n")
                    level = "INFO"
                    if re.search(r"\bERROR\b|\bCRITICAL\b|Traceback", raw, re.I):
                        level = "ERROR"
                    elif re.search(r"\bWARNING\b", raw, re.I):
                        level = "WARNING"
                    elif re.search(r"WIN|KAPANDI|LONG|SHORT", raw):
                        level = "TRADE"
                    lines_out.append({"text": raw, "level": level})
                return jsonify({"ok": True, "data": lines_out, "path": path})
            except Exception:
                continue
    try:
        with get_conn() as conn:
            rows = conn.execute(
                "SELECT symbol, direction, close_reason, net_pnl, close_time "
                "FROM trades WHERE close_time IS NOT NULL ORDER BY id DESC LIMIT ?", (n,)
            ).fetchall()
        lines_out = []
        for r in reversed(rows):
            r = dict(r)
            pnl = r.get("net_pnl", 0) or 0
            lines_out.append({
                "text":  f"[{r.get('close_time','')}] {r.get('symbol','')} "
                         f"{r.get('direction','')} -> {r.get('close_reason','?').upper()} "
                         f"PNL: {pnl:+.4f}$",
                "level": "TRADE" if pnl > 0 else "CLOSE",
            })
        return jsonify({"ok": True, "data": lines_out, "path": "db_fallback"})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e), "data": []}), 500


# ─────────────────────────────────────────────────────────────────────────────
# /api/coin_library
# ─────────────────────────────────────────────────────────────────────────────
@app.route("/api/coin_library")
def api_coin_library():
    try:
        with get_conn() as conn:
            tbl = conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name='coin_params'"
            ).fetchone()
            if not tbl:
                return jsonify({"ok": True, "data": [], "note": "coin_params tablosu henuz olusturulmadi"})
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
        allowed = ["enabled", "volatility_profile", "sl_atr_mult",
                   "tp_atr_mult", "risk_pct", "max_leverage"]
        updates = {k: v for k, v in data.items() if k in allowed}
        if not updates:
            return jsonify({"ok": False, "error": "Guncellenecek alan yok"}), 400
        sets = ", ".join(f"{k}=?" for k in updates)
        with get_conn() as conn:
            conn.execute(f"UPDATE coin_params SET {sets} WHERE symbol=?",
                         list(updates.values()) + [symbol])
        return jsonify({"ok": True, "symbol": symbol, "updated": updates})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500


# ─────────────────────────────────────────────────────────────────────────────
# /api/daily_pnl  — 30 gunluk takvim
# ─────────────────────────────────────────────────────────────────────────────
@app.route("/api/daily_pnl")
def api_daily_pnl():
    try:
        days = int(request.args.get("days", 30))
        return jsonify({"ok": True, "data": dash_svc.get_calendar_data(days)})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500


# ─────────────────────────────────────────────────────────────────────────────
# /api/weekly
# ─────────────────────────────────────────────────────────────────────────────
@app.route("/api/weekly")
def api_weekly():
    try:
        weeks = int(request.args.get("weeks", 8))
        return jsonify({"ok": True, "data": dash_svc.get_weekly_data(weeks)})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500


# ─────────────────────────────────────────────────────────────────────────────
# /api/ax_status
# ─────────────────────────────────────────────────────────────────────────────
@app.route("/api/ax_status")
def api_ax_status():
    try:
        return jsonify({"ok": True, "data": dash_svc.get_ax_status()})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500


# ─────────────────────────────────────────────────────────────────────────────
# /api/coin_profiles
# ─────────────────────────────────────────────────────────────────────────────
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


# ─────────────────────────────────────────────────────────────────────────────
# /api/signal_stats  — DUZELTME: 'decision' kolonu (ax_decision degil)
# ─────────────────────────────────────────────────────────────────────────────
@app.route("/api/signal_stats")
def api_signal_stats():
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
            dec = (row[0] or "PENDING").upper()
            cnt = row[1] or 0
            if dec in stats:
                stats[dec] = cnt
            stats["total"] += cnt
        return jsonify({"ok": True, "data": stats})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500


# ─────────────────────────────────────────────────────────────────────────────
# /api/calendar-pnl  — Aylik takvim (ay secelebilir)
# ─────────────────────────────────────────────────────────────────────────────
@app.route("/api/calendar-pnl")
def api_calendar_pnl():
    try:
        month = request.args.get("month", datetime.now(timezone.utc).strftime("%Y-%m"))
        year, mon = int(month[:4]), int(month[5:7])
        _, last_day = cal_lib.monthrange(year, mon)
        start_date = f"{year}-{mon:02d}-01"
        end_date   = f"{year}-{mon:02d}-{last_day:02d}"
        balance    = get_paper_balance()

        with get_conn() as conn:
            rows = conn.execute("""
                SELECT DATE(close_time) as d,
                       SUM(net_pnl)  as pnl,
                       COUNT(*)      as trades,
                       SUM(CASE WHEN net_pnl > 0  THEN 1 ELSE 0 END) as wins,
                       SUM(CASE WHEN net_pnl <= 0 THEN 1 ELSE 0 END) as losses
                FROM trades
                WHERE DATE(close_time) BETWEEN ? AND ?
                  AND close_time IS NOT NULL
                GROUP BY DATE(close_time)
                ORDER BY d
            """, (start_date, end_date)).fetchall()

        days_map = {}
        for r in rows:
            pnl = r[1] or 0
            tc  = r[2] or 0
            w   = r[3] or 0
            days_map[r[0]] = {
                "date":     r[0],
                "pnl":      round(pnl, 4),
                "pnl_pct":  round(pnl / max(balance, 1) * 100, 2),
                "trades":   tc,
                "wins":     w,
                "losses":   r[4] or 0,
                "win_rate": round(w / max(tc, 1) * 100, 1),
                "tag":      None,
            }

        if days_map:
            best  = max(days_map.values(), key=lambda x: x["pnl"])
            worst = min(days_map.values(), key=lambda x: x["pnl"])
            if best["pnl"]  > 0: best["tag"]  = "best"
            if worst["pnl"] < 0: worst["tag"] = "worst"

        weeks = []
        for w_n in range(1, 6):
            w_start = (w_n - 1) * 7 + 1
            w_end   = min(w_n * 7, last_day)
            w_pnl   = sum(v["pnl"] for k, v in days_map.items()
                          if w_start <= int(k[8:]) <= w_end)
            w_days  = sum(1 for k in days_map if w_start <= int(k[8:]) <= w_end)
            if w_days:
                weeks.append({"week": w_n, "pnl": round(w_pnl, 4), "days": w_days})

        all_pnls = [v["pnl"] for v in days_map.values()]
        ttrades  = sum(v["trades"] for v in days_map.values())
        twins    = sum(v["wins"]   for v in days_map.values())
        running, peak, max_dd = 0, 0, 0
        for p in all_pnls:
            running += p
            peak     = max(peak, running)
            max_dd   = max(max_dd, peak - running)

        return jsonify({"ok": True, "data": {
            "month":          month,
            "days":           list(days_map.values()),
            "weeks":          weeks,
            "month_total":    round(sum(all_pnls), 4),
            "month_win_rate": round(twins / max(ttrades, 1) * 100, 1),
            "month_trades":   ttrades,
            "max_drawdown":   round(-max_dd, 4),
        }})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500


# ─────────────────────────────────────────────────────────────────────────────
# /api/equity-curve  — Trade bazli kumulatif PnL + drawdown
# ─────────────────────────────────────────────────────────────────────────────
@app.route("/api/equity-curve")
def api_equity_curve():
    try:
        trades   = get_trades(limit=500, status="closed")
        initial  = float(os.getenv("PAPER_BALANCE", "250.0"))
        balance  = get_paper_balance()
        points   = []
        cumul    = 0
        run_bal  = initial
        peak     = initial

        for t in reversed(trades):
            pnl     = t.get("net_pnl") or 0
            cumul  += pnl
            run_bal = initial + cumul
            peak    = max(peak, run_bal)
            points.append({
                "trade_id":   t["id"],
                "symbol":     t["symbol"],
                "pnl":        round(pnl, 4),
                "cumulative": round(cumul, 4),
                "balance":    round(run_bal, 4),
                "drawdown":   round(-(peak - run_bal), 4),
                "r_multiple": t.get("r_multiple") or 0,
                "close_time": t.get("close_time", ""),
            })

        return jsonify({"ok": True, "data": points,
                        "current_balance": balance, "initial_balance": initial})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500


# ─────────────────────────────────────────────────────────────────────────────
# /api/winrate-rr  — WinRate / RR / Expectancy heatmap
# ─────────────────────────────────────────────────────────────────────────────
@app.route("/api/winrate-rr")
def api_winrate_rr():
    try:
        raw = get_stats()
        wr  = raw.get("win_rate", 0)       # 0-1
        avg_r = raw.get("avg_r", 0)
        aw  = raw.get("avg_win", 0) or 0
        al  = abs(raw.get("avg_loss", 0) or 0) or 1
        rr  = round(aw / al, 2) if al else 0

        expectancy = round((wr * rr) - ((1 - wr) * 1.0), 3)

        if expectancy >= 0.5:
            status = "strong_zone"
        elif expectancy > 0:
            status = "profitable_zone"
        elif abs(expectancy) < 0.05:
            status = "breakeven"
        else:
            status = "below_breakeven"

        heatmap = []
        for wr_pct in range(30, 81, 5):
            for rr_x10 in range(5, 31, 5):
                rr_val = rr_x10 / 10
                w      = wr_pct / 100
                exp    = round((w * rr_val) - ((1 - w) * 1.0), 3)
                heatmap.append({"wr": wr_pct, "rr": rr_val, "expectancy": exp})

        return jsonify({"ok": True, "data": {
            "current_wr":         round(wr * 100, 1),
            "current_rr":         rr,
            "current_avg_r":      round(avg_r, 2),
            "current_expectancy": expectancy,
            "status":             status,
            "target_zone":        {"wr_min": 45, "wr_max": 55, "rr_min": 1.5},
            "heatmap":            heatmap,
        }})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500


# ─────────────────────────────────────────────────────────────────────────────
# /api/session-performance
# ─────────────────────────────────────────────────────────────────────────────
@app.route("/api/session-performance")
def api_session_performance():
    try:
        from collections import Counter
        with get_conn() as conn:
            result = {}
            for fe_key, db_keys in [
                ("ASIA",    ["ASIA"]),
                ("LONDON",  ["LONDON"]),
                ("NEWYORK", ["NEWYORK", "NEW_YORK"]),
                ("OFF",     ["OFF"]),
            ]:
                ph   = ",".join("?" * len(db_keys))
                rows = conn.execute(
                    f"SELECT result, r_multiple, symbol FROM coin_market_memory "
                    f"WHERE session IN ({ph})", db_keys
                ).fetchall()
                total   = len(rows)
                wins    = sum(1 for r in rows if r[0] == "WIN")
                rs      = [r[1] or 0 for r in rows]
                win_rs  = [r[1] or 0 for r in rows if r[0] == "WIN"]
                loss_rs = [abs(r[1] or 0) for r in rows if r[0] != "WIN"]
                syms    = [r[2] for r in rows if r[2]]
                top_coin = Counter(syms).most_common(1)[0][0] if syms else None
                result[fe_key] = {
                    "trade_count":   total,
                    "wins":          wins,
                    "losses":        total - wins,
                    "win_rate":      round(wins / max(total, 1) * 100, 1),
                    "avg_r":         round(sum(rs) / max(total, 1), 2),
                    "profit_factor": round(sum(win_rs) / max(sum(loss_rs), 1e-10), 2),
                    "top_coin":      top_coin,
                }
        return jsonify({"ok": True, "data": result})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500


# ─────────────────────────────────────────────────────────────────────────────
# /api/coin-performance
# ─────────────────────────────────────────────────────────────────────────────
@app.route("/api/coin-performance")
def api_coin_performance():
    try:
        with get_conn() as conn:
            rows = conn.execute("""
                SELECT cp.symbol,
                       cp.trade_count,
                       cp.win_count,
                       cp.loss_count,
                       ROUND(cp.win_rate * 100, 1) as win_rate_pct,
                       cp.avg_r,
                       cp.profit_factor,
                       cp.avg_mfe,
                       cp.avg_mae,
                       cp.danger_score,
                       cp.preferred_direction,
                       cp.best_session,
                       cp.volatility_profile,
                       (SELECT SUM(t.net_pnl) FROM trades t
                        WHERE t.symbol = cp.symbol AND t.status = 'closed') as total_pnl,
                       (SELECT cc.until FROM coin_cooldown cc
                        WHERE cc.symbol = cp.symbol AND cc.until > datetime('now')
                        LIMIT 1) as cooldown_until
                FROM coin_profile cp
                ORDER BY cp.trade_count DESC
            """).fetchall()
        result = []
        for r in rows:
            d = dict(r)
            d["total_pnl"]   = round(d.get("total_pnl") or 0, 4)
            d["in_cooldown"] = d["cooldown_until"] is not None
            result.append(d)
        return jsonify({"ok": True, "data": result})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500


# ─────────────────────────────────────────────────────────────────────────────
# /api/veto-stats
# ─────────────────────────────────────────────────────────────────────────────
@app.route("/api/veto-stats")
def api_veto_stats():
    try:
        with get_conn() as conn:
            dec_rows = conn.execute("""
                SELECT decision, COUNT(*) as cnt
                FROM signal_candidates
                GROUP BY decision
            """).fetchall()
            veto_rows = conn.execute("""
                SELECT veto_reason, COUNT(*) as cnt
                FROM signal_candidates
                WHERE decision = 'VETO' AND veto_reason IS NOT NULL
                GROUP BY veto_reason
                ORDER BY cnt DESC
                LIMIT 20
            """).fetchall()
            allow_row = conn.execute("""
                SELECT COUNT(DISTINCT sc.id),
                       COUNT(DISTINCT t.id),
                       SUM(CASE WHEN t.net_pnl > 0 THEN 1 ELSE 0 END)
                FROM signal_candidates sc
                LEFT JOIN trades t ON t.linked_candidate_id = sc.id
                WHERE sc.decision = 'ALLOW'
            """).fetchone()

        decisions = {"ALLOW": 0, "VETO": 0, "WATCH": 0, "PENDING": 0}
        for row in dec_rows:
            dec = (row[0] or "PENDING").upper()
            if dec in decisions:
                decisions[dec] = row[1] or 0

        veto_reasons = {(r[0] or "unknown"): (r[1] or 0) for r in veto_rows}

        allow_wr = 0
        if allow_row and allow_row[1] and allow_row[2]:
            allow_wr = round(allow_row[2] / allow_row[1] * 100, 1)

        return jsonify({"ok": True, "data": {
            "decisions":      decisions,
            "veto_reasons":   veto_reasons,
            "allow_win_rate": allow_wr,
            "total":          sum(decisions.values()),
        }})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500


# ─────────────────────────────────────────────────────────────────────────────
# /api/health  — sistem sagligi
# ─────────────────────────────────────────────────────────────────────────────
@app.route("/api/health")
def api_health():
    try:
        db_ok = False
        try:
            with get_conn() as conn:
                conn.execute("SELECT 1").fetchone()
            db_ok = True
        except Exception:
            pass

        last_scan  = None
        last_trade = None
        last_error = None
        try:
            with get_conn() as conn:
                r = conn.execute(
                    "SELECT value FROM system_state WHERE key='last_scan_at'"
                ).fetchone()
                if r:
                    last_scan = r[0]
                r2 = conn.execute(
                    "SELECT close_time FROM trades ORDER BY id DESC LIMIT 1"
                ).fetchone()
                if r2:
                    last_trade = r2[0]
                r3 = conn.execute(
                    "SELECT reason, created_at FROM ai_logs "
                    "WHERE event='error' ORDER BY id DESC LIMIT 1"
                ).fetchone()
                if r3:
                    last_error = {"msg": r3[0], "at": r3[1]}
        except Exception:
            pass

        return jsonify({"ok": True, "data": {
            "db_connected":   db_ok,
            "dashboard_ok":   True,
            "binance_client": client is not None,
            "last_scan_at":   last_scan,
            "last_trade_at":  last_trade,
            "last_error":     last_error,
            "timestamp":      datetime.now(timezone.utc).isoformat(),
        }})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500


if __name__ == "__main__":
    socketio.run(app, host="0.0.0.0", port=5000,
                 debug=False, use_reloader=False, allow_unsafe_werkzeug=True)
