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
from config import DB_PATH
from database import (
    init_db, get_trades, get_stats, get_current_params,
    get_open_trades, get_paper_balance, get_conn,
    get_pipeline_stats, get_pipeline_totals, get_veto_stats,
    get_daily_summaries,
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
            tbl = conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name='coin_params'"
            ).fetchone()
            if not tbl:
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
        for r in rows:
            dec = r["decision"] or "PENDING"
            if dec in stats:
                stats[dec] = r["cnt"]
            stats["total"] += r["cnt"]
        return jsonify({"ok": True, "data": stats})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500


# ── /api/status ───────────────────────────────────────────────────────────────
@app.route("/api/status")
def api_status():
    """Bot genel durumu — dashboard üst kartlar için."""
    try:
        data = dash_svc.get_ax_status()
        bal  = get_paper_balance()
        st   = get_stats(hours=24)
        return jsonify({"ok": True, "data": {
            **data,
            "balance":       round(bal, 4),
            "today_pnl":     data.get("today_pnl", 0),
            "open_trades":   data.get("open_trades", 0),
            "today_signals": data.get("today_signals", 0),
            "win_rate":      st.get("win_rate", 0),
            "total_trades":  st.get("total", 0),
        }})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e), "data": {}}), 500


# ── /api/summary ──────────────────────────────────────────────────────────────
@app.route("/api/summary")
def api_summary():
    """Kümülatif PnL + performans özeti."""
    try:
        st48 = get_stats(hours=48)
        st7d = get_stats(hours=168)
        bal  = get_paper_balance()
        with get_conn() as conn:
            row = conn.execute(
                "SELECT initial_balance FROM paper_account WHERE id=1"
            ).fetchone()
            initial = float(row["initial_balance"]) if row else 250.0
            closed = conn.execute(
                "SELECT SUM(net_pnl) as total FROM trades WHERE status='closed'"
            ).fetchone()
            cum_pnl = round(float(closed["total"] or 0), 4)
        return jsonify({"ok": True, "data": {
            "cumulative_pnl":  cum_pnl,
            "balance":         round(bal, 4),
            "initial_balance": initial,
            "total_return_pct": round((bal - initial) / initial * 100, 2) if initial else 0,
            "stats_48h": st48,
            "stats_7d":  st7d,
        }})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e), "data": {}}), 500


# ── /api/trades/open ─────────────────────────────────────────────────────────
@app.route("/api/trades/open")
def api_trades_open():
    """Açık pozisyonlar."""
    try:
        open_trades = get_open_trades()
        if not open_trades:
            return jsonify({"ok": True, "data": [], "status": "Henüz açık trade yok"})
        result = []
        for t in open_trades:
            dur_str, dur_min = _fmt_duration(t.get("open_time"))
            result.append({**t, "duration_str": dur_str, "duration_min": dur_min})
        return jsonify({"ok": True, "data": result})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e), "data": []}), 500


# ── /api/trades/recent ───────────────────────────────────────────────────────
@app.route("/api/trades/recent")
def api_trades_recent():
    """Son N kapanmış trade."""
    try:
        limit  = int(request.args.get("limit", 20))
        trades = get_trades(limit=limit, status="closed")
        if not trades:
            return jsonify({"ok": True, "data": [], "status": "Henüz kapanmış trade yok"})
        result = []
        for t in trades:
            dur_str, dur_min = _fmt_duration(t.get("open_time"), t.get("close_time"))
            result.append({**t, "duration_str": dur_str, "duration_min": dur_min})
        return jsonify({"ok": True, "data": result})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e), "data": []}), 500


# ── /api/signals/recent ───────────────────────────────────────────────────────
@app.route("/api/signals/recent")
def api_signals_recent():
    """Son N signal_candidate."""
    try:
        limit = int(request.args.get("limit", 20))
        with get_conn() as conn:
            rows = conn.execute(
                "SELECT * FROM signal_candidates ORDER BY id DESC LIMIT ?", (limit,)
            ).fetchall()
        if not rows:
            return jsonify({"ok": True, "data": [],
                            "status": "Henüz sinyal üretilmedi — bot tarıyor veya filtreler çok sıkı"})
        return jsonify({"ok": True, "data": [dict(r) for r in rows]})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e), "data": []}), 500


# ── /api/pipeline-stats ──────────────────────────────────────────────────────
@app.route("/api/pipeline-stats")
def api_pipeline_stats():
    """Son 24 saatlik pipeline istatistikleri."""
    try:
        hours  = int(request.args.get("hours", 24))
        totals = get_pipeline_totals(hours=hours)
        recent = get_pipeline_stats(limit=10)

        # Durum mesajı üret
        status_msg = "—"
        candidates = totals.get("candidates") or 0
        allow      = totals.get("allow") or 0
        veto       = totals.get("veto") or 0
        trades     = totals.get("trades_opened") or 0
        scanned    = totals.get("scanned") or 0

        if scanned == 0:
            status_msg = "Bot henüz scan yapmadı"
        elif candidates == 0:
            status_msg = "Bot scan ediyor ama signal üretmiyor — filtreler çok sıkı olabilir"
        elif allow == 0 and veto > 0:
            status_msg = "Candidate var ama ALLOW yok — AX hepsini veto ediyor"
        elif allow > 0 and trades == 0:
            status_msg = "ALLOW sinyali var ama trade açılmadı — execution eşiği veya MAX_OPEN_TRADES"
        elif trades > 0:
            status_msg = f"{trades} paper trade açıldı"
        else:
            status_msg = "Sistem çalışıyor"

        return jsonify({"ok": True, "data": {
            "totals":     totals,
            "recent":     recent,
            "status_msg": status_msg,
            "hours":      hours,
        }})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e), "data": {}}), 500


# ── /api/ai/params ────────────────────────────────────────────────────────────
@app.route("/api/ai/params")
def api_ai_params():
    """AX'in mevcut parametreleri."""
    try:
        p = get_current_params()
        if not p:
            p = {
                "sl_atr_mult": 1.3, "tp_atr_mult": 2.0,
                "rsi5_min": 35, "rsi5_max": 75,
                "rsi1_min": 32, "rsi1_max": 75,
                "vol_ratio_min": 1.0, "min_volume_m": 3.0,
                "min_change_pct": 0.5, "risk_pct": 1.0,
            }
        with get_conn() as conn:
            log = conn.execute(
                "SELECT reason, created_at FROM params ORDER BY id DESC LIMIT 1"
            ).fetchone()
        last_reason = log["reason"] if log else "Henüz AI Brain çalışmadı"
        last_update = log["created_at"] if log else None
        return jsonify({"ok": True, "data": {
            "params":      p,
            "last_reason": last_reason,
            "last_update": last_update,
        }})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e), "data": {}}), 500


# ── /api/performance ─────────────────────────────────────────────────────────
@app.route("/api/performance")
def api_performance():
    """Performans metrikleri."""
    try:
        st = get_stats(hours=720)  # 30 gün
        if st.get("total", 0) == 0:
            return jsonify({"ok": True, "data": None,
                            "status": "Henüz kapanmış trade yok — performans verisi oluşacak"})
        return jsonify({"ok": True, "data": st})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e), "data": None}), 500


# ── /api/session-performance ─────────────────────────────────────────────────
@app.route("/api/session-performance")
def api_session_performance():
    """Seans bazlı performans."""
    try:
        with get_conn() as conn:
            # Önce candidate bazlı veri (trade olmasa bile)
            cand_rows = conn.execute(
                """SELECT session,
                          COUNT(*) as total_candidates,
                          SUM(CASE WHEN decision='ALLOW' THEN 1 ELSE 0 END) as allow_count,
                          SUM(CASE WHEN decision='VETO'  THEN 1 ELSE 0 END) as veto_count,
                          SUM(CASE WHEN decision='WATCH' THEN 1 ELSE 0 END) as watch_count
                   FROM signal_candidates
                   WHERE session IS NOT NULL
                   GROUP BY session"""
            ).fetchall()

            trade_rows = conn.execute(
                """SELECT
                     c.session,
                     COUNT(t.id)  as trade_count,
                     SUM(CASE WHEN t.net_pnl > 0 THEN 1 ELSE 0 END) as win_count,
                     SUM(t.net_pnl) as net_pnl
                   FROM trades t
                   JOIN signal_candidates c ON t.linked_candidate_id = c.id
                   WHERE t.status='closed' AND c.session IS NOT NULL
                   GROUP BY c.session"""
            ).fetchall()

        sessions = {}
        for r in cand_rows:
            s = r["session"] or "UNKNOWN"
            sessions[s] = {
                "session":          s,
                "total_candidates": r["total_candidates"],
                "allow_count":      r["allow_count"],
                "veto_count":       r["veto_count"],
                "watch_count":      r["watch_count"],
                "trade_count": 0, "win_count": 0,
                "win_rate": 0, "net_pnl": 0,
            }
        for r in trade_rows:
            s = r["session"] or "UNKNOWN"
            if s not in sessions:
                sessions[s] = {"session": s, "total_candidates": 0,
                                "allow_count": 0, "veto_count": 0, "watch_count": 0}
            tc = r["trade_count"] or 0
            wc = r["win_count"] or 0
            sessions[s].update({
                "trade_count": tc,
                "win_count":   wc,
                "win_rate":    round(wc / tc * 100, 1) if tc else 0,
                "net_pnl":     round(r["net_pnl"] or 0, 4),
            })

        if not sessions:
            return jsonify({"ok": True, "data": [],
                            "status": "Henüz seans verisi yok — bot taramaya devam ediyor"})
        return jsonify({"ok": True, "data": list(sessions.values())})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e), "data": []}), 500


# ── /api/calendar-pnl ────────────────────────────────────────────────────────
@app.route("/api/calendar-pnl")
def api_calendar_pnl():
    """Aylık PnL takvim verisi."""
    try:
        days = int(request.args.get("days", 35))
        data = dash_svc.get_calendar_data(days)
        if not data:
            return jsonify({"ok": True, "data": [],
                            "status": "Henüz trade yok — takvim verisi oluşacak"})
        return jsonify({"ok": True, "data": data})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e), "data": []}), 500


# ── /api/weekly-pnl ──────────────────────────────────────────────────────────
@app.route("/api/weekly-pnl")
def api_weekly_pnl():
    """Haftalık PnL özeti."""
    try:
        weeks = int(request.args.get("weeks", 8))
        data  = dash_svc.get_weekly_data(weeks)
        if not data:
            return jsonify({"ok": True, "data": [],
                            "status": "Henüz haftalık veri yok"})
        return jsonify({"ok": True, "data": data})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e), "data": []}), 500


# ── /api/equity-curve ────────────────────────────────────────────────────────
@app.route("/api/equity-curve")
def api_equity_curve():
    """Equity curve — kümülatif PnL noktaları."""
    try:
        trades = get_trades(limit=500, status="closed")
        if not trades:
            return jsonify({"ok": True, "data": [],
                            "status": "Henüz trade yok — equity curve oluşacak"})
        cumulative = 0
        points = []
        for t in reversed(trades):
            pnl = t.get("net_pnl") or 0
            cumulative += pnl
            points.append({
                "x":          t.get("close_time", ""),
                "y":          round(cumulative, 4),
                "pnl":        round(pnl, 4),
                "symbol":     t.get("symbol", ""),
                "direction":  t.get("direction", ""),
            })
        return jsonify({"ok": True, "data": points})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e), "data": []}), 500


# ── /api/winrate-rr ───────────────────────────────────────────────────────────
@app.route("/api/winrate-rr")
def api_winrate_rr():
    """Winrate / RR / Expectancy grafiği için veri."""
    try:
        st = get_stats(hours=720)
        from config import EXEC_MIN_RR, EXEC_SCORE_MIN
        has_data = st.get("total", 0) > 0
        return jsonify({"ok": True, "data": {
            "current": {
                "win_rate":   round(st.get("win_rate", 0) * 100, 1) if has_data else None,
                "avg_rr":     round(st.get("avg_r", 0), 3) if has_data else None,
                "expectancy": round(
                    st.get("win_rate", 0) * st.get("avg_r", 0)
                    - (1 - st.get("win_rate", 0)), 3
                ) if has_data else None,
                "profit_factor": round(st.get("profit_factor", 0), 2) if has_data else None,
            },
            "targets": {
                "win_rate_min": 45,
                "win_rate_max": 55,
                "rr_target":    EXEC_MIN_RR,
                "break_even_wr": round(100 / (1 + EXEC_MIN_RR), 1),
            },
            "has_data": has_data,
            "status": "Henüz trade yok — hedef bölge gösteriliyor" if not has_data else "OK",
        }})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e), "data": {}}), 500


# ── /api/coin-performance ────────────────────────────────────────────────────
@app.route("/api/coin-performance")
def api_coin_performance():
    """Coin bazlı performans — trade + candidate birleşik."""
    try:
        with get_conn() as conn:
            # Candidate bazlı (trade olmasa bile veri göster)
            cand = conn.execute(
                """SELECT symbol,
                          COUNT(*) as scanned_count,
                          COUNT(*) as candidate_count,
                          SUM(CASE WHEN decision='ALLOW' THEN 1 ELSE 0 END) as allow_count,
                          SUM(CASE WHEN decision='VETO'  THEN 1 ELSE 0 END) as veto_count
                   FROM signal_candidates
                   GROUP BY symbol
                   ORDER BY candidate_count DESC"""
            ).fetchall()

            # Trade bazlı
            trade = conn.execute(
                """SELECT symbol,
                          COUNT(*) as trade_count,
                          SUM(CASE WHEN net_pnl > 0 THEN 1 ELSE 0 END) as win_count,
                          SUM(net_pnl) as net_pnl,
                          AVG(r_multiple) as avg_r
                   FROM trades
                   WHERE status='closed'
                   GROUP BY symbol"""
            ).fetchall()

            # Coin profili
            prof = conn.execute(
                "SELECT symbol, danger_score, avg_mfe, volatility_profile FROM coin_profile"
            ).fetchall()

        by_sym: dict = {}
        for r in cand:
            s = r["symbol"]
            by_sym[s] = {
                "symbol":          s,
                "candidate_count": r["candidate_count"],
                "allow_count":     r["allow_count"],
                "veto_count":      r["veto_count"],
                "trade_count": 0, "win_count": 0,
                "net_pnl": 0, "win_rate": 0, "avg_r": 0,
                "danger_score": 0, "avg_mfe": 0,
            }
        for r in trade:
            s = r["symbol"]
            if s not in by_sym:
                by_sym[s] = {
                    "symbol": s, "candidate_count": 0,
                    "allow_count": 0, "veto_count": 0,
                }
            tc = r["trade_count"] or 0
            wc = r["win_count"] or 0
            by_sym[s].update({
                "trade_count": tc,
                "win_count":   wc,
                "net_pnl":     round(r["net_pnl"] or 0, 4),
                "win_rate":    round(wc / tc * 100, 1) if tc else 0,
                "avg_r":       round(r["avg_r"] or 0, 3),
            })
        for r in prof:
            s = r["symbol"]
            if s in by_sym:
                by_sym[s]["danger_score"] = r["danger_score"]
                by_sym[s]["avg_mfe"]      = r["avg_mfe"]

        data = sorted(by_sym.values(),
                      key=lambda x: x.get("candidate_count", 0), reverse=True)
        if not data:
            return jsonify({"ok": True, "data": [],
                            "status": "Henüz coin bazlı veri yok"})
        return jsonify({"ok": True, "data": data})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e), "data": []}), 500


# ── /api/veto-stats ───────────────────────────────────────────────────────────
@app.route("/api/veto-stats")
def api_veto_stats():
    """Veto istatistikleri ve öğrenme verisi."""
    try:
        days = int(request.args.get("days", 7))
        data = get_veto_stats(days=days)

        total = data.get("total", 0)
        allow = data.get("ALLOW", 0)
        veto  = data.get("VETO", 0)
        watch = data.get("WATCH", 0)

        # AX çok sert mi / çok gevşek mi?
        diagnosis = "Veri yok"
        if total > 0:
            veto_rate = veto / total
            if veto_rate > 0.85:
                diagnosis = "AX çok sert veto ediyor — filtreler gevşetilebilir"
            elif veto_rate > 0.70:
                diagnosis = "Veto oranı yüksek — normal ama dikkat"
            elif veto_rate < 0.30:
                diagnosis = "Veto oranı düşük — AX çok gevşek olabilir"
            else:
                diagnosis = "Veto/Allow dengesi normal"

        return jsonify({"ok": True, "data": {
            **data,
            "allow_rate":  round(allow / total * 100, 1) if total else 0,
            "veto_rate":   round(veto  / total * 100, 1) if total else 0,
            "watch_rate":  round(watch / total * 100, 1) if total else 0,
            "diagnosis":   diagnosis,
            "days":        days,
        }})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e), "data": {}}), 500


# ── /api/health ───────────────────────────────────────────────────────────────
@app.route("/api/health")
def api_health():
    """Sistem sağlık durumu."""
    try:
        db_ok = False
        db_msg = ""
        try:
            with get_conn() as conn:
                conn.execute("SELECT 1").fetchone()
            db_ok = True
        except Exception as e:
            db_msg = str(e)

        # Son scan zamanı
        pipeline_rows = get_pipeline_stats(limit=1)
        last_scan    = pipeline_rows[0]["scan_time"] if pipeline_rows else None
        last_cand_row = None
        last_trade_row = None
        try:
            with get_conn() as conn:
                r = conn.execute(
                    "SELECT created_at FROM signal_candidates ORDER BY id DESC LIMIT 1"
                ).fetchone()
                last_cand_row = r["created_at"] if r else None
                r2 = conn.execute(
                    "SELECT open_time FROM trades ORDER BY id DESC LIMIT 1"
                ).fetchone()
                last_trade_row = r2["open_time"] if r2 else None
        except Exception:
            pass

        return jsonify({"ok": True, "data": {
            "db_ok":            db_ok,
            "db_path":          DB_PATH,
            "db_error":         db_msg,
            "dashboard_ok":     True,
            "last_scan_time":   last_scan,
            "last_candidate_time": last_cand_row,
            "last_trade_time":  last_trade_row,
            "uptime":           "running",
        }})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e), "data": {}}), 500


if __name__ == "__main__":
    socketio.run(app, host="0.0.0.0", port=5000, debug=False, use_reloader=False, allow_unsafe_werkzeug=True)
