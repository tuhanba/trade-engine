"""
app.py — AX Dashboard API v4.6 (ULTIMATE ELITE)
===============================================
Aşama 6: Dashboard / API / Frontend Senkronizasyonu.
"""
import eventlet
eventlet.monkey_patch()

import os
from flask import Flask, render_template, jsonify
from flask_socketio import SocketIO
from dotenv import load_dotenv
import database as db
from core.data_layer import calculate_duration
import logging

load_dotenv()
app = Flask(__name__, template_folder="templates", static_folder="static")
app.config['SECRET_KEY'] = os.getenv("SECRET_KEY", "ax_secret")
socketio = SocketIO(app, cors_allowed_origins="*", async_mode='eventlet')

@app.route('/')
def index():
    return render_template('index.html')

@app.route('/api/live')
def api_live():
    """
    Açık trade'lerin detaylı listesini döner.
    """
    try:
        open_trades = db.get_open_trades()
        results = []
        
        for t in open_trades:
            # Süre hesapla
            _, duration_str = calculate_duration(t['open_time'])
            
            # PnL ve Marjin detayları
            results.append({
                "id": t['id'],
                "symbol": t['symbol'],
                "direction": t['direction'],
                "entry": t['entry'],
                "sl": t['sl'],
                "tp1": t['tp1'],
                "tp2": t['tp2'],
                "status": t['status'],
                "realized_pnl": round(t.get('realized_pnl', 0), 2),
                "total_fee": round(t.get('total_fee', 0), 2),
                "duration_str": duration_str,
                "leverage": t.get('leverage', 10)
            })
        
        return jsonify({"ok": True, "data": {"live": results, "open_count": len(results)}})
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500

@app.route('/api/stats')
def api_stats():
    try:
        stats = db.get_stats()
        balance = db.get_paper_balance()
        return jsonify({
            "ok": True,
            "data": {
                "closed_net_pnl": stats.get('total_pnl', 0),
                "paper_balance": round(balance, 2),
                "total_trades": stats.get('total_trades', 0)
            }
        })
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500

if __name__ == '__main__':
    db.init_db()
    socketio.run(app, host='0.0.0.0', port=5000, debug=False)
