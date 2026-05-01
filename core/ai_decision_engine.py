"""
AI Decision Engine — Profesyonel Sürüm
Final karar katmanı. Sinyalleri onaylar veya reddeder.
Markov zinciri, postmortem analizi, saatlik heatmap, coin profili öğrenmesi ve parametre optimizasyonu içerir.
"""
import logging
import sqlite3
import json
from datetime import datetime, timedelta
from collections import defaultdict

logger = logging.getLogger(__name__)

# Config'den backtest tabanlı filtre parametrelerini al
try:
    import sys as _sys, os as _os
    _sys.path.insert(0, _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))))
    from config import BAD_HOURS_UTC as _BAD_HOURS, GOOD_HOURS_UTC as _GOOD_HOURS, ALLOWED_QUALITIES as _ALLOWED_QUAL
except ImportError:
    _BAD_HOURS   = [5, 6, 14, 20]
    _GOOD_HOURS  = [0, 2, 3, 7, 15]
    _ALLOWED_QUAL = ["A+"]

PARAM_BOUNDS = {
    "sl_atr_mult":    (0.8,  2.5),
    "tp_atr_mult":    (1.2,  4.5),
    "risk_pct":       (0.5,  3.0),
}

def clamp(v, lo, hi):
    return max(lo, min(hi, v))

class AIDecisionEngine:
    def __init__(self, db_path="trade_engine.db"):
        self.db_path = db_path
        self.daily_signals = 0
        self.max_daily_signals = 40
        self.recent_coins = []
        self.last_reset_date = datetime.utcnow().date()
        
        # Dinamik parametreler
        self.params = {
            "sl_atr_mult": 1.5,
            "tp_atr_mult": 2.0,
            "risk_pct": 1.0
        }
        
        self._init_db()
        self._load_best_params()

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
                conn.execute("""CREATE TABLE IF NOT EXISTS best_params (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    params_json TEXT,
                    win_rate REAL,
                    profit_factor REAL,
                    saved_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )""")
                conn.commit()
        except Exception as e:
            logger.error(f"AI DB init hatası: {e}")

    def _load_best_params(self):
        """En iyi parametreleri yükler."""
        try:
            with sqlite3.connect(self.db_path) as conn:
                conn.row_factory = sqlite3.Row
                best = conn.execute("SELECT * FROM best_params ORDER BY profit_factor DESC LIMIT 1").fetchone()
                if best:
                    self.params = json.loads(best["params_json"])
                    logger.info(f"En iyi parametreler yüklendi: {self.params}")
        except Exception as e:
            logger.error(f"Parametre yükleme hatası: {e}")

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
        try:
            with sqlite3.connect(self.db_path) as conn:
                conn.row_factory = sqlite3.Row
                trades = conn.execute("SELECT created_at, trade_result FROM ai_learning").fetchall()
                
                by_hour = defaultdict(list)
                for t in trades:
                    try:
                        dt = datetime.fromisoformat(t["created_at"])
                        by_hour[dt.hour].append(t["trade_result"])
                    except:
                        pass
                
                current_hour = datetime.utcnow().hour
                if current_hour in by_hour and len(by_hour[current_hour]) >= 3:
                    results = by_hour[current_hour]
                    wr = len([r for r in results if r == "WIN"]) / len(results)
                    if wr < 0.3: return -2.0
                    if wr > 0.6: return 1.0
        except Exception as e:
            logger.error(f"Heatmap hesaplama hatası: {e}")
            
        # Fallback — Backtest bulgularına göre kalibre edildi
        current_hour = datetime.utcnow().hour
        if current_hour in _BAD_HOURS:  return -2.5  # WR %13-22 — ağır ceza
        if current_hour in _GOOD_HOURS: return +1.5  # WR %44-51 — bonus
        return 0.0

    def _calc_markov_matrix(self) -> dict:
        """Geçmiş işlemlerden Markov geçiş matrisini hesaplar."""
        try:
            with sqlite3.connect(self.db_path) as conn:
                conn.row_factory = sqlite3.Row
                trades = conn.execute("SELECT trade_result FROM ai_learning ORDER BY id ASC").fetchall()
                
                if len(trades) < 5:
                    return None
                    
                transitions = {
                    ("WIN",  "WIN"):  0,
                    ("WIN",  "LOSS"): 0,
                    ("LOSS", "WIN"):  0,
                    ("LOSS", "LOSS"): 0,
                }
                
                for i in range(1, len(trades)):
                    prev_r = trades[i-1]["trade_result"]
                    curr_r = trades[i]["trade_result"]
                    if prev_r in ("WIN", "LOSS") and curr_r in ("WIN", "LOSS"):
                        transitions[(prev_r, curr_r)] += 1
                        
                total_from_win  = transitions[("WIN",  "WIN")]  + transitions[("WIN",  "LOSS")]
                total_from_loss = transitions[("LOSS", "WIN")]  + transitions[("LOSS", "LOSS")]
                
                ww = transitions[("WIN",  "WIN")]  / max(total_from_win,  1)
                ll = transitions[("LOSS", "LOSS")] / max(total_from_loss, 1)
                
                last3 = [t["trade_result"] for t in trades[-3:]]
                hot_streak  = all(r == "WIN"  for r in last3) and len(last3) == 3
                cold_streak = all(r == "LOSS" for r in last3) and len(last3) == 3
                
                return {
                    "WIN->WIN": ww,
                    "LOSS->LOSS": ll,
                    "hot_streak": hot_streak,
                    "cold_streak": cold_streak,
                    "sample": len(trades)
                }
        except Exception as e:
            logger.error(f"Markov hesaplama hatası: {e}")
            return None

    def evaluate(self, signal_data) -> dict:
        """Sinyali değerlendirir ve final kararını verir."""
        self._check_daily_reset()

        if not signal_data.is_valid():
            return {"decision": "VETO", "reason": "Invalid data"}

        if self.daily_signals >= self.max_daily_signals:
            return {"decision": "VETO", "reason": "Daily limit reached"}

        if signal_data.symbol in self.recent_coins[-3:]:
            return {"decision": "VETO", "reason": "Coin spam protection"}

        profile = self._get_coin_profile(signal_data.symbol)
        
        base_score = (
            signal_data.coin_score * 0.1 +
            signal_data.trend_score * 0.3 +
            signal_data.trigger_score * 0.3 +
            signal_data.risk_score * 0.3
        )

        ai_adj = 0.0
        
        # 1. Coin Geçmiş Performansı
        if profile["total_trades"] >= 5:
            if profile["win_rate"] < 0.3: ai_adj -= 2.0
            elif profile["win_rate"] > 0.6: ai_adj += 1.0
                
        # 2. Tehlike Skoru
        if profile["danger_score"] > 0.7: ai_adj -= 2.0
            
        # 3. Saatlik Heatmap
        ai_adj += self._get_hourly_heatmap_score()
        
        # 4. Markov Zinciri Etkisi
        markov = self._calc_markov_matrix()
        if markov:
            if markov["cold_streak"]: ai_adj -= 1.5
            elif markov["hot_streak"]: ai_adj += 1.0
            
            if markov["LOSS->LOSS"] > 0.65 and markov["sample"] >= 12:
                ai_adj -= 1.0

        # 5. ML Sinyal Skoru Etkisi
        ml_score = getattr(signal_data, "ml_score", 50)
        if ml_score > 75:
            ai_adj += 1.5
        elif ml_score > 60:
            ai_adj += 0.5
        elif ml_score < 35:
            ai_adj -= 2.0
        elif ml_score < 45:
            ai_adj -= 1.0

        final_score = base_score + ai_adj
        
        # Confidence hesaplamasında ML skorunu da hesaba kat
        base_confidence = max(0.0, min(1.0, final_score / 10.0))
        confidence = (base_confidence * 0.6) + ((ml_score / 100.0) * 0.4)

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
                
            # Dinamik parametreleri sinyale uygula
            signal_data.risk_percent = self.params["risk_pct"]

        return {
            "decision": decision,
            "final_score": round(final_score, 1),
            "confidence": round(confidence, 2),
            "reason": reason,
            "ai_adjustment": round(ai_adj, 1)
        }

    def _optimize_params(self):
        """Geçmiş işlemlere göre parametreleri optimize eder."""
        try:
            with sqlite3.connect(self.db_path) as conn:
                conn.row_factory = sqlite3.Row
                trades = conn.execute("SELECT * FROM ai_learning ORDER BY id DESC LIMIT 50").fetchall()
                
                if len(trades) < 10:
                    return
                    
                wins = [t for t in trades if t["trade_result"] == "WIN"]
                losses = [t for t in trades if t["trade_result"] == "LOSS"]
                
                wr = len(wins) / len(trades)
                gwin = sum(t["pnl"] for t in wins)
                gloss = abs(sum(t["pnl"] for t in losses))
                pf = gwin / gloss if gloss > 0 else 0
                
                markov = self._calc_markov_matrix()
                
                # Optimizasyon Kuralları
                if markov and markov["cold_streak"]:
                    self.params["risk_pct"] = clamp(self.params["risk_pct"] - 0.20, *PARAM_BOUNDS["risk_pct"])
                elif markov and markov["hot_streak"]:
                    self.params["risk_pct"] = clamp(self.params["risk_pct"] + 0.15, *PARAM_BOUNDS["risk_pct"])
                    
                if wr < 0.32:
                    self.params["sl_atr_mult"] = clamp(self.params["sl_atr_mult"] - 0.08, *PARAM_BOUNDS["sl_atr_mult"])
                elif wr > 0.60 and pf > 1.8:
                    self.params["risk_pct"] = clamp(self.params["risk_pct"] + 0.10, *PARAM_BOUNDS["risk_pct"])
                    self.params["tp_atr_mult"] = clamp(self.params["tp_atr_mult"] + 0.15, *PARAM_BOUNDS["tp_atr_mult"])
                    
                # En iyi parametreleri kaydet
                if wr > 0.40 and pf > 1.0:
                    best = conn.execute("SELECT * FROM best_params ORDER BY profit_factor DESC LIMIT 1").fetchone()
                    if not best or (pf > best["profit_factor"] and wr > best["win_rate"]):
                        conn.execute("""
                            INSERT INTO best_params (params_json, win_rate, profit_factor)
                            VALUES (?, ?, ?)
                        """, (json.dumps(self.params), wr, pf))
                        conn.commit()
                        logger.info(f"Yeni en iyi parametreler kaydedildi: {self.params}")
                        
        except Exception as e:
            logger.error(f"Parametre optimizasyon hatası: {e}")

    def learn_from_trade(self, symbol: str, result: str, pnl: float, setup_quality: str):
        """Kapanan işlemlerden öğrenir, coin profillerini günceller ve parametreleri optimize eder."""
        try:
            with sqlite3.connect(self.db_path) as conn:
                conn.execute("""
                    INSERT INTO ai_learning (symbol, trade_result, pnl, setup_quality)
                    VALUES (?, ?, ?, ?)
                """, (symbol, result, pnl, setup_quality))
                
                trades = conn.execute("""
                    SELECT trade_result FROM ai_learning WHERE symbol=? ORDER BY id DESC LIMIT 20
                """, (symbol,)).fetchall()
                
                if trades:
                    total = len(trades)
                    wins = sum(1 for t in trades if t[0] == "WIN")
                    win_rate = wins / total
                    
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
                
            # Parametreleri optimize et
            self._optimize_params()
            
        except Exception as e:
            logger.error(f"AI öğrenme hatası: {e}")
