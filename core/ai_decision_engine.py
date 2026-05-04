"""
AI Decision Engine — Profesyonel Sürüm
Final karar katmanı. Sinyalleri onaylar veya reddeder.
"""
import logging
import sqlite3
import json
import uuid
from datetime import datetime, timedelta, timezone
from collections import defaultdict

logger = logging.getLogger(__name__)

# Config'den backtest tabanlı filtre parametrelerini al
try:
    import sys as _sys, os as _os
    _sys.path.insert(0, _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))))
    from config import (
        BAD_HOURS_UTC as _BAD_HOURS,
        GOOD_HOURS_UTC as _GOOD_HOURS,
        ALLOWED_QUALITIES as _ALLOWED_QUAL,
        DATA_THRESHOLD,
        WATCHLIST_THRESHOLD,
        TELEGRAM_THRESHOLD,
        TRADE_THRESHOLD
    )
except ImportError:
    _BAD_HOURS   = [5, 6, 14, 20]
    _GOOD_HOURS  = [0, 2, 3, 7, 15]
    _ALLOWED_QUAL = ["S", "A+", "A", "B"]
    DATA_THRESHOLD = 30
    WATCHLIST_THRESHOLD = 50
    TELEGRAM_THRESHOLD = 60
    TRADE_THRESHOLD = 70

class AIDecisionEngine:
    def __init__(self, db_path="trading.db"):
        self.db_path = db_path
        self.daily_signals = 0
        self.max_daily_signals = 40
        self.recent_coins = []
        self.last_reset_date = datetime.now(timezone.utc).date()
        self.thresholds = {
            "data": DATA_THRESHOLD,
            "watchlist": WATCHLIST_THRESHOLD,
            "telegram": TELEGRAM_THRESHOLD,
            "trade": TRADE_THRESHOLD,
        }
        self.params = {"sl_atr_mult": 1.5, "tp_atr_mult": 2.0, "risk_pct": 1.0}
        self._init_db()

    def _init_db(self):
        try:
            with sqlite3.connect(self.db_path) as conn:
                conn.execute("""CREATE TABLE IF NOT EXISTS ai_learning (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    symbol TEXT, trade_result TEXT, pnl REAL, setup_quality TEXT,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )""")
                conn.execute("""CREATE TABLE IF NOT EXISTS coin_profiles (
                    symbol TEXT PRIMARY KEY, win_rate REAL DEFAULT 0, total_trades INTEGER DEFAULT 0,
                    danger_score REAL DEFAULT 0, updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )""")
                conn.commit()
        except Exception as e:
            logger.error(f"AI DB init hatası: {e}")

    def evaluate(self, signal_data) -> dict:
        # Basit değerlendirme mantığı (Geliştirilebilir)
        base_score = (
            signal_data.coin_score * 0.1 +
            signal_data.trend_score * 0.3 +
            signal_data.trigger_score * 0.3 +
            signal_data.risk_score * 0.3
        )
        
        ai_score = base_score * 10
        final_score = max(0.0, min(100.0, ai_score))
        
        if final_score >= self.thresholds["trade"]:
            decision = "ALLOW"
        elif final_score >= self.thresholds["telegram"]:
            decision = "WATCH"
        else:
            decision = "VETO"

        return {
            "decision": decision,
            "final_score": round(final_score, 1),
            "reason": "score_evaluation"
        }
