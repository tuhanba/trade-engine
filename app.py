"""
app.py — AX Dashboard v5.1 (PAPER DASHBOARD / LIVE-BLOCKED)
======================================================
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
            # Safe numeric getters
            def safe_num(k, default=0.0):
                v = t.get(k)
                return float(v) if v is not None else default

            current_price = safe_num("current_price")
            data_quality = "live"
            if current_price <= 0:
                current_price = safe_num("entry")
                data_quality = "fallback"
                
            mark_price = safe_num("mark_price", current_price)
            entry = safe_num("entry")
            sl = safe_num("sl")
            tp1 = safe_num("tp1")
            tp2 = safe_num("tp2")
            tp3 = safe_num("tp3")
            direction = str(t.get("direction", "LONG")).upper()
            
            # Distance hesaplamaları
            def calc_dist(target_price, is_sl=False):
                if not current_price or not target_price: return 0.0
                if direction == "LONG":
                    dist = (current_price - target_price) if is_sl else (target_price - current_price)
                else:
                    dist = (target_price - current_price) if is_sl else (current_price - target_price)
                return (dist / current_price) * 100.0

            sl_dist = calc_dist(sl, is_sl=True)
            tp1_dist = calc_dist(tp1)
            tp2_dist = calc_dist(tp2)
            tp3_dist = calc_dist(tp3)

            # Süre
            duration_seconds, duration_str = calculate_duration(t.get('open_time'))

            # PnL Hesapları
            remaining_qty = safe_num("remaining_qty", safe_num("original_qty"))
            fee_rate = safe_num("fee_rate", 0.0004)
            realized_pnl = safe_num("realized_pnl")
            
            runner_unrealized = calculate_runner_unrealized_pnl(
                direction, entry, current_price, remaining_qty, fee_rate
            )
            net_pnl = calculate_open_trade_total_pnl(realized_pnl, runner_unrealized)
            total_unrealized += net_pnl

            # Lifecycle & Targets
            status = str(t.get("status", "open")).upper()
            lifecycle_state = status
            
            if status == "OPEN":
                active_target = "TP1"
                next_target = "TP1"
            elif status == "TP1_HIT":
                active_target = "TP2"
                next_target = "TP2"
            elif status in ("RUNNER", "TP2_HIT"):
                active_target = "RUNNER"
                next_target = "RUNNER"
            else:
                active_target = "-"
                next_target = "-"

            # Margin & Risk
            margin = safe_num("margin_used", 1.0)
            if margin <= 0: margin = 1.0
            risk_usd = safe_num("risk_usd", 1.0)
            if risk_usd <= 0: risk_usd = 1.0
            
            net_pnl_pct = (net_pnl / margin) * 100.0
            current_R = net_pnl / risk_usd

            results.append({
                "id":                    str(t.get("id", "")),
                "symbol":                str(t.get("symbol", "-")),
                "side":                  direction,
                "status":                status,
                "lifecycle_state":       lifecycle_state,
                "entry":                 entry,
                "current_price":         current_price,
                "mark_price":            mark_price,
                "sl":                    sl,
                "tp1":                   tp1,
                "tp2":                   tp2,
                "tp3":                   tp3,
                "active_target":         active_target,
                "next_target":           next_target,
                "qty":                   safe_num("original_qty"),
                "remaining_qty":         remaining_qty,
                "margin":                margin,
                "leverage":              safe_num("leverage", 10.0),
                "risk_usd":              risk_usd,
                "risk_pct":              safe_num("risk_pct", 1.0),
                "realized_pnl":          round(realized_pnl, 4),
                "unrealized_pnl":        round(net_pnl, 4),
                "runner_pnl":            round(runner_unrealized, 4),
                "net_pnl":               round(net_pnl, 4),
                "net_pnl_pct":           round(net_pnl_pct, 2),
                "current_R":             round(current_R, 2),
                "sl_distance_pct":       round(sl_dist, 2),
                "tp1_distance_pct":      round(tp1_dist, 2),
                "tp2_distance_pct":      round(tp2_dist, 2),
                "tp3_distance_pct":      round(tp3_dist, 2),
                "duration_sec":          duration_seconds,
                "duration_str":          duration_str,
                "opened_at":             str(t.get("open_time", "-")),
                "last_update":           str(t.get("last_update", "-")),
                "fee_paid":              safe_num("total_fee"),
                "fee_estimate":          0.0,
                "source":                str(t.get("source", "bot")),
                "data_quality":          data_quality
            })

        return jsonify({
            "ok": True,
            "data": {
                "live": results,
                "open_count": len(results),
                "total_unrealized": round(total_unrealized, 4)
            }
        })
    except Exception as e:
        logger.error(f"[API] /api/live crash: {e}", exc_info=True)
        return jsonify({"ok": False, "error": str(e), "data": {"live": [], "open_count": 0, "total_unrealized": 0}}), 500


@app.route('/api/stats')
def api_stats():
    """Kapanmış ve açık trade istatistikleri."""
    try:
        stats = db.get_stats()
        balance = db.get_paper_balance()
        open_trades = db.get_open_trades()

        def safe_num(val, default=0.0):
            return float(val) if val is not None else default

        open_realized = sum(safe_num(t.get("realized_pnl")) for t in open_trades)
        
        # Günlük PnL
        daily_pnl = 0.0
        daily_loss_remaining = 0.0
        try:
            from database import get_conn
            from datetime import datetime, timezone
            with get_conn() as conn:
                today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
                row = conn.execute(
                    "SELECT SUM(net_pnl) FROM trades WHERE DATE(close_time)=? AND status='closed'",
                    (today,)
                ).fetchone()
                daily_pnl = round(safe_num(row[0] if row else 0), 4)
                from config import DAILY_MAX_LOSS_PCT
                daily_loss_remaining = round(
                    balance * (DAILY_MAX_LOSS_PCT / 100.0) - abs(min(0, daily_pnl)), 2
                )
        except Exception:
            pass

        total_trades = int(stats.get("total_trades", 0))
        wins = int(stats.get("wins", 0))
        losses = int(stats.get("losses", 0))

        best_trade = 0.0
        worst_trade = 0.0
        avg_pnl = 0.0
        avg_win = 0.0
        avg_loss = 0.0
        avg_dur = 0.0
        try:
            from database import get_conn
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
                    best_trade = round(safe_num(row[0]), 4)
                    worst_trade = round(safe_num(row[1]), 4)
                    avg_pnl = round(safe_num(row[2]), 4)
                    avg_win = round(safe_num(row[3]), 4)
                    avg_loss = round(safe_num(row[4]), 4)
                    avg_dur = round(safe_num(row[5]) / 60.0, 1)
        except Exception:
            pass

        open_unrealized = sum(safe_num(t.get("unrealized_pnl")) for t in open_trades)
        closed_net_pnl = safe_num(stats.get("total_pnl"))

        return jsonify({
            "ok": True,
            "data": {
                "closed_net_pnl":      round(closed_net_pnl, 4),
                "open_realized_pnl":   round(open_realized, 4),
                "open_unrealized_pnl": round(open_unrealized, 4),
                "total_net_pnl":       round(closed_net_pnl + open_realized + open_unrealized, 4),
                "total_pnl":           round(closed_net_pnl, 4),
                "total_fees":          round(safe_num(stats.get("total_fees")), 4),
                "open_trades":         len(open_trades),
                "open_count":          len(open_trades),
                "closed_trades":       total_trades,
                "total":               total_trades + len(open_trades),
                "wins":                wins,
                "losses":              losses,
                "win_rate":            round(safe_num(stats.get("win_rate")) * 100, 1),
                "profit_factor":       round(safe_num(stats.get("profit_factor")), 2),
                "avg_r":               round(safe_num(stats.get("avg_r")), 2),
                "avg_rr":              round(safe_num(stats.get("avg_r")), 2),
                "paper_balance":       round(safe_num(balance), 2),
                "daily_pnl":           daily_pnl,
                "daily_loss_remaining": daily_loss_remaining,
                "best_trade":          best_trade,
                "worst_trade":         worst_trade,
                "avg_pnl":             avg_pnl,
                "avg_win":             avg_win,
                "avg_loss":            avg_loss,
                "avg_dur":             avg_dur,
                "max_drawdown":        0.0,
            }
        })
    except Exception as e:
        logger.error(f"[API] /api/stats crash: {e}", exc_info=True)
        return jsonify({"ok": False, "error": str(e), "data": {}}), 500


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
            dur_sec = float(d.get("duration_seconds") or 0.0)
            if dur_sec >= 3600:
                dur_str = f"{dur_sec/3600:.1f}sa"
            elif dur_sec >= 60:
                dur_str = f"{dur_sec//60:.0f}dk"
            else:
                dur_str = f"{dur_sec:.0f}s"

            d["duration_str"] = dur_str
            d["duration_min"] = round(dur_sec / 60.0, 1)
            d["exit_price"] = d.get("close_price") or d.get("sl") or d.get("entry")
            
            net = float(d.get("net_pnl") or 0.0)
            d["status"] = "WIN" if net > 0 else "LOSS"
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
        logger.error(f"[API] /api/trades crash: {e}", exc_info=True)
        return jsonify({"ok": False, "error": str(e), "data": [], "pagination": {}}), 500


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

        cumulative = 0.0
        points = []
        for r in rows:
            val = float(r[2] if r[2] is not None else 0.0)
            cumulative += val
            points.append({
                "symbol": str(r[0] or ""),
                "direction": str(r[1] or ""),
                "pnl": round(val, 4),
                "cumulative": round(cumulative, 4),
            })

        return jsonify({"ok": True, "data": points})
    except Exception as e:
        logger.error(f"[API] /api/pnl_chart crash: {e}", exc_info=True)
        return jsonify({"ok": False, "error": str(e), "data": []}), 500


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
                data[r[0]] = round(float(r[1] if r[1] is not None else 0.0), 4)

        return jsonify({"ok": True, "data": data})
    except Exception as e:
        logger.error(f"[API] /api/daily_pnl crash: {e}", exc_info=True)
        return jsonify({"ok": False, "error": str(e), "data": {}}), 500


@app.route('/api/weekly')
def api_weekly():
    """Haftalık özet verisi."""
    try:
        from dashboard_service import get_weekly_data
        weeks = get_weekly_data(weeks=8)
        return jsonify({"ok": True, "data": weeks})
    except Exception as e:
        logger.error(f"[API] /api/weekly crash: {e}", exc_info=True)
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
                "symbol": str(r[0] or ""),
                "win_rate": round(float(r[1] or 0.0) * 100.0, 1),
                "avg_r": round(float(r[2] or 0.0), 2),
                "profit_factor": round(float(r[3] or 0.0), 2),
                "danger_score": round(float(r[4] or 0.0), 2),
                "sample_size": int(r[5] or 0),
                "total": int(r[6] or 0),
                "total_pnl": round(float(r[7] or 0.0), 4),
            })

        return jsonify({"ok": True, "data": profiles})
    except Exception as e:
        logger.error(f"[API] /api/coin_profiles crash: {e}", exc_info=True)
        return jsonify({"ok": True, "data": []})


@app.route('/api/ax_status')
def api_ax_status():
    """AX sistem durumu."""
    try:
        from dashboard_service import get_ax_status
        data = get_ax_status()
        return jsonify({"ok": True, "data": data})
    except Exception as e:
        logger.error(f"[API] /api/ax_status crash: {e}", exc_info=True)
        return jsonify({"ok": False, "error": str(e), "data": {}}), 500


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
        logger.error(f"[API] /api/scalp_signal_stats crash: {e}", exc_info=True)
        return jsonify({"ok": True, "data": {"S": 0, "A+": 0, "A": 0, "B": 0, "total": 0}})


@app.route('/api/health')
def api_health():
    """Detaylı sistem sağlık kontrolü."""
    try:
        import time
        from config import DB_PATH, EXECUTION_MODE, LIVE_TRADING_ENABLED, DRY_RUN, TELEGRAM_BOT_TOKEN
        
        db_size_mb = 0.0
        db_ok = False
        if os.path.exists(DB_PATH):
            db_size_mb = round(os.path.getsize(DB_PATH) / (1024 * 1024), 2)
            try:
                from database import get_conn
                with get_conn() as conn:
                    conn.execute("SELECT 1").fetchone()
                db_ok = True
            except Exception:
                pass
                
        # Binance Check
        binance_ok = False
        try:
            import requests
            r = requests.get('https://fapi.binance.com/fapi/v1/ping', timeout=3)
            binance_ok = (r.status_code == 200)
        except Exception:
            pass

        # Uptime Check
        uptime = "0s"
        try:
            with open("/proc/uptime", "r") as f:
                uptime_sec = float(f.readline().split()[0])
                if uptime_sec > 86400: uptime = f"{uptime_sec/86400:.1f}d"
                elif uptime_sec > 3600: uptime = f"{uptime_sec/3600:.1f}h"
                else: uptime = f"{uptime_sec/60:.1f}m"
        except Exception:
            pass

        active_trade_count = len(db.get_open_trades())

        # System State Fetch
        try:
            from database import get_system_state
            last_scan_str = get_system_state("last_scan_time", "-")
            last_paper_str = get_system_state("last_paper_result_process_at", "-")
            last_monitor_str = get_system_state("last_trade_monitor_at", "-")
            bot_hb_str = get_system_state("bot_heartbeat_at", "")
            
            bot_running = False
            if bot_hb_str:
                from datetime import datetime, timezone
                try:
                    hb_dt = datetime.fromisoformat(bot_hb_str.replace("Z", "+00:00"))
                    diff = (datetime.now(timezone.utc) - hb_dt).total_seconds()
                    # 2 scan_interval süresi (e.g. 60s) limit sayılabilir
                    if diff < 120:
                        bot_running = True
                except:
                    pass
        except Exception:
            last_scan_str, last_paper_str, last_monitor_str, bot_running = "-", "-", "-", False

        return jsonify({
            "ok": True,
            "data": {
                "bot_running": bot_running,
                "dashboard_running": True,
                "db_ok": db_ok,
                "binance_public_ok": binance_ok,
                "telegram_config_ok": bool(TELEGRAM_BOT_TOKEN),
                "execution_mode": str(EXECUTION_MODE),
                "live_trading_enabled": bool(LIVE_TRADING_ENABLED),
                "dry_run": bool(DRY_RUN),
                "paper_safety_status": "SECURE" if EXECUTION_MODE == "paper" and DRY_RUN and not LIVE_TRADING_ENABLED else "RISK",
                "last_scan_time": last_scan_str,
                "last_trade_monitor_at": last_monitor_str,
                "last_paper_result_process_at": last_paper_str,
                "active_trade_count": active_trade_count,
                "last_error": get_system_state("last_error", "-"),
                "uptime": uptime,
                "timestamp": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            }
        })
    except Exception as e:
        logger.error(f"[API] /api/health crash: {e}", exc_info=True)
        return jsonify({"ok": False, "error": str(e), "data": {}}), 500


@app.route('/api/learning_metrics')
def api_learning_metrics():
    """Ghost learning metrikleri."""
    try:
        from dashboard_service import get_learning_metrics
        data = get_learning_metrics(days=14)
        return jsonify({"ok": True, "data": data})
    except Exception as e:
        logger.error(f"[API] /api/learning_metrics crash: {e}", exc_info=True)
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
