"""
core/ai_decision_engine.py — AX Ghost Learning & AI Decision Engine v5.0
=========================================================================
Ghost Learning sistemi: açılmayan sinyalleri takip eder, 
pattern öğrenir ve AI kararlarını adaptif olarak geliştir.

Özellikler:
  - Adaptif scoring (geçmiş performansa göre ağırlık)
  - Ghost trade memory (açılmayan sinyallerin sonuçları)
  - Score decaying (zamanla başarısızlık denemelerini azalt)
  - Market context entegrasyonu
  - Multi-factor confidence hesabı
"""

from __future__ import annotations

import logging
import sqlite3
<<<<<<< HEAD
from dataclasses import dataclass
from datetime import datetime, timezone, timedelta
from typing import Optional
=======
import json
import uuid
from datetime import datetime, timedelta, timezone
from collections import defaultdict
from core.signal_intelligence import SignalIntelligence
>>>>>>> 0797c70b8640d2006e47a50d5580ffae4606199b

from core.data_layer import SignalData, SignalDecision

logger = logging.getLogger("ax.ai_decision")


# ── AI Decision Result ───────────────────────────────────────────────

<<<<<<< HEAD
@dataclass
class AIDecisionResult:
    """AI karar sonucu."""
    decision: str = SignalDecision.WATCH.value
    reason: str = ""
    confidence: float = 0.0
    score_adjusted: float = 0.0
    ghost_insight: str = ""
=======
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
        self.intelligence = SignalIntelligence(db_path=db_path)
>>>>>>> 0797c70b8640d2006e47a50d5580ffae4606199b


# ── Ghost Memory Manager ─────────────────────────────────────────────

class GhostMemoryManager:
    """
    Ghost learning: açılmayan sinyallerin geçmiş sonuçlarını
    analiz ederek AI kararına adaptif katkı sağlar.
    """

    def __init__(self, db_path: str = ""):
        try:
            import config
            self.db_path = db_path or config.DB_PATH
        except Exception:
            self.db_path = "trading.db"

    def get_symbol_ghost_stats(self, symbol: str, days: int = 14) -> dict:
        """
        Belirli sembol için ghost trade başarı istatistiklerini döner.
        Son N gündeki VETO edilen sinyallerin TP/SL oranı.
        """
        try:
<<<<<<< HEAD
            cutoff = (
                datetime.now(timezone.utc) - timedelta(days=days)
            ).isoformat()
            conn = sqlite3.connect(self.db_path, timeout=5)
            conn.row_factory = sqlite3.Row
            try:
                rows = conn.execute(
                    """
                    SELECT status, COUNT(*) as cnt
                    FROM signal_candidates
                    WHERE symbol = ? AND created_at >= ?
                    AND status IN ('TP_HIT', 'SL_HIT')
                    GROUP BY status
                    """,
                    (symbol, cutoff),
                ).fetchall()
=======
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
>>>>>>> 0797c70b8640d2006e47a50d5580ffae4606199b

                tp_hits = 0
                sl_hits = 0
                for r in rows:
                    if r["status"] == "TP_HIT":
                        tp_hits = r["cnt"]
                    elif r["status"] == "SL_HIT":
                        sl_hits = r["cnt"]

                total = tp_hits + sl_hits
                ghost_wr = round(tp_hits / total * 100, 1) if total > 0 else 0.0

                return {
                    "total": total,
                    "tp_hits": tp_hits,
                    "sl_hits": sl_hits,
                    "ghost_winrate": ghost_wr,
                }
            finally:
                conn.close()
        except Exception as exc:
            logger.debug("Ghost stats alınamadı [%s]: %s", symbol, exc)
            return {"total": 0, "tp_hits": 0, "sl_hits": 0, "ghost_winrate": 0.0}

    def get_direction_bias(self, symbol: str, days: int = 7) -> dict:
        """
        Sembol için yön bias analizi:
        Son N günde hangi yön (LONG/SHORT) daha başarılı?
        """
        try:
            cutoff = (
                datetime.now(timezone.utc) - timedelta(days=days)
            ).isoformat()
            conn = sqlite3.connect(self.db_path, timeout=5)
            conn.row_factory = sqlite3.Row
            try:
                rows = conn.execute(
                    """
                    SELECT side, status, COUNT(*) as cnt
                    FROM signal_candidates
                    WHERE symbol = ? AND created_at >= ?
                    AND status IN ('TP_HIT', 'SL_HIT')
                    GROUP BY side, status
                    """,
                    (symbol, cutoff),
                ).fetchall()

                data = {"LONG": {"tp": 0, "sl": 0}, "SHORT": {"tp": 0, "sl": 0}}
                for r in rows:
                    side = r["side"].upper()
                    if side not in data:
                        continue
                    if r["status"] == "TP_HIT":
                        data[side]["tp"] += r["cnt"]
                    else:
                        data[side]["sl"] += r["cnt"]

                result = {}
                for side, counts in data.items():
                    total = counts["tp"] + counts["sl"]
                    wr = round(counts["tp"] / total * 100, 1) if total > 0 else 0.0
                    result[side] = {"total": total, "winrate": wr}

                return result
            finally:
                conn.close()
        except Exception as exc:
            logger.debug("Direction bias alınamadı: %s", exc)
            return {}

    def get_score_multiplier(self, symbol: str, side: str) -> float:
        """
        Ghost performance'a göre skor çarpanı hesaplar.
        İyi performans → boost, kötü performans → reduce.
        Range: 0.5 – 1.5
        """
        stats = self.get_symbol_ghost_stats(symbol)
        if stats["total"] < 3:
            return 1.0  # Yetersiz veri

        wr = stats["ghost_winrate"]
        if wr >= 70:
            return 1.3
        elif wr >= 55:
            return 1.1
        elif wr <= 30:
            return 0.7
        elif wr <= 45:
            return 0.85
        return 1.0


# ── Adaptif Scoring ──────────────────────────────────────────────────

class AdaptiveScorer:
    """Sinyale adaptif skor atar — ghost history + market context."""

    def __init__(self, ghost_manager: GhostMemoryManager):
        self.ghost = ghost_manager

    def compute_adjusted_score(
        self,
        signal: SignalData,
        context: dict,
    ) -> float:
        """
        Ham skoru pek çok faktörle ayarlar:
          1. Ghost history multiplier
          2. Market trend alignment
          3. Hour-of-day quality
          4. Volatility factor
          5. RR quality bonus
        """
        from core.accounting import calculate_rr

        base_score = signal.score

        # 1. Ghost multiplier
        ghost_mult = self.ghost.get_score_multiplier(signal.symbol, signal.side)
        adjusted = base_score * ghost_mult

        # 2. Market trend alignment
        trend = context.get("market_trend", "neutral").lower()
        if trend == "bullish" and signal.side == "LONG":
            adjusted *= 1.15
        elif trend == "bearish" and signal.side == "SHORT":
            adjusted *= 1.15
        elif trend == "bullish" and signal.side == "SHORT":
            adjusted *= 0.85
        elif trend == "bearish" and signal.side == "LONG":
            adjusted *= 0.85

        # 3. RR quality bonus
        tp1 = signal.tp1 or 0
        if tp1 > 0 and signal.entry_price > 0 and signal.stop_loss > 0:
            rr = calculate_rr(signal.entry_price, signal.stop_loss, tp1)
            if rr >= 3.0:
                adjusted *= 1.2
            elif rr >= 2.0:
                adjusted *= 1.1
            elif rr < 1.5:
                adjusted *= 0.9

        # 4. Volatility factor
        vol = context.get("volatility", "normal")
        if vol == "high":
            adjusted *= 0.92  # Yüksek volatilite → risk artıyor
        elif vol == "low":
            adjusted *= 0.95

        # 5. Volume confirmation
        vol_ratio = float(context.get("volume_ratio", 1.0))
        if vol_ratio >= 2.0:
            adjusted *= 1.1
        elif vol_ratio < 0.5:
            adjusted *= 0.9

        return round(min(adjusted, 200.0), 1)


# ── Ana AI Decision Engine ───────────────────────────────────────────

# Singleton ghost manager
_ghost_manager: Optional[GhostMemoryManager] = None

def _get_ghost_manager() -> GhostMemoryManager:
    global _ghost_manager
    if _ghost_manager is None:
        _ghost_manager = GhostMemoryManager()
    return _ghost_manager


def classify_signal(
    signal: SignalData,
    context: dict | None = None,
) -> AIDecisionResult:
    """
    Sinyali değerlendirip ALLOW / WATCH / VETO kararı verir.

    context opsiyonel alanlar:
      - market_trend: "bullish" | "bearish" | "neutral"
      - volatility: "high" | "normal" | "low"
      - open_trade_count: int
      - volume_ratio: float (relative volume)
    """
    ctx = context or {}
    ghost = _get_ghost_manager()
    scorer = AdaptiveScorer(ghost)

    # ── Temel validasyon ─────────────────────────────────────────
    if signal.entry_price <= 0 or signal.stop_loss <= 0:
        return AIDecisionResult(
            decision=SignalDecision.VETO.value,
            reason="Geçersiz entry/SL değerleri",
            confidence=1.0,
        )

    stop_dist = abs(signal.entry_price - signal.stop_loss)
    if stop_dist <= 0:
        return AIDecisionResult(
            decision=SignalDecision.VETO.value,
            reason="Stop distance sıfır",
            confidence=1.0,
        )

<<<<<<< HEAD
    # ── Score çok düşük ──────────────────────────────────────────
    if signal.score < 5:
        return AIDecisionResult(
            decision=SignalDecision.VETO.value,
            reason=f"Ham score çok düşük: {signal.score}",
            confidence=0.9,
        )
=======
        # 5. Historical Intelligence Boost
        intel_boost = self.intelligence.get_ai_boost_recommendation(signal_data.symbol, signal_data.setup_quality)
        ai_adj += intel_boost
        
        # 6. ML Sinyal Skoru Etkisi
        ml_score = getattr(signal_data, "ml_score", 50)
        if ml_score > 75:
            ai_adj += 1.5
        elif ml_score > 60:
            ai_adj += 0.5
        elif ml_score < 35:
            ai_adj -= 2.0
        elif ml_score < 45:
            ai_adj -= 1.0
>>>>>>> 0797c70b8640d2006e47a50d5580ffae4606199b

    # ── Adaptif score hesapla ────────────────────────────────────
    adjusted_score = scorer.compute_adjusted_score(signal, ctx)

    # ── Ghost insight ────────────────────────────────────────────
    ghost_stats = ghost.get_symbol_ghost_stats(signal.symbol)
    ghost_insight = ""
    if ghost_stats["total"] >= 3:
        ghost_insight = (
            f"Ghost WR: {ghost_stats['ghost_winrate']}% "
            f"({ghost_stats['tp_hits']}W/{ghost_stats['sl_hits']}L)"
        )

    # ── Karar ────────────────────────────────────────────────────
    decision = SignalDecision.WATCH.value
    reason = f"Adjusted score: {adjusted_score}"
    confidence = 0.4

    if adjusted_score >= 30:
        decision = SignalDecision.ALLOW.value
        reason = f"Score yeterli: {adjusted_score:.1f} (ham={signal.score:.1f})"
        confidence = min(0.5 + adjusted_score / 150, 0.95)

    elif adjusted_score >= 15:
        decision = SignalDecision.WATCH.value
        reason = f"Orta score: {adjusted_score:.1f} – izleme"
        confidence = 0.35

    else:
        decision = SignalDecision.VETO.value
        reason = f"Ayarlanmış score düşük: {adjusted_score:.1f}"
        confidence = 0.7

    # Ghost başarı bonusu
    if ghost_stats["ghost_winrate"] >= 65 and ghost_stats["total"] >= 5:
        if decision == SignalDecision.WATCH.value:
            decision = SignalDecision.ALLOW.value
            reason += f" | Ghost boost ({ghost_stats['ghost_winrate']}% WR)"
            confidence = min(confidence + 0.15, 0.95)

    result = AIDecisionResult(
        decision=decision,
        reason=reason,
        confidence=round(confidence, 2),
        score_adjusted=adjusted_score,
        ghost_insight=ghost_insight,
    )

    logger.info(
        "AI karar: %s %s → %s (%.0f%%)  score=%.1f→%.1f  %s",
        signal.symbol, signal.side,
        result.decision, result.confidence * 100,
        signal.score, adjusted_score,
        ghost_insight or "",
    )
    return result


# ── Ghost learning summary ────────────────────────────────────────────

def get_learning_summary() -> dict:
    """Tüm ghost learning istatistiklerini döner."""
    ghost = _get_ghost_manager()
    try:
        conn = sqlite3.connect(ghost.db_path, timeout=5)
        conn.row_factory = sqlite3.Row
        try:
            total = conn.execute(
                "SELECT COUNT(*) FROM signal_candidates"
            ).fetchone()[0]
            tp_hits = conn.execute(
                "SELECT COUNT(*) FROM signal_candidates WHERE status='TP_HIT'"
            ).fetchone()[0]
            sl_hits = conn.execute(
                "SELECT COUNT(*) FROM signal_candidates WHERE status='SL_HIT'"
            ).fetchone()[0]
            pending = total - tp_hits - sl_hits

            # En başarılı semboller
            top_symbols = conn.execute(
                """
                SELECT symbol,
                    SUM(CASE WHEN status='TP_HIT' THEN 1 ELSE 0 END) as wins,
                    SUM(CASE WHEN status='SL_HIT' THEN 1 ELSE 0 END) as losses,
                    COUNT(*) as total
                FROM signal_candidates
                WHERE status IN ('TP_HIT', 'SL_HIT')
                GROUP BY symbol
                HAVING total >= 3
                ORDER BY wins * 1.0 / total DESC
                LIMIT 10
                """
            ).fetchall()

            return {
                "total_candidates": total,
                "tp_hits": tp_hits,
                "sl_hits": sl_hits,
                "pending": pending,
                "ghost_winrate": round(
                    tp_hits / (tp_hits + sl_hits) * 100, 1
                ) if (tp_hits + sl_hits) > 0 else 0.0,
                "top_symbols": [
                    {
                        "symbol": r["symbol"],
                        "wins": r["wins"],
                        "losses": r["losses"],
                        "winrate": round(r["wins"] / r["total"] * 100, 1),
                    }
                    for r in top_symbols
                ],
            }
        finally:
            conn.close()
    except Exception as exc:
        logger.error("Learning summary hatası: %s", exc)
        return {
            "total_candidates": 0, "tp_hits": 0, "sl_hits": 0,
            "pending": 0, "ghost_winrate": 0.0, "top_symbols": [],
        }
<<<<<<< HEAD
=======

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
        except Exception as e:
            logger.debug(f"Paper outcome learn atlandı: {e}")

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
>>>>>>> 0797c70b8640d2006e47a50d5580ffae4606199b
