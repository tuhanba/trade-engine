"""
app.py — AX Dashboard API v5.0 (LIVE-READY)
=============================================
Full dashboard API with open trade breakdown, stats, live PnL,
trade history, calendar, weekly, coin profiles, AX status.
"""
# ── EVENTLET MONKEY PATCH (Must be first) ──
try:
    import eventlet
    eventlet.monkey_patch()
    USE_SOCKETIO = True
except ImportError:
    USE_SOCKETIO = False

import os
from flask import Flask, render_template, jsonify, request
from dotenv import load_dotenv
import logging

load_dotenv()

import database as db
from core.accounting import (
    calculate_runner_unrealized_pnl,
    calculate_open_trade_total_pnl,
)
from core.data_layer import calculate_duration

logger = logging.getLogger(__name__)

app = Flask(__name__, template_folder="templates", static_folder="static")
app.config['SECRET_KEY'] = os.getenv("SECRET_KEY", "ax_secret")

if USE_SOCKETIO:
    from flask_socketio import SocketIO
    socketio = SocketIO(app, cors_allowed_origins="*", async_mode='eventlet')
else:
    socketio = None
    logger.info("[App] eventlet/socketio yok — düz Flask modu.")


@app.route('/')
def index():
    return render_template('index.html')


@app.route('/api/live')
def api_live():
    """Açık trade'lerin detaylı breakdown'ı."""
    try:
        open_trades = db.get_open_trades()
        results = []
        total_unrealized = 0

        for t in open_trades:
            # Fiyat bilgisi (mevcut fiyat yoksa entry kullan)
            current_price = t.get("current_price") or t.get("entry", 0)

            # Süre
            duration_seconds, duration_str = calculate_duration(t.get('open_time'))

            # Runner unrealized PnL
            remaining_qty = t.get("remaining_qty") or t.get("original_qty", 0)
            fee_rate = t.get("fee_rate", 0.0004)
            entry = t.get("entry", 0)
            direction = t.get("direction", "LONG")

            runner_unrealized = calculate_runner_unrealized_pnl(
                direction, entry, current_price, remaining_qty, fee_rate
            )

            realized_pnl = t.get("realized_pnl", 0) or 0
            open_trade_total = calculate_open_trade_total_pnl(realized_pnl, runner_unrealized)
            total_unrealized += open_trade_total

            # TP katkıları
            tp1_pnl = 0
            tp2_pnl = 0
            try:
                partials = db.get_partial_closes(t["id"])
                for p in partials:
                    if p.get("close_type") == "TP1":
                        tp1_pnl = p.get("net_pnl", 0)
                    elif p.get("close_type") == "TP2":
                        tp2_pnl = p.get("net_pnl", 0)
            except Exception:
                pass

            # Active target
            status = t.get("status", "open")
            if status == "open":
                active_target = "TP1"
            elif status == "tp1_hit":
                active_target = "TP2"
            elif status in ("runner", "tp2_hit"):
                active_target = "RUNNER"
            else:
                active_target = status

            # SL distance pct
            sl = t.get("sl", 0) or 0
            sl_dist_pct = abs(entry - sl) / entry * 100 if entry else 0

            results.append({
                "id":                    t["id"],
                "symbol":                t["symbol"],
                "direction":             direction,
                "entry":                 entry,
                "current_price":         current_price,
                "sl":                    sl,
                "tp":                    t.get("tp1"),
                "tp1":                   t.get("tp1"),
                "tp2":                   t.get("tp2"),
                "tp3":                   t.get("tp3"),
                "original_qty":          t.get("original_qty"),
                "remaining_qty":         remaining_qty,
                "qty_tp1":               t.get("qty_tp1"),
                "qty_tp2":               t.get("qty_tp2"),
                "qty_runner":            t.get("qty_runner"),
                "tp1_pnl":               round(tp1_pnl, 4),
                "tp2_pnl":               round(tp2_pnl, 4),
                "realized_pnl":          round(realized_pnl, 4),
                "runner_unrealized_pnl": round(runner_unrealized, 4),
                "unrealized_pnl":        round(open_trade_total, 4),
                "open_trade_total_pnl":  round(open_trade_total, 4),
                "unrealized_pct":        round(open_trade_total / (t.get("margin_used", 1) or 1) * 100, 2),
                "total_fee":             round(t.get("total_fee", 0) or 0, 4),
                "leverage":              t.get("leverage", 10),
                "margin_used":           round(t.get("margin_used", 0) or 0, 2),
                "risk_usd":              round(t.get("risk_usd", 0) or 0, 2),
                "max_loss_after_fee":    round(t.get("max_loss_after_fee", 0) or 0, 2),
                "active_target":         active_target,
                "duration_seconds":      duration_seconds,
                "duration_str":          duration_str,
                "status":                status,
                "sl_distance_pct":       round(sl_dist_pct, 2),
                "current_rr":            round(open_trade_total / (t.get("risk_usd", 1) or 1), 2),
            })

        return jsonify({
            "ok": True,
            "data": {
                "live": results,
                "open_count": len(results),
                "total_unrealized": round(total_unrealized, 4),
            }
        })
    except Exception as e:
        logger.error(f"[API] /api/live hatası: {e}")
        return jsonify({"ok": False, "error": str(e)}), 500


@app.route('/api/stats')
def api_stats():
    """Kapanmış ve açık trade istatistikleri."""
    try:
        stats = db.get_stats()
        balance = db.get_paper_balance()
        open_trades = db.get_open_trades()

        # Açık trade PnL toplamları
        open_realized = sum(t.get("realized_pnl", 0) or 0 for t in open_trades)
        open_unrealized = 0  # Fiyat verisi olmadan hesaplanamaz

        # Günlük PnL
        daily_pnl = 0
        daily_loss_remaining = 0
        try:
            from database import get_conn
            from datetime import datetime, timezone
            with get_conn() as conn:
                today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
                row = conn.execute(
                    "SELECT SUM(net_pnl) FROM trades WHERE DATE(close_time)=? AND status='closed'",
                    (today,)
                ).fetchone()
                daily_pnl = round(row[0] or 0, 4)
                from config import DAILY_MAX_LOSS_PCT
                daily_loss_remaining = round(
                    balance * (DAILY_MAX_LOSS_PCT / 100) - abs(min(0, daily_pnl)), 2
                )
        except Exception:
            pass

        # Detaylı istatistikler
        total_trades = stats.get("total_trades", 0)
        wins = stats.get("wins", 0)
        losses = stats.get("losses", 0)

        # Ek performans metrikleri hesapla
        best_trade = 0
        worst_trade = 0
        avg_pnl = 0
        avg_win = 0
        avg_loss = 0
        avg_dur = 0
        max_dd = 0
        try:
            from database import get_conn
            from datetime import datetime, timezone
            with get_conn() as conn:
                row = conn.execute("""
                    SELECT MAX(net_pnl), MIN(net_pnl),
                           AVG(net_pnl),
                           AVG(CASE WHEN net_pnl > 0 THEN net_pnl END),
                           AVG(CASE WHEN net_pnl <= 0 THEN net_pnl END),
                           AVG(duration_seconds)
                    FROM trades WHERE status='closed' AND is_valid_for_stats=1
                """).fetchone()
                if row:
                    best_trade = round(row[0] or 0, 4)
                    worst_trade = round(row[1] or 0, 4)
                    avg_pnl = round(row[2] or 0, 4)
                    avg_win = round(row[3] or 0, 4)
                    avg_loss = round(row[4] or 0, 4)
                    avg_dur = round((row[5] or 0) / 60, 1)  # saniyeden dakikaya
        except Exception:
            pass

        return jsonify({
            "ok": True,
            "data": {
                "closed_net_pnl":      stats.get("total_pnl", 0),
                "open_realized_pnl":   round(open_realized, 4),
                "open_unrealized_pnl": round(open_unrealized, 4),
                "total_net_pnl":       round(stats.get("total_pnl", 0) + open_realized, 4),
                "total_pnl":           stats.get("total_pnl", 0),
                "total_fees":          stats.get("total_fees", 0),
                "open_trades":         len(open_trades),
                "open_count":          len(open_trades),
                "closed_trades":       total_trades,
                "total":               total_trades + len(open_trades),
                "wins":                wins,
                "losses":              losses,
                "win_rate":            round(stats.get("win_rate", 0) * 100, 1),
                "profit_factor":       stats.get("profit_factor", 0),
                "avg_r":               stats.get("avg_r", 0),
                "avg_rr":              stats.get("avg_r", 0),
                "paper_balance":       round(balance, 2),
                "daily_pnl":           daily_pnl,
                "daily_loss_remaining": daily_loss_remaining,
                "best_trade":          best_trade,
                "worst_trade":         worst_trade,
                "avg_pnl":             avg_pnl,
                "avg_win":             avg_win,
                "avg_loss":            avg_loss,
                "avg_dur":             avg_dur,
                "max_drawdown":        max_dd,
            }
        })
    except Exception as e:
        logger.error(f"[API] /api/stats hatası: {e}")
        return jsonify({"ok": False, "error": str(e)}), 500


@app.route('/api/trades')
def api_trades():
    """Trade geçmişi — pagination ile."""
    try:
        page = int(request.args.get("page", 1))
        per_page = int(request.args.get("per_page", 10))

        from database import get_conn
        with get_conn() as conn:
            total = conn.execute(
                "SELECT COUNT(*) FROM trades WHERE status='closed' AND is_valid_for_stats=1"
            ).fetchone()[0]

            rows = conn.execute("""
                SELECT * FROM trades
                WHERE status='closed' AND is_valid_for_stats=1
                ORDER BY id DESC
                LIMIT ? OFFSET ?
            """, (per_page, (page - 1) * per_page)).fetchall()

        trades = []
        for r in rows:
            d = dict(r)
            # Duration str
            dur_sec = d.get("duration_seconds", 0) or 0
            if dur_sec >= 3600:
                dur_str = f"{dur_sec/3600:.1f}sa"
            elif dur_sec >= 60:
                dur_str = f"{dur_sec//60}dk"
            else:
                dur_str = f"{dur_sec}s"

            d["duration_str"] = dur_str
            d["duration_min"] = round(dur_sec / 60, 1) if dur_sec else 0
            d["exit_price"] = d.get("close_price") or d.get("sl") or d.get("entry")

            # Status mapping
            net = d.get("net_pnl", 0) or 0
            if net > 0:
                d["status"] = "WIN"
            else:
                d["status"] = "LOSS"
            trades.append(d)

        total_pages = max(1, (total + per_page - 1) // per_page)

        return jsonify({
            "ok": True,
            "data": trades,
            "pagination": {
                "page": page,
                "per_page": per_page,
                "total": total,
                "total_pages": total_pages,
            }
        })
    except Exception as e:
        logger.error(f"[API] /api/trades hatası: {e}")
        return jsonify({"ok": False, "error": str(e)}), 500


@app.route('/api/pnl_chart')
def api_pnl_chart():
    """Kümülatif PnL chart verisi."""
    try:
        from database import get_conn
        with get_conn() as conn:
            rows = conn.execute("""
                SELECT symbol, direction, net_pnl
                FROM trades
                WHERE status='closed' AND is_valid_for_stats=1
                ORDER BY id ASC
            """).fetchall()

        cumulative = 0
        points = []
        for r in rows:
            cumulative += (r[2] or 0)
            points.append({
                "symbol": r[0],
                "direction": r[1],
                "pnl": round(r[2] or 0, 4),
                "cumulative": round(cumulative, 4),
            })

        return jsonify({"ok": True, "data": points})
    except Exception as e:
        logger.error(f"[API] /api/pnl_chart hatası: {e}")
        return jsonify({"ok": False, "error": str(e)}), 500


@app.route('/api/daily_pnl')
def api_daily_pnl():
    """Günlük PnL calendar verisi."""
    try:
        from database import get_conn
        with get_conn() as conn:
            rows = conn.execute("""
                SELECT DATE(close_time) as cdate, SUM(net_pnl) as total
                FROM trades
                WHERE status='closed' AND close_time IS NOT NULL AND is_valid_for_stats=1
                GROUP BY cdate
                ORDER BY cdate
            """).fetchall()

        data = {}
        for r in rows:
            if r[0]:
                data[r[0]] = round(r[1] or 0, 4)

        return jsonify({"ok": True, "data": data})
    except Exception as e:
        logger.error(f"[API] /api/daily_pnl hatası: {e}")
        return jsonify({"ok": False, "error": str(e)}), 500


@app.route('/api/weekly')
def api_weekly():
    """Haftalık özet verisi."""
    try:
        from dashboard_service import get_weekly_data
        weeks = get_weekly_data(weeks=8)
        return jsonify({"ok": True, "data": weeks})
    except Exception as e:
        logger.error(f"[API] /api/weekly hatası: {e}")
        return jsonify({"ok": False, "data": []})


@app.route('/api/coin_profiles')
def api_coin_profiles():
    """Coin öğrenme profilleri."""
    try:
        from database import get_conn
        with get_conn() as conn:
            rows = conn.execute("""
                SELECT cp.symbol, cp.win_rate, cp.avg_r, cp.profit_factor,
                       cp.danger_score, cp.sample_size,
                       (SELECT COUNT(*) FROM trades t WHERE t.symbol=cp.symbol
                        AND t.status='closed' AND t.is_valid_for_stats=1) as total,
                       (SELECT SUM(net_pnl) FROM trades t WHERE t.symbol=cp.symbol
                        AND t.status='closed' AND t.is_valid_for_stats=1) as total_pnl
                FROM coin_profiles cp
                WHERE cp.sample_size > 0
                ORDER BY cp.sample_size DESC
                LIMIT 50
            """).fetchall()

        profiles = []
        for r in rows:
            profiles.append({
                "symbol": r[0],
                "win_rate": round((r[1] or 0) * 100, 1),
                "avg_r": round(r[2] or 0, 2),
                "profit_factor": round(r[3] or 0, 2),
                "danger_score": round(r[4] or 0, 2),
                "sample_size": r[5] or 0,
                "total": r[6] or 0,
                "total_pnl": round(r[7] or 0, 4),
            })

        return jsonify({"ok": True, "data": profiles})
    except Exception as e:
        logger.error(f"[API] /api/coin_profiles hatası: {e}")
        return jsonify({"ok": True, "data": []})


@app.route('/api/ax_status')
def api_ax_status():
    """AX sistem durumu."""
    try:
        from dashboard_service import get_ax_status
        data = get_ax_status()
        return jsonify({"ok": True, "data": data})
    except Exception as e:
        logger.error(f"[API] /api/ax_status hatası: {e}")
        return jsonify({"ok": False, "error": str(e)}), 500


@app.route('/api/scalp_signal_stats')
def api_scalp_signal_stats():
    """Bugünkü sinyal istatistikleri — kalite kırılımı."""
    try:
        from database import get_conn
        from datetime import datetime, timezone
        with get_conn() as conn:
            today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
            rows = conn.execute("""
                SELECT setup_quality, COUNT(*) as cnt
                FROM signal_candidates
                WHERE DATE(created_at) = ?
                GROUP BY setup_quality
            """, (today,)).fetchall()

        data = {"S": 0, "A+": 0, "A": 0, "B": 0, "C": 0, "total": 0}
        for r in rows:
            quality = r[0] or "C"
            cnt = r[1] or 0
            data["total"] += cnt
            if quality in data:
                data[quality] = cnt

        return jsonify({"ok": True, "data": data})
    except Exception as e:
        logger.error(f"[API] /api/scalp_signal_stats hatası: {e}")
        return jsonify({"ok": True, "data": {"S": 0, "A+": 0, "A": 0, "B": 0, "total": 0}})


@app.route('/api/health')
def api_health():
    """Sistem sağlık kontrolü."""
    try:
        import time
        from config import DB_PATH, EXECUTION_MODE, LIVE_TRADING_ENABLED
        db_size_mb = 0
        if os.path.exists(DB_PATH):
            db_size_mb = round(os.path.getsize(DB_PATH) / (1024 * 1024), 2)

        return jsonify({
            "ok": True,
            "data": {
                "status": "healthy",
                "version": "5.0",
                "execution_mode": EXECUTION_MODE,
                "live_trading": LIVE_TRADING_ENABLED,
                "db_size_mb": db_size_mb,
                "db_path": DB_PATH,
                "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            }
        })
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500


@app.route('/api/learning_metrics')
def api_learning_metrics():
    """Ghost learning metrikleri."""
    try:
        from dashboard_service import get_learning_metrics
        data = get_learning_metrics(days=14)
        return jsonify({"ok": True, "data": data})
    except Exception as e:
        logger.error(f"[API] /api/learning_metrics hatası: {e}")
        return jsonify({"ok": True, "data": {}})

if __name__ == '__main__':
    db.init_db()

    # Dashboard service başlat
    try:
        import dashboard_service
        dashboard_service.start()
    except Exception as e:
        logger.warning(f"[App] Dashboard service başlatılamadı: {e}")

    port = int(os.getenv("DASHBOARD_PORT", "5000"))
    if USE_SOCKETIO:
        socketio.run(app, host='0.0.0.0', port=port, debug=False)
    else:
        app.run(host='0.0.0.0', port=port, debug=False)
