"""
app.py — AX Dashboard (Flask)

Endpoint'ler:
  /                   → index.html
  /api/stats          → ana metrikler
  /api/calendar       → günlük özet (takvim)
  /api/weekly         → haftalık özet
  /api/trades         → kapanmış trade'ler
  /api/live           → açık trade'ler
  /api/signals        → son signal_candidates
  /api/coins          → coin_profile listesi
  /api/state          → system_state
  /api/logs           → log dosyası
"""

import os
import re
from datetime import datetime, timezone

from flask import Flask, jsonify, render_template, request, make_response
from flask_socketio import SocketIO

import config
from database import get_conn, init_db

app = Flask(__name__, template_folder="templates", static_folder="static")
app.config["SECRET_KEY"] = os.getenv("SECRET_KEY", "ax2026")
socketio = SocketIO(app, cors_allowed_origins="*", async_mode="threading")

init_db()


# ── Yardımcılar ──────────────────────────────────────────────────────────────

def _ok(data):
    return jsonify({"ok": True, "data": data})


def _err(e):
    return jsonify({"ok": False, "error": str(e)}), 500


def _fmt_dur(open_time: str, close_time: str = None) -> str:
    try:
        ot = datetime.fromisoformat(open_time.replace("Z", "+00:00"))
        ct = (datetime.fromisoformat(close_time.replace("Z", "+00:00"))
              if close_time else datetime.now(timezone.utc))
        m = int((ct - ot).total_seconds() / 60)
        h, mn = divmod(m, 60)
        return f"{h}s {mn}dk" if h else f"{mn}dk"
    except Exception:
        return "—"


# ── Routes ───────────────────────────────────────────────────────────────────

@app.route("/")
def index():
    resp = make_response(render_template("index.html"))
    resp.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
    resp.headers["Pragma"] = "no-cache"
    resp.headers["Expires"] = "0"
    return resp


# ── /api/stats ───────────────────────────────────────────────────────────────

@app.route("/api/stats")
def api_stats():
    try:
        conn = get_conn()
        c = conn.cursor()

        c.execute("""
            SELECT
                COUNT(*) total,
                SUM(CASE WHEN result='WIN' THEN 1 ELSE 0 END) wins,
                SUM(CASE WHEN result='LOSS' THEN 1 ELSE 0 END) losses,
                ROUND(SUM(net_pnl), 4) total_pnl,
                ROUND(AVG(r_multiple), 4) avg_r,
                ROUND(AVG(duration_min), 1) avg_dur
            FROM trades
            WHERE status NOT IN ('OPEN','TP1_HIT','TP2_HIT','RUNNER_ACTIVE')
        """)
        row = dict(c.fetchone() or {})

        total = row.get("total") or 0
        wins  = row.get("wins") or 0
        wr    = round(wins / total * 100, 1) if total else 0

        # Profit factor
        c.execute("""
            SELECT
                SUM(CASE WHEN result='WIN' THEN net_pnl ELSE 0 END),
                ABS(SUM(CASE WHEN result='LOSS' THEN net_pnl ELSE 0 END))
            FROM trades
            WHERE status NOT IN ('OPEN','TP1_HIT','TP2_HIT','RUNNER_ACTIVE')
        """)
        pf_row = c.fetchone()
        gp = float(pf_row[0] or 0)
        gl = float(pf_row[1] or 0)
        pf = round(gp / gl, 2) if gl > 0 else round(gp, 2)

        # Max drawdown
        c.execute("""
            SELECT net_pnl FROM trades
            WHERE status NOT IN ('OPEN','TP1_HIT','TP2_HIT','RUNNER_ACTIVE')
            ORDER BY id
        """)
        cum = peak = dd = 0.0
        for (p,) in c.fetchall():
            cum += (p or 0)
            if cum > peak:
                peak = cum
            if cum - peak < dd:
                dd = cum - peak

        # Açık trade
        c.execute("SELECT COUNT(*) FROM trades WHERE status='OPEN'")
        open_cnt = c.fetchone()[0]

        # Bakiye
        c.execute("SELECT paper_balance FROM paper_account WHERE id=1")
        bal_row = c.fetchone()
        balance = float(bal_row[0]) if bal_row else 250.0

        # Günlük PnL
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        c.execute("""
            SELECT ROUND(SUM(net_pnl),4) FROM trades
            WHERE status NOT IN ('OPEN','TP1_HIT','TP2_HIT','RUNNER_ACTIVE')
              AND close_time >= ?
        """, (today,))
        daily_pnl = float(c.fetchone()[0] or 0)

        # System state
        c.execute("SELECT * FROM system_state WHERE id=1")
        state_row = c.fetchone()
        state = dict(state_row) if state_row else {}

        # Best / worst coin
        c.execute("""
            SELECT symbol, ROUND(SUM(net_pnl),4) pnl
            FROM trades
            WHERE status NOT IN ('OPEN','TP1_HIT','TP2_HIT','RUNNER_ACTIVE')
            GROUP BY symbol ORDER BY pnl DESC LIMIT 1
        """)
        best_coin_row = c.fetchone()
        c.execute("""
            SELECT symbol, ROUND(SUM(net_pnl),4) pnl
            FROM trades
            WHERE status NOT IN ('OPEN','TP1_HIT','TP2_HIT','RUNNER_ACTIVE')
            GROUP BY symbol ORDER BY pnl ASC LIMIT 1
        """)
        worst_coin_row = c.fetchone()

        # Top veto reason
        c.execute("""
            SELECT veto_reason, COUNT(*) n
            FROM signal_candidates
            WHERE veto_reason IS NOT NULL
            GROUP BY veto_reason ORDER BY n DESC LIMIT 1
        """)
        veto_row = c.fetchone()

        conn.close()

        return _ok({
            "total_trades":    total,
            "wins":            wins,
            "losses":          row.get("losses") or 0,
            "win_rate":        wr,
            "total_pnl":       row.get("total_pnl") or 0,
            "daily_pnl":       daily_pnl,
            "avg_r":           row.get("avg_r") or 0,
            "profit_factor":   pf,
            "max_drawdown":    round(dd, 4),
            "avg_duration":    row.get("avg_dur") or 0,
            "open_trades":     open_cnt,
            "balance":         balance,
            "ax_mode":         state.get("ax_mode", config.AX_MODE),
            "execution_mode":  state.get("execution_mode", config.EXECUTION_MODE),
            "bot_status":      state.get("bot_status", "unknown"),
            "circuit_breaker": bool(state.get("circuit_breaker_active")),
            "consecutive_losses": state.get("consecutive_losses", 0),
            "best_coin":       best_coin_row[0] if best_coin_row else None,
            "worst_coin":      worst_coin_row[0] if worst_coin_row else None,
            "top_veto":        veto_row[0] if veto_row else None,
        })
    except Exception as e:
        return _err(e)


# ── /api/calendar ─────────────────────────────────────────────────────────────

@app.route("/api/calendar")
def api_calendar():
    try:
        conn = get_conn()
        c = conn.cursor()

        # daily_summary tablosu dolu değilse trades'ten üret
        c.execute("SELECT COUNT(*) FROM daily_summary")
        if c.fetchone()[0] == 0:
            c.execute("""
                SELECT
                    DATE(close_time) day,
                    COUNT(*) total,
                    SUM(CASE WHEN result='WIN' THEN 1 ELSE 0 END) wins,
                    ROUND(SUM(net_pnl),4) pnl,
                    ROUND(AVG(r_multiple),3) avg_r
                FROM trades
                WHERE status NOT IN ('OPEN','TP1_HIT','TP2_HIT','RUNNER_ACTIVE')
                  AND close_time IS NOT NULL
                GROUP BY day ORDER BY day DESC LIMIT 60
            """)
            rows = [dict(r) for r in c.fetchall()]
        else:
            c.execute("""
                SELECT date day, total_trades total, wins, total_pnl pnl, avg_r,
                       win_rate, profit_factor
                FROM daily_summary ORDER BY date DESC LIMIT 60
            """)
            rows = [dict(r) for r in c.fetchall()]

        conn.close()
        for r in rows:
            total = r.get("total") or 0
            wins  = r.get("wins") or 0
            r["win_rate"] = round(wins / total * 100, 1) if total else 0
        return _ok(rows)
    except Exception as e:
        return _err(e)


# ── /api/weekly ───────────────────────────────────────────────────────────────

@app.route("/api/weekly")
def api_weekly():
    try:
        conn = get_conn()
        c = conn.cursor()

        c.execute("SELECT COUNT(*) FROM weekly_summary")
        if c.fetchone()[0] == 0:
            # trades'ten haftalık üret
            c.execute("""
                SELECT
                    strftime('%Y-W%W', close_time) week,
                    COUNT(*) total,
                    SUM(CASE WHEN result='WIN' THEN 1 ELSE 0 END) wins,
                    ROUND(SUM(net_pnl),4) pnl,
                    ROUND(AVG(r_multiple),3) avg_r
                FROM trades
                WHERE status NOT IN ('OPEN','TP1_HIT','TP2_HIT','RUNNER_ACTIVE')
                  AND close_time IS NOT NULL
                GROUP BY week ORDER BY week DESC LIMIT 8
            """)
            rows = [dict(r) for r in c.fetchall()]
            for r in rows:
                total = r.get("total") or 0
                wins  = r.get("wins") or 0
                r["win_rate"] = round(wins / total * 100, 1) if total else 0
        else:
            c.execute("""
                SELECT week_start week, total_trades total, wins,
                       total_pnl pnl, avg_r, win_rate, profit_factor,
                       best_coin, worst_coin
                FROM weekly_summary ORDER BY week_start DESC LIMIT 8
            """)
            rows = [dict(r) for r in c.fetchall()]

        conn.close()
        return _ok(rows)
    except Exception as e:
        return _err(e)


# ── /api/trades ───────────────────────────────────────────────────────────────

@app.route("/api/trades")
def api_trades():
    try:
        limit  = min(int(request.args.get("limit", 50)), 500)
        offset = int(request.args.get("offset", 0))
        conn = get_conn()
        c = conn.cursor()
        c.execute("""
            SELECT * FROM trades
            WHERE status NOT IN ('OPEN','TP1_HIT','TP2_HIT','RUNNER_ACTIVE')
            ORDER BY id DESC LIMIT ? OFFSET ?
        """, (limit, offset))
        rows = [dict(r) for r in c.fetchall()]
        c.execute("""
            SELECT COUNT(*) FROM trades
            WHERE status NOT IN ('OPEN','TP1_HIT','TP2_HIT','RUNNER_ACTIVE')
        """)
        total = c.fetchone()[0]
        conn.close()
        for r in rows:
            r["duration_str"] = _fmt_dur(r.get("open_time",""), r.get("close_time"))
        return jsonify({"ok": True, "data": rows, "total": total})
    except Exception as e:
        return _err(e)


# ── /api/live ─────────────────────────────────────────────────────────────────

@app.route("/api/live")
def api_live():
    try:
        conn = get_conn()
        c = conn.cursor()
        c.execute("""
            SELECT * FROM trades
            WHERE status IN ('OPEN','TP1_HIT','TP2_HIT','RUNNER_ACTIVE')
            ORDER BY id DESC
        """)
        rows = [dict(r) for r in c.fetchall()]
        conn.close()
        for r in rows:
            r["duration_str"] = _fmt_dur(r.get("open_time", ""))
        return _ok(rows)
    except Exception as e:
        return _err(e)


# ── /api/signals ──────────────────────────────────────────────────────────────

@app.route("/api/signals")
def api_signals():
    try:
        limit = min(int(request.args.get("limit", 50)), 200)
        conn = get_conn()
        c = conn.cursor()
        c.execute("""
            SELECT * FROM signal_candidates ORDER BY id DESC LIMIT ?
        """, (limit,))
        rows = [dict(r) for r in c.fetchall()]
        conn.close()
        return _ok(rows)
    except Exception as e:
        return _err(e)


# ── /api/coins ────────────────────────────────────────────────────────────────

@app.route("/api/coins")
def api_coins():
    try:
        conn = get_conn()
        c = conn.cursor()
        c.execute("""
            SELECT * FROM coin_profile ORDER BY trade_count DESC
        """)
        rows = [dict(r) for r in c.fetchall()]
        conn.close()
        return _ok(rows)
    except Exception as e:
        return _err(e)


# ── /api/state ────────────────────────────────────────────────────────────────

@app.route("/api/state")
def api_state():
    try:
        conn = get_conn()
        c = conn.cursor()
        c.execute("SELECT * FROM system_state WHERE id=1")
        row = c.fetchone()
        conn.close()
        return _ok(dict(row) if row else {})
    except Exception as e:
        return _err(e)


# ── /api/logs ─────────────────────────────────────────────────────────────────

@app.route("/api/logs")
def api_logs():
    n = min(int(request.args.get("n", 80)), 300)
    log_path = os.path.join(config.LOG_DIR, "ax_bot.log")
    if not os.path.exists(log_path):
        return _ok([])
    try:
        with open(log_path, "r", encoding="utf-8", errors="replace") as f:
            lines = f.readlines()[-n:]
        result = []
        for raw in lines:
            raw = raw.rstrip("\n")
            level = "INFO"
            if re.search(r"\bERROR\b|\bCRITICAL\b", raw, re.I):
                level = "ERROR"
            elif re.search(r"\bWARNING\b", raw, re.I):
                level = "WARNING"
            elif re.search(r"\bDEBUG\b", raw, re.I):
                level = "DEBUG"
            result.append({"text": raw, "level": level})
        return _ok(result)
    except Exception as e:
        return _err(e)


# ── /api/balance ──────────────────────────────────────────────────────────────

@app.route("/api/balance")
def api_balance():
    try:
        conn = get_conn()
        c = conn.cursor()
        c.execute("SELECT paper_balance FROM paper_account WHERE id=1")
        row = c.fetchone()
        conn.close()
        bal = float(row[0]) if row else 250.0
        return _ok({"balance": bal, "mode": config.EXECUTION_MODE})
    except Exception as e:
        return _err(e)


# ── /api/telegram ────────────────────────────────────────────────────────────

@app.route("/api/telegram")
def api_telegram():
    try:
        import requests as _req
        token = config.TELEGRAM_TOKEN
        chat  = config.TELEGRAM_CHAT_ID
        if not token or not chat:
            return _ok({"configured": False, "ok": False, "error": "token/chat eksik"})
        r = _req.get(f"https://api.telegram.org/bot{token}/getMe", timeout=5)
        data = r.json()
        if data.get("ok"):
            bot = data["result"]
            return _ok({
                "configured": True, "ok": True,
                "bot_name": bot.get("first_name"),
                "username": bot.get("username"),
                "chat_id": chat,
            })
        return _ok({"configured": True, "ok": False, "error": data.get("description")})
    except Exception as e:
        return _ok({"configured": False, "ok": False, "error": str(e)})


# ── Entry point ───────────────────────────────────────────────────────────────

if __name__ == "__main__":
    socketio.run(
        app, host="0.0.0.0", port=5000,
        debug=False, use_reloader=False,
        allow_unsafe_werkzeug=True,
    )
