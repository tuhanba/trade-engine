import os, json, sqlite3, re
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
socketio = SocketIO(app, cors_allowed_origins="*", async_mode="threading")

client = Client(os.getenv("BINANCE_API_KEY", ""), os.getenv("BINANCE_API_SECRET", ""))

init_db()
dash_svc.start()

DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "trading.db")

@app.route("/")
def index():
    return render_template("index.html")

@app.route("/api/stats")
def api_stats():
    try:
        stats = get_stats()
        # Elite verilerini ekle
        with get_conn() as conn:
            row = conn.execute("SELECT COUNT(*) FROM paper_trades").fetchone()
            stats["ghost_trades_count"] = row[0] if row else 0
            
            row_sentiment = conn.execute("SELECT value FROM state WHERE key='market_sentiment'").fetchone()
            stats["market_sentiment"] = float(row_sentiment[0]) if row_sentiment else 50.0
            
        return jsonify({"ok": True, "data": stats})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500

@app.route("/api/live")
def api_live():
    try:
        open_trades = get_open_trades()
        live = []
        for t in open_trades:
            # Elite verilerini ekle (AI Confidence vb.)
            live.append({**t, "ai_confidence": t.get("confidence", 0.8)})
        
        return jsonify({"ok": True, "data": {"live": live, "open_count": len(live)}})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500

# SocketIO Events for Real-time Elite Updates
@socketio.on('connect')
def handle_connect():
    print('Client connected to Elite Dashboard')

if __name__ == "__main__":
    socketio.run(app, host="0.0.0.0", port=5000)
