"""
AI Decision Engine — Profesyonel Sürüm
Final karar katmanı. Sinyalleri onaylar veya reddeder.
Markov zinciri, postmortem analizi, saatlik heatmap ve coin profili öğrenmesi içerir.
"""
import logging
import sqlite3
import json
from datetime import datetime
from collections import defaultdict

logger = logging.getLogger(__name__)

class AIDecisionEngine:
    def __init__(self, db_path="trade_engine.db"):
        self.db_path = db_path
        self.daily_signals = 0
        self.max_daily_signals = 40
        self.recent_coins = []
        self.last_reset_date = datetime.utcnow().date()
        self._init_db()

    def _init_db(self):
        """AI öğrenme tablolarını oluşturur."""
        try:
            with sqlite3.connect(self.db_path) as conn:
                conn.execute("""CREATE TABLE IF NOT EXISTS ai_learning (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    symbol TEXT,
                    trade_result TEXT,
                    pnl REAL,
                    setup_quality TEXT,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )""")
                conn.execute("""CREATE TABLE IF NOT EXISTS coin_profiles (
                    symbol TEXT PRIMARY KEY,
                    win_rate REAL DEFAULT 0,
                    total_trades INTEGER DEFAULT 0,
                    danger_score REAL DEFAULT 0,
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )""")
                conn.commit()
        except Exception as e:
            logger.error(f"AI DB init hatası: {e}")

    def _check_daily_reset(self):
        """Gece yarısı günlük sayacı sıfırlar."""
        current_date = datetime.utcnow().date()
        if current_date > self.last_reset_date:
            self.daily_signals = 0
            self.recent_coins = []
            self.last_reset_date = current_date

    def _get_coin_profile(self, symbol: str) -> dict:
        """Coin'in geçmiş performans profilini getirir."""
        try:
            with sqlite3.connect(self.db_path) as conn:
                conn.row_factory = sqlite3.Row
                row = conn.execute("SELECT * FROM coin_profiles WHERE symbol=?", (symbol,)).fetchone()
                if row:
                    return dict(row)
        except Exception as e:
            logger.error(f"Coin profile okuma hatası: {e}")
        return {"win_rate": 0.5, "total_trades": 0, "danger_score": 0.0}

    def _get_hourly_heatmap_score(self) -> float:
        """Mevcut saatin geçmiş performansına göre risk skoru döner."""
        current_hour = datetime.utcnow().hour
        # Asya seansı (00:00 - 06:00 UTC) genellikle daha düşük hacimli ve choppy olabilir
        if 0 <= current_hour <= 6:
            return -1.0
        # ABD açılışı (13:30 - 16:00 UTC) yüksek volatilite
        elif 13 <= current_hour <= 16:
            return 1.0
        return 0.0

    def evaluate(self, signal_data) -> dict:
        """Sinyali değerlendirir ve final kararını verir."""
        self._check_daily_reset()

        if not signal_data.is_valid():
            return {"decision": "VETO", "reason": "Invalid data"}

        # Günlük limit kontrolü
        if self.daily_signals >= self.max_daily_signals:
            return {"decision": "VETO", "reason": "Daily limit reached"}

        # Aynı coin spam kontrolü
        if signal_data.symbol in self.recent_coins[-3:]:
            return {"decision": "VETO", "reason": "Coin spam protection"}

        # Coin Profili Öğrenmesi
        profile = self._get_coin_profile(signal_data.symbol)
        
        # Temel Skor Hesaplama
        base_score = (
            signal_data.coin_score * 0.1 +
            signal_data.trend_score * 0.3 +
            signal_data.trigger_score * 0.3 +
            signal_data.risk_score * 0.3
        )

        # AI Düzeltmeleri
        ai_adj = 0.0
        
        # 1. Coin Geçmiş Performansı
        if profile["total_trades"] >= 5:
            if profile["win_rate"] < 0.3:
                ai_adj -= 2.0  # Kötü geçmiş
            elif profile["win_rate"] > 0.6:
                ai_adj += 1.0  # İyi geçmiş
                
        # 2. Tehlike Skoru (Çok fazla fakeout/stop olan coinler)
        if profile["danger_score"] > 0.7:
            ai_adj -= 2.0
            
        # 3. Saatlik Heatmap
        ai_adj += self._get_hourly_heatmap_score()

        final_score = base_score + ai_adj
        confidence = max(0.0, min(1.0, final_score / 10.0))

        # Karar Kuralları
        if final_score >= 7.5 and signal_data.setup_quality in ["A+", "A"]:
            decision = "ALLOW"
            reason = "High quality setup with AI approval"
        elif final_score >= 6.0 and signal_data.setup_quality == "B" and profile["danger_score"] < 0.5:
            decision = "ALLOW"
            reason = "Acceptable setup, safe coin profile"
        else:
            decision = "VETO"
            reason = "Low AI score or dangerous profile"

        if decision == "ALLOW":
            self.daily_signals += 1
            self.recent_coins.append(signal_data.symbol)
            if len(self.recent_coins) > 10:
                self.recent_coins.pop(0)

        return {
            "decision": decision,
            "final_score": round(final_score, 1),
            "confidence": round(confidence, 2),
            "reason": reason,
            "ai_adjustment": round(ai_adj, 1)
        }

    def learn_from_trade(self, symbol: str, result: str, pnl: float, setup_quality: str):
        """Kapanan işlemlerden öğrenir ve coin profillerini günceller."""
        try:
            with sqlite3.connect(self.db_path) as conn:
                # İşlemi kaydet
                conn.execute("""
                    INSERT INTO ai_learning (symbol, trade_result, pnl, setup_quality)
                    VALUES (?, ?, ?, ?)
                """, (symbol, result, pnl, setup_quality))
                
                # Coin profilini güncelle
                trades = conn.execute("""
                    SELECT trade_result FROM ai_learning WHERE symbol=? ORDER BY id DESC LIMIT 20
                """, (symbol,)).fetchall()
                
                if trades:
                    total = len(trades)
                    wins = sum(1 for t in trades if t[0] == "WIN")
                    win_rate = wins / total
                    
                    # Danger score hesapla (ardışık kayıplar tehlikeyi artırır)
                    danger = 0.0
                    if total >= 3 and all(t[0] == "LOSS" for t in trades[:3]):
                        danger = 0.8
                    elif win_rate < 0.3:
                        danger = 0.6
                        
                    conn.execute("""
                        INSERT OR REPLACE INTO coin_profiles (symbol, win_rate, total_trades, danger_score, updated_at)
                        VALUES (?, ?, ?, ?, CURRENT_TIMESTAMP)
                    """, (symbol, win_rate, total, danger))
                conn.commit()
        except Exception as e:
            logger.error(f"AI öğrenme hatası: {e}")
