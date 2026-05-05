import os, json, sqlite3, re, time
from datetime import datetime, timezone
from flask import Flask, render_template, jsonify, request
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
socketio = SocketIO(app, cors_allowed_origins="*", async_mode="eventlet")
client = Client(os.getenv("BINANCE_API_KEY", ""), os.getenv("BINANCE_API_SECRET", ""))
init_db()
dash_svc.start()

# Health durumu icin son hata kaydedici
_last_error = {"msg": "", "time": ""}
_last_signal_time = {"time": ""}
_last_price_update = {"time": ""}

@app.route("/")
def index():
    return render_template("index.html")

@app.route("/api/stats")
def api_stats():
    try:
        stats = get_stats()
        with get_conn() as conn:
            row = conn.execute("SELECT COUNT(*) FROM trades").fetchone()
            stats["ghost_trades_count"] = row[0] if row else 0
            row_sentiment = conn.execute("SELECT value FROM state WHERE key='market_sentiment'").fetchone()
            stats["market_sentiment"] = float(row_sentiment[0]) if row_sentiment else 50.0
            # 10/10 kalite dağılımı
            quality_rows = conn.execute(
                """SELECT setup_quality, COUNT(*) cnt,
                          SUM(CASE WHEN result='WIN' THEN 1 ELSE 0 END) wins,
                          ROUND(AVG(net_pnl),3) avg_pnl
                   FROM trades WHERE result IS NOT NULL
                   GROUP BY setup_quality ORDER BY cnt DESC"""
            ).fetchall()
            stats["quality_distribution"] = [
                {"quality": r[0] or "?", "count": r[1], "wins": r[2] or 0,
                 "win_rate": round(100.0 * (r[2] or 0) / r[1], 1) if r[1] else 0,
                 "avg_pnl": r[3] or 0}
                for r in quality_rows
            ]
            # Watchlist (B kalite) sinyal sayısı
            watch_row = conn.execute(
                "SELECT COUNT(*) FROM signal_candidates WHERE decision='WATCH'"
            ).fetchone()
            stats["watchlist_count"] = watch_row[0] if watch_row else 0
            # Veto sayısı
            veto_row = conn.execute(
                "SELECT COUNT(*) FROM signal_candidates WHERE decision='VETO'"
            ).fetchone()
            stats["veto_count"] = veto_row[0] if veto_row else 0
        return jsonify({"ok": True, "data": stats})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500

@app.route("/api/live")
def api_live():
    try:
        open_trades = get_open_trades()
        live = []
        total_unrealized = 0.0
        error_msg = None

        for t in open_trades:
            trade = dict(t)
            symbol = trade.get("symbol", "")
            entry  = trade.get("entry", 0) or 0
            sl     = trade.get("sl", 0) or 0
            tp1    = trade.get("tp1", 0) or 0
            tp2    = trade.get("tp2", 0) or 0
            tp3    = trade.get("tp3", 0) or 0
            direction = (trade.get("direction") or "").upper()
            qty    = trade.get("qty", 0) or 0
            lev    = trade.get("leverage", 10) or 10
            # qty=0 ise notional_size'dan hesapla
            if qty == 0 and entry > 0:
                notional = trade.get("notional_size", 0) or 0
                if notional > 0:
                    qty = notional / entry

            # Binance'ten guncel fiyat al
            current_price = trade.get("current_price") or 0
            try:
                import requests as _req
                _resp = _req.get(
                    "https://fapi.binance.com/fapi/v1/ticker/price",
                    params={"symbol": symbol}, timeout=5
                )
                if _resp.status_code == 200:
                    _p = float(_resp.json().get("price", 0))
                    if _p > 0:
                        current_price = _p
                if not current_price:
                    ticker = client.futures_ticker(symbol=symbol)
                    current_price = float(ticker["lastPrice"])
                _last_price_update["time"] = datetime.now(timezone.utc).isoformat()
            except Exception as pe:
                error_msg = str(pe)
                _last_error["msg"] = str(pe)
                _last_error["time"] = datetime.now(timezone.utc).isoformat()

            # PnL hesapla
            unrealized_pnl = 0.0
            pnl_percent = 0.0
            if current_price and entry and qty:
                if direction == "LONG":
                    unrealized_pnl = (current_price - entry) * qty
                elif direction == "SHORT":
                    unrealized_pnl = (entry - current_price) * qty
                pnl_percent = round((unrealized_pnl / (entry * qty)) * 100, 2) if entry * qty else 0

            # Mesafe hesapla
            def dist_pct(target):
                if not target or not current_price:
                    return None
                return round(abs(current_price - target) / current_price * 100, 2)

            # Trade stage ve active target
            status = trade.get("status", "open")
            trade_stage = trade.get("trade_stage") or status
            active_target = trade.get("active_target") or "tp1"

            enriched = {
                **trade,
                "current_price":      round(current_price, 6) if current_price else None,
                "unrealized_pnl":     round(unrealized_pnl, 4),
                "unrealized_pct":     pnl_percent,
                "tp3":                tp3,
                "runner_target":      trade.get("runner_target"),
                "trade_stage":        trade_stage,
                "active_target":      active_target,
                "tp1_hit":            trade.get("tp1_hit", 0),
                "tp2_hit":            trade.get("tp2_hit", 0),
                "distance_to_sl":     dist_pct(sl),
                "distance_to_tp1":    dist_pct(tp1),
                "distance_to_tp2":    dist_pct(tp2),
                "distance_to_tp3":    dist_pct(tp3),
                "ai_confidence":      trade.get("confidence", 0.8),
                "leverage":           lev,
                "notional_size":      trade.get("notional_size", 0),
                "position_size":      trade.get("position_size", 0),
                "error":              error_msg,
            }
            total_unrealized += unrealized_pnl
            live.append(enriched)

            # DB'ye current_price ve unrealized_pnl yaz
            if current_price:
                try:
                    from database import update_trade
                    update_trade(trade["id"], {
                        "current_price": round(current_price, 6),
                        "unrealized_pnl": round(unrealized_pnl, 4),
                    })
                except Exception:
                    pass

        return jsonify({
            "ok": True,
            "data": {
                "live": live,
                "open_count": len(live),
                "total_unrealized": round(total_unrealized, 4),
                "error": error_msg,
            }
        })
    except Exception as e:
        _last_error["msg"] = str(e)
        _last_error["time"] = datetime.now(timezone.utc).isoformat()
        return jsonify({"ok": False, "error": str(e)}), 500

@app.route("/api/health")
def api_health():
    try:
        # DB durumu
        db_status = "ok"
        open_trade_count = 0
        try:
            with get_conn() as conn:
                open_trade_count = conn.execute(
                    "SELECT COUNT(*) FROM trades WHERE status NOT IN ('closed','closed_win','closed_loss','sl','trail','timeout')"
                ).fetchone()[0]
        except Exception as e:
            db_status = f"error: {e}"

        # Binance durumu
        binance_status = "ok"
        try:
            client.ping()
        except Exception as e:
            binance_status = f"error: {e}"

        # Telegram durumu
        telegram_status = "ok"
        try:
            from config import TELEGRAM_BOT_TOKEN
            import requests as req
            r = req.get(f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/getMe", timeout=5)
            if r.status_code != 200:
                telegram_status = f"error: HTTP {r.status_code}"
        except Exception as e:
            telegram_status = f"error: {e}"

        # Scanner durumu
        scanner_status = "running" if dash_svc._running else "stopped"

        return jsonify({
            "ok": True,
            "data": {
                "db_status":             db_status,
                "binance_status":        binance_status,
                "telegram_status":       telegram_status,
                "scanner_status":        scanner_status,
                "open_trade_count":      open_trade_count,
                "last_signal_time":      _last_signal_time.get("time", ""),
                "last_price_update_time": _last_price_update.get("time", ""),
                "last_error":            _last_error.get("msg", ""),
                "last_error_time":       _last_error.get("time", ""),
                "timestamp":             datetime.now(timezone.utc).isoformat(),
            }
        })
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500

@app.route("/api/ax_status")
def api_ax_status():
    try:
        status = dash_svc.get_ax_status()
        return jsonify({"ok": True, "data": status})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500

@app.route("/api/daily_pnl")
def api_daily_pnl():
    try:
        data = dash_svc.get_calendar_data()
        pnl_dict = {d['date']: d['net_pnl'] for d in data}
        return jsonify({"ok": True, "data": pnl_dict})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500

@app.route("/api/weekly")
def api_weekly():
    try:
        data = dash_svc.get_weekly_data()
        return jsonify({"ok": True, "data": data})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500

@app.route("/api/coin_profiles")
def api_coin_profiles():
    try:
        with get_conn() as conn:
            rows = conn.execute("SELECT * FROM coin_profiles ORDER BY win_rate DESC").fetchall()
            data = [dict(r) for r in rows]
        return jsonify({"ok": True, "data": data})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500

@app.route("/api/history")
def api_history():
    try:
        page = int(request.args.get("page", 1))
        limit = 20
        offset = (page - 1) * limit
        with get_conn() as conn:
            rows = conn.execute(
                "SELECT * FROM trades WHERE status NOT IN ('open','tp1_hit','runner') ORDER BY close_time DESC LIMIT ? OFFSET ?",
                (limit, offset)
            ).fetchall()
            trades = [dict(r) for r in rows]
            total = conn.execute(
                "SELECT COUNT(*) FROM trades WHERE status NOT IN ('open','tp1_hit','runner')"
            ).fetchone()[0]
        return jsonify({"ok": True, "data": trades, "total": total, "page": page, "pages": (total // limit) + 1})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500


@app.route("/api/trades")
def api_trades():
    """Sayfalanmis trade gecmisi - dashboard history icin"""
    try:
        page     = int(request.args.get("page", 1))
        per_page = int(request.args.get("per_page", 10))
        offset   = (page - 1) * per_page
        with get_conn() as conn:
            total = conn.execute(
                "SELECT COUNT(*) FROM trades"
            ).fetchone()[0]
            rows = conn.execute(
                "SELECT * FROM trades ORDER BY open_time DESC LIMIT ? OFFSET ?",
                (per_page, offset)
            ).fetchall()
            trades = [dict(r) for r in rows]
        return jsonify({
            "ok": True,
            "data": trades,
            "pagination": {
                "total": total,
                "page": page,
                "per_page": per_page,
                "pages": max(1, (total + per_page - 1) // per_page)
            }
        })
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500


@app.route("/api/pnl_chart")
def api_pnl_chart():
    """Son 30 gunluk kumulatif PnL grafigi icin veri"""
    try:
        with get_conn() as conn:
            rows = conn.execute(
                """
                SELECT DATE(close_time) as day,
                       SUM(COALESCE(realized_pnl,0)) as daily_pnl
                FROM trades
                WHERE close_time IS NOT NULL
                  AND close_time >= datetime('now','-30 days')
                GROUP BY DATE(close_time)
                ORDER BY day ASC
                """
            ).fetchall()
        labels  = [r["day"] for r in rows]
        values  = [round(r["daily_pnl"] or 0, 4) for r in rows]
        # Kumulatif
        cumulative = []
        running = 0.0
        for v in values:
            running += v
            cumulative.append(round(running, 4))
        return jsonify({"ok": True, "labels": labels, "values": values, "cumulative": cumulative})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500


@app.route("/api/scalp_signal_stats")
def api_scalp_signal_stats():
    """AI sinyal istatistikleri - ALLOW/WATCH/VETO dagilimi"""
    try:
        with get_conn() as conn:
            # Toplam trade sayisi ve win/loss
            total_row = conn.execute(
                "SELECT COUNT(*) as total, "
                "SUM(CASE WHEN realized_pnl > 0 THEN 1 ELSE 0 END) as wins, "
                "SUM(CASE WHEN realized_pnl <= 0 THEN 1 ELSE 0 END) as losses "
                "FROM trades WHERE close_time IS NOT NULL"
            ).fetchone()
            # Ghost tracker istatistikleri
            ghost_row = conn.execute(
                "SELECT COUNT(*) as total, "
                "SUM(would_have_won) as ghost_wins "
                "FROM paper_results WHERE status='completed'"
            ).fetchone()
            # Ortalama leverage
            lev_row = conn.execute(
                "SELECT AVG(COALESCE(leverage,10)) as avg_lev FROM trades"
            ).fetchone()
        total  = total_row["total"] or 0
        wins   = total_row["wins"] or 0
        losses = total_row["losses"] or 0
        win_rate = round((wins / total * 100), 1) if total > 0 else 0.0
        ghost_total = ghost_row["total"] or 0
        ghost_wins  = ghost_row["ghost_wins"] or 0
        avg_lev     = round(lev_row["avg_lev"] or 10, 1)
        return jsonify({
            "ok": True,
            "data": {
                "total_trades": total,
                "wins": wins,
                "losses": losses,
                "win_rate": win_rate,
                "ghost_total": ghost_total,
                "ghost_wins": ghost_wins,
                "avg_leverage": avg_lev,
            }
        })
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500


@app.route("/api/reset", methods=["POST"])
def api_reset():
    """Kasa ve trade gecmisini sifirla. AI learning korunur."""
    try:
        from database import reset_paper_data
        data = request.get_json(silent=True) or {}
        initial_balance = float(data.get("initial_balance", 250.0))
        keep_ai = bool(data.get("keep_ai_learning", True))
        ok = reset_paper_data(initial_balance=initial_balance, keep_ai_learning=keep_ai)
        if ok:
            return jsonify({
                "ok": True,
                "message": f"Reset tamamlandi. Bakiye: {initial_balance}$",
                "keep_ai_learning": keep_ai
            })
        return jsonify({"ok": False, "error": "Reset basarisiz"}), 500
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500

@socketio.on('connect')
def handle_connect():
    print('Client connected to Elite Dashboard')

if __name__ == "__main__":
    socketio.run(app, host="0.0.0.0", port=5000, debug=False)
