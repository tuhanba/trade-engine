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
from dataclasses import dataclass
from datetime import datetime, timezone, timedelta
from typing import Optional

from core.data_layer import SignalData, SignalDecision

logger = logging.getLogger("ax.ai_decision")


# ── AI Decision Result ───────────────────────────────────────────────

@dataclass
class AIDecisionResult:
    """AI karar sonucu."""
    decision: str = SignalDecision.WATCH.value
    reason: str = ""
    confidence: float = 0.0
    score_adjusted: float = 0.0
    ghost_insight: str = ""


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

    # ── Score çok düşük ──────────────────────────────────────────
    if signal.score < 5:
        return AIDecisionResult(
            decision=SignalDecision.VETO.value,
            reason=f"Ham score çok düşük: {signal.score}",
            confidence=0.9,
        )

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
