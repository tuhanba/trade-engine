"""
AI Decision Engine — Profesyonel Sürüm
Final karar katmanı. Sinyalleri onaylar veya reddeder.
Markov zinciri, postmortem analizi, saatlik heatmap, coin profili öğrenmesi ve parametre optimizasyonu içerir.
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
        TRADE_THRESHOLD,
        MIN_CANDIDATES_FOR_COIN_LEARNING,
        MIN_TRADES_FOR_RISK_UPDATE,
        MIN_CANDIDATES_FOR_THRESHOLD_UPDATE,
    )
except ImportError:
    _BAD_HOURS   = [5, 6, 14, 20]
    _GOOD_HOURS  = [0, 2, 3, 7, 15]
    _ALLOWED_QUAL = ["A+"]
    DATA_THRESHOLD = 45
    WATCHLIST_THRESHOLD = 65
    TELEGRAM_THRESHOLD = 75
    TRADE_THRESHOLD = 82
    MIN_CANDIDATES_FOR_COIN_LEARNING = 30
    MIN_TRADES_FOR_RISK_UPDATE = 50
    MIN_CANDIDATES_FOR_THRESHOLD_UPDATE = 100

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
        self.max_daily_signals = 40  # Backtest: kalite > miktar
        self.recent_coins = []
        self.last_reset_date = datetime.now(timezone.utc).date()
        self.thresholds = {
            "data": DATA_THRESHOLD,
            "watchlist": WATCHLIST_THRESHOLD,
            "telegram": TELEGRAM_THRESHOLD,
            "trade": TRADE_THRESHOLD,
        }
        
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
                # best_params tablosu database.py tarafından yönetilir
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
            self._last_param_reload = datetime.now(timezone.utc)
        except Exception as e:
            logger.error(f"Parametre yükleme hatası: {e}")

    def _check_daily_reset(self):
        """Gece yarısı günlük sayacı sıfırlar."""
        current_date = datetime.now(timezone.utc).date()
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

    def _get_ghost_profile(self, symbol: str) -> dict:
        """
        Ghost (WATCH/VETO/SKIPPED) sinyallerinin geçmiş sonuçlarını okur.
        paper_results tablosundan finalize edilmiş kayıtları çeker.
        """
        try:
            with sqlite3.connect(self.db_path) as conn:
                row = conn.execute("""
                    SELECT
                        COUNT(*) as total,
                        SUM(CASE WHEN would_have_won=1 THEN 1 ELSE 0 END) as wins,
                        SUM(CASE WHEN skip_decision_correct=1 THEN 1 ELSE 0 END) as correct_skips,
                        AVG(max_favorable_excursion) as avg_mfe,
                        AVG(max_adverse_excursion) as avg_mae
                    FROM paper_results
                    WHERE symbol=? AND status IN ('completed','finalized')
                """, (symbol,)).fetchone()
                if row and row[0]:
                    total = row[0]
                    wins  = row[1] or 0
                    return {
                        "total":          total,
                        "ghost_win_rate": round(wins / total, 4),
                        "correct_skips":  row[2] or 0,
                        "avg_mfe":        round(float(row[3] or 0), 4),
                        "avg_mae":        round(float(row[4] or 0), 4),
                    }
        except Exception as e:
            logger.debug(f"Ghost profile okuma atlandı: {e}")
        return {"total": 0, "ghost_win_rate": 0.5, "correct_skips": 0, "avg_mfe": 0, "avg_mae": 0}

    def _get_hourly_heatmap_score(self) -> float:
        """Mevcut saatin geçmiş performansına göre risk skoru döner."""
        try:
            with sqlite3.connect(self.db_path) as conn:
                conn.row_factory = sqlite3.Row
                trades = conn.execute(
                    "SELECT created_at, trade_result FROM ai_learning WHERE trade_result IN ('WIN','LOSS')"
                ).fetchall()
                
                by_hour = defaultdict(list)
                for t in trades:
                    try:
                        dt = datetime.fromisoformat(t["created_at"])
                        by_hour[dt.hour].append(t["trade_result"])
                    except Exception:
                        pass
                
                current_hour = datetime.now(timezone.utc).hour
                if current_hour in by_hour and len(by_hour[current_hour]) >= 3:
                    results = by_hour[current_hour]
                    wr = len([r for r in results if r == "WIN"]) / len(results)
                    if wr < 0.3: return -2.0
                    if wr > 0.6: return 1.0
        except Exception as e:
            logger.error(f"Heatmap hesaplama hatası: {e}")
            
        # Fallback — Backtest bulgularına göre kalibre edildi
        current_hour = datetime.now(timezone.utc).hour
        if current_hour in _BAD_HOURS:  return -2.5  # WR %13-22 — ağır ceza
        if current_hour in _GOOD_HOURS: return +1.5  # WR %44-51 — bonus
        return 0.0

    def _calc_markov_matrix(self) -> dict:
        """Geçmiş işlemlerden Markov geçiş matrisini hesaplar."""
        try:
            with sqlite3.connect(self.db_path) as conn:
                conn.row_factory = sqlite3.Row
                trades = conn.execute(
                    """SELECT trade_result FROM ai_learning
                       WHERE trade_result IN ('WIN','LOSS')
                       ORDER BY id ASC"""
                ).fetchall()
                
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

    def _maybe_reload_params(self):
        """Her 5 dakikada bir best_params'tan parametreleri yeniler (analyze_and_adapt senkronizasyonu)."""
        last = getattr(self, "_last_param_reload", None)
        if last is None or (datetime.now(timezone.utc) - last).total_seconds() > 300:
            self._load_best_params()

    def evaluate(self, signal_data) -> dict:
        """Sinyali değerlendirir ve final kararını verir."""
        self._check_daily_reset()
        self._maybe_reload_params()

        if not signal_data.is_valid():
            return {"decision": "VETO", "reason": "Invalid data"}

        if self.daily_signals >= self.max_daily_signals:
            return {"decision": "VETO", "reason": "Daily limit reached"}

        # Coin spam koruması: aynı coin son 2 sinyalde varsa engelle
        if signal_data.symbol in self.recent_coins[-2:]:
            return {"decision": "VETO", "reason": "Coin spam protection"}

        profile = self._get_coin_profile(signal_data.symbol)
        ghost  = self._get_ghost_profile(signal_data.symbol)

        base_score = (
            signal_data.coin_score * 0.1 +
            signal_data.trend_score * 0.3 +
            signal_data.trigger_score * 0.3 +
            signal_data.risk_score * 0.3
        )

        ai_adj = 0.0

        # 1. Coin Geçmiş Performansı (gerçek trade'ler)
        if profile["total_trades"] >= 5:
            if profile["win_rate"] < 0.3: ai_adj -= 2.0
            elif profile["win_rate"] > 0.6: ai_adj += 1.0

        # 1b. Ghost Learning — WATCH/VETO/SKIPPED sonuçlarından öğren
        GHOST_WEIGHT = 0.30
        if ghost["total"] >= 5:
            gwr = ghost["ghost_win_rate"]
            correct_skip_rate = ghost["correct_skips"] / ghost["total"]
            # Çok kazandıran ama atlanan coin → bonus
            if gwr > 0.65 and correct_skip_rate < 0.4:
                ai_adj += 1.5 * GHOST_WEIGHT
            elif gwr > 0.55:
                ai_adj += 0.8 * GHOST_WEIGHT
            # Doğru atlanan coin (SL'e giden) → ceza
            elif correct_skip_rate > 0.70:
                ai_adj -= 1.0 * GHOST_WEIGHT
            # Çoğu SL'e giden coin → ek ceza
            if gwr < 0.35:
                ai_adj -= 1.5 * GHOST_WEIGHT

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
        ml_score = getattr(signal_data, "ml_score", None)
        if ml_score is None:
            from core.score_engine import cold_start_score
            ml_score = cold_start_score(signal_data.to_dict() if hasattr(signal_data, "to_dict") else {})
        if ml_score > 75:
            ai_adj += 1.5
        elif ml_score > 60:
            ai_adj += 0.5
        elif ml_score < 35:
            ai_adj -= 2.0
        elif ml_score < 45:
            ai_adj -= 1.0

        ai_score = (base_score + ai_adj) * 10
        final_score = max(0.0, min(100.0, ai_score))
        
        # Confidence hesaplamasında ML skorunu da hesaba kat
        base_confidence = max(0.0, min(1.0, final_score / 100.0))
        confidence = (base_confidence * 0.6) + ((ml_score / 100.0) * 0.4)

        if final_score >= self.thresholds["trade"]:
            decision = "ALLOW"
            reason = "approved_for_trade"
        elif final_score >= self.thresholds["telegram"]:
            decision = "WATCH"
            reason = "approved_for_telegram"
        elif final_score >= self.thresholds["watchlist"]:
            decision = "WATCH"
            reason = "approved_for_watchlist"
        elif final_score >= self.thresholds["data"]:
            decision = "CANDIDATE"
            reason = "approved_for_data_collection"
        else:
            decision = "VETO"
            reason = "low_confidence"

        if decision == "ALLOW":
            self.daily_signals += 1
            self.recent_coins.append(signal_data.symbol)
            if len(self.recent_coins) > 10:
                self.recent_coins.pop(0)
                
            # Dinamik parametreleri sinyale uygula
            signal_data.risk_percent = self.params["risk_pct"]

        from ml_signal_scorer import get_scorer as _get_scorer
        try:
            _scorer_trained = _get_scorer().trained
        except Exception:
            _scorer_trained = False
        score_source = "mixed" if _scorer_trained else "cold_start"

        return {
            "decision": decision,
            "final_score": round(final_score, 1),
            "ai_score": round(ai_score, 1),
            "ml_score": round(ml_score, 1),
            "confidence": round(confidence, 2),
            "reason": reason,
            "ai_adjustment": round(ai_adj, 1),
            "score_source": score_source,
        }

    def _optimize_params(self):
        """Geçmiş işlemlere göre parametreleri optimize eder."""
        try:
            with sqlite3.connect(self.db_path) as conn:
                conn.row_factory = sqlite3.Row
                trades = conn.execute("SELECT * FROM ai_learning ORDER BY id DESC LIMIT 50").fetchall()
                
                if len(trades) < MIN_TRADES_FOR_RISK_UPDATE:
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
                if wr > 0.40 and pf > 1.0 and len(trades) >= MIN_CANDIDATES_FOR_THRESHOLD_UPDATE:
                    best = conn.execute("SELECT * FROM best_params ORDER BY profit_factor DESC LIMIT 1").fetchone()
                    if not best or (pf > best["profit_factor"] and wr > best["win_rate"]):
                        conn.execute("""
                            INSERT INTO best_params
                                (data, params_json, win_rate, profit_factor)
                            VALUES (?, ?, ?, ?)
                        """, (json.dumps(self.params), json.dumps(self.params), wr, pf))
                        conn.commit()
                        logger.info(f"Yeni en iyi parametreler kaydedildi: {self.params}")
                        
        except Exception as e:
            logger.error(f"Parametre optimizasyon hatası: {e}")

    def learn_from_paper_outcome(
        self,
        symbol: str,
        tracked_from: str,
        would_have_won: int,
        mfe_r: float,
        mae_r: float,
        first_touch: str,
        skip_correct: int,
    ):
        """Girilmeyen fırsatların çıktısı — gerçek trade Markov zincirine karışmasın."""
        try:
            tr = "PAPER_WIN" if would_have_won else "PAPER_LOSS"
            with sqlite3.connect(self.db_path) as conn:
                conn.execute(
                    """INSERT INTO ai_learning (symbol, trade_result, pnl, setup_quality)
                       VALUES (?, ?, 0.0, ?)""",
                    (
                        symbol,
                        tr,
                        f"paper:{tracked_from}:{first_touch}:mfe={mfe_r}:mae={mae_r}:skip_ok={skip_correct}",
                    ),
                )

                conn.execute(
                    """INSERT INTO adaptive_stats (
                        id, scope, key, sample_size, win_rate, expectancy, avg_r,
                        threshold_data, threshold_watchlist, threshold_telegram, threshold_trade,
                        action_taken, notes)
                       VALUES (?, 'paper_tracking', ?, 1, ?, 0.0, 0.0, 0, 0, 0, 0, '', ?)""",
                    (
                        str(uuid.uuid4()),
                        f"{tracked_from}:{first_touch}:{symbol}",
                        float(would_have_won),
                        json.dumps({"mfe_r": mfe_r, "mae_r": mae_r, "skip_correct": skip_correct}),
                    ),
                )
                conn.commit()

            # Ghost istatistiklerini coin_profiles'a yansıt (GHOST_WEIGHT ağırlıklı)
            self._update_coin_profile_with_ghost(symbol)

        except Exception as e:
            logger.debug(f"Paper outcome learn atlandı: {e}")

    def _update_coin_profile_with_ghost(self, symbol: str):
        """
        Finalize edilmiş ghost sonuçlarını coin_profiles'a GHOST_WEIGHT (0.30) ile blend eder.
        Gerçek trade verisi yokken bile coin'in davranışı hakkında erken sinyal sağlar.
        """
        GHOST_WEIGHT = 0.30
        try:
            with sqlite3.connect(self.db_path) as conn:
                ghost_row = conn.execute("""
                    SELECT COUNT(*) as total,
                           SUM(CASE WHEN would_have_won=1 THEN 1 ELSE 0 END) as wins
                    FROM paper_results
                    WHERE symbol=? AND status IN ('completed','finalized')
                """, (symbol,)).fetchone()
                if not ghost_row or not ghost_row[0]:
                    return
                g_total, g_wins = ghost_row[0], ghost_row[1] or 0
                if g_total < 3:
                    return
                ghost_wr = g_wins / g_total

                real_row = conn.execute(
                    "SELECT win_rate, total_trades, danger_score FROM coin_profiles WHERE symbol=?",
                    (symbol,)
                ).fetchone()

                if real_row and real_row[1] >= 5:
                    # Gerçek veri varsa ghost'u hafif blend et
                    blended_wr = real_row[0] * (1 - GHOST_WEIGHT) + ghost_wr * GHOST_WEIGHT
                    danger = real_row[2]
                    total  = real_row[1]
                else:
                    # Gerçek veri yoksa ghost veriyi baskın kullan
                    blended_wr = ghost_wr
                    danger = 0.6 if ghost_wr < 0.35 else 0.0
                    total  = g_total

                conn.execute("""
                    INSERT OR REPLACE INTO coin_profiles
                        (symbol, win_rate, total_trades, danger_score, updated_at)
                    VALUES (?, ?, ?, ?, CURRENT_TIMESTAMP)
                """, (symbol, round(blended_wr, 4), total, danger))
                conn.commit()
                logger.debug(
                    f"[Ghost→Profile] {symbol}: ghost_wr={ghost_wr:.2f} "
                    f"blended_wr={blended_wr:.2f} total={total}"
                )
        except Exception as e:
            logger.debug(f"Ghost profile güncelleme atlandı: {e}")

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
                    if total >= MIN_CANDIDATES_FOR_COIN_LEARNING and all(t[0] == "LOSS" for t in trades[:3]):
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

    def learn_from_outcome(self, symbol: str, net_pnl: float, reason: str):
        """
        Trade sonucundan öğren. Coin profilini tam olarak güncelle.
        execution_engine._finalize() tarafından çağrılır.
        """
        try:
            with sqlite3.connect(self.db_path) as conn:
                conn.row_factory = sqlite3.Row
                rows = conn.execute("""
                    SELECT net_pnl, tp1_hit, tp2_hit, r_multiple, duration_seconds
                    FROM trades
                    WHERE symbol=? AND status='closed' AND is_valid_for_stats=1
                    ORDER BY id DESC LIMIT 30
                """, (symbol,)).fetchall()

            if not rows:
                return

            total  = len(rows)
            wins   = sum(1 for r in rows if (r["net_pnl"] or 0) > 0)
            tp1s   = sum(1 for r in rows if r["tp1_hit"])
            tp2s   = sum(1 for r in rows if r["tp2_hit"])
            avg_r  = sum(float(r["r_multiple"] or 0) for r in rows) / total
            gp = sum(float(r["net_pnl"] or 0) for r in rows if (r["net_pnl"] or 0) > 0)
            gl = abs(sum(float(r["net_pnl"] or 0) for r in rows if (r["net_pnl"] or 0) < 0))
            pf     = round(gp / gl, 2) if gl > 0 else 0
            avg_dur = sum(float(r["duration_seconds"] or 0) for r in rows) / total / 60

            import sys as _sys, os as _os
            _sys.path.insert(0, _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))))
            from database import update_coin_profile
            update_coin_profile(symbol, {
                "win_rate":       round(wins / total, 4),
                "avg_r":          round(avg_r, 3),
                "profit_factor":  pf,
                "tp1_hit_rate":   round(tp1s / total, 4),
                "tp2_hit_rate":   round(tp2s / total, 4),
                "avg_duration":   round(avg_dur, 1),
                "sample_size":    total,
                "danger_score":   round(max(0, 1 - (wins / total)), 2),
            })
            logger.info(
                f"[AI] {symbol} profil: wr={wins/total:.1%} pf={pf} avg_r={avg_r:.2f} n={total}"
            )
        except Exception as e:
            logger.warning(f"[AI] learn_from_outcome hatası {symbol}: {e}")
