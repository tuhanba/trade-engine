"""
core/ai_decision_engine.py — AX AI Brain v4.9
=============================================
Aşama 9: AI Brain / Ghost Learning / Coin Personality.
"""
import logging
import sqlite3
import json
from datetime import datetime, timezone

logger = logging.getLogger(__name__)

class AIDecisionEngine:
    def __init__(self, db_path="trading.db"):
        self.db_path = db_path
        self._init_db()

    def _init_db(self):
        try:
            with sqlite3.connect(self.db_path) as conn:
                conn.execute("""CREATE TABLE IF NOT EXISTS ai_logs (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    event TEXT,
                    symbol TEXT,
                    decision TEXT,
                    score REAL,
                    confidence REAL,
                    reason TEXT,
                    data TEXT,
                    timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )""")
                conn.execute("""CREATE TABLE IF NOT EXISTS coin_personality (
                    symbol TEXT PRIMARY KEY,
                    win_rate REAL DEFAULT 0,
                    avg_pnl REAL DEFAULT 0,
                    volatility_rank INTEGER DEFAULT 3,
                    best_hour INTEGER,
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )""")
                conn.commit()
        except Exception as e:
            logger.error(f"AI DB init hatası: {e}")

    def decide(self, signal_data):
        """
        Sinyali analiz eder ve karar verir.
        """
        # SignalData nesnesi veya dict olabilir
        symbol = getattr(signal_data, 'symbol', signal_data.get('symbol'))
        score = getattr(signal_data, 'final_score', signal_data.get('final_score', 0))
        confidence = getattr(signal_data, 'confidence', signal_data.get('confidence', 0.8))
        
        # Karar Mantığı
        if score >= 80 and confidence >= 0.85:
            decision = "ALLOW"
            reason = "Yüksek skor ve güven onayı."
        elif score >= 65:
            decision = "WATCH"
            reason = "İzleme listesine alındı (Ghost Trade)."
        else:
            decision = "VETO"
            reason = "Düşük skor."
            
        # Log kaydı
        try:
            with sqlite3.connect(self.db_path) as conn:
                conn.execute("""
                    INSERT INTO ai_logs (event, symbol, decision, score, confidence, reason, data)
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                """, ("DECISION", symbol, decision, score, confidence, reason, json.dumps(signal_data if isinstance(signal_data, dict) else signal_data.__dict__)))
        except Exception as e:
            logger.error(f"AI Log kaydı hatası: {e}")
        
        return decision, reason

    def learn_from_outcome(self, symbol, pnl, reason):
        """
        Kapanan işlemlerden (Gerçek veya Ghost) öğrenir ve Coin Personality'yi günceller.
        """
        try:
            with sqlite3.connect(self.db_path) as conn:
                # Basit bir güncelleme mantığı
                conn.execute("""
                    INSERT INTO coin_personality (symbol, win_rate, avg_pnl, updated_at)
                    VALUES (?, ?, ?, CURRENT_TIMESTAMP)
                    ON CONFLICT(symbol) DO UPDATE SET
                    win_rate = (win_rate * 0.9) + (? * 0.1),
                    avg_pnl = (avg_pnl * 0.9) + (? * 0.1)
                """, (symbol, 1 if pnl > 0 else 0, pnl, 1 if pnl > 0 else 0, pnl))
                logger.info(f"AI Learned: {symbol} | PnL: {pnl:.2f}")
        except Exception as e:
            logger.error(f"AI Learning hatası: {e}")
