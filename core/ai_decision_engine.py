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
import json
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone, timedelta
from typing import Optional
from collections import defaultdict

from core.data_layer import SignalData, SignalDecision

try:
    import sys as _sys, os as _os
    _sys.path.insert(0, _os.path.dirname(_os.path.dirname(_os.path.abspath(__file__))))
    from database import open_db as _open_db
except Exception:
    def _open_db(db_path=None, timeout=15):  # type: ignore[misc]
        if db_path is None:
            try:
                import config as _cfg
                db_path = _cfg.DB_PATH
            except Exception:
                db_path = "trading.db"
        conn = sqlite3.connect(db_path, timeout=timeout)
        conn.row_factory = sqlite3.Row
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA synchronous=NORMAL")
        return conn

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


# ── AIDecisionEngine constants ───────────────────────────────────────

try:
    import config
    _BAD_HOURS = getattr(config, 'BAD_HOURS_UTC', [])
    _GOOD_HOURS = getattr(config, 'GOOD_HOURS_UTC', [])
    MIN_TRADES_FOR_RISK_UPDATE = 10
    MIN_CANDIDATES_FOR_THRESHOLD_UPDATE = 20
    MIN_CANDIDATES_FOR_COIN_LEARNING = 5
    PARAM_BOUNDS = {"sl_atr_mult": (0.8, 3.0), "tp_atr_mult": (1.0, 5.0), "risk_pct": (0.3, 3.0)}
    def clamp(v, lo, hi): return max(lo, min(hi, v))
except Exception:
    _BAD_HOURS = []; _GOOD_HOURS = []
    MIN_TRADES_FOR_RISK_UPDATE = 10; MIN_CANDIDATES_FOR_THRESHOLD_UPDATE = 20
    MIN_CANDIDATES_FOR_COIN_LEARNING = 5
    PARAM_BOUNDS = {"sl_atr_mult": (0.8, 3.0), "tp_atr_mult": (1.0, 5.0), "risk_pct": (0.3, 3.0)}
    def clamp(v, lo, hi): return max(lo, min(hi, v))


class DynamicThresholds:
    def __init__(self, fallback_dict):
        self.fallback = fallback_dict

    def __getitem__(self, key):
        try:
            import config
            if key == "trade":
                return getattr(config, "TRADE_THRESHOLD", self.fallback["trade"])
            elif key == "data":
                return getattr(config, "DATA_THRESHOLD", self.fallback["data"])
            elif key == "watchlist":
                return getattr(config, "WATCHLIST_THRESHOLD", self.fallback["watchlist"])
            elif key == "telegram":
                return getattr(config, "TELEGRAM_THRESHOLD", self.fallback["telegram"])
        except Exception:
            pass
        return self.fallback.get(key)

    def __setitem__(self, key, value):
        self.fallback[key] = value

    def get(self, key, default=None):
        val = self[key]
        return val if val is not None else default


# ── AIDecisionEngine ─────────────────────────────────────────────────

class AIDecisionEngine:
    def __init__(self, db_path=None):
        try:
            import config as _cfg
            self.db_path = db_path or _cfg.DB_PATH
        except Exception:
            self.db_path = db_path or "trading.db"
        self.daily_signals = 0
        self.max_daily_signals = 40  # Backtest: kalite > miktar
        self.recent_coins = []
        self.last_reset_date = datetime.now(timezone.utc).date()
        self.thresholds = DynamicThresholds({
            "data": 55.0,
            "watchlist": 60.0,
            "telegram": 65.0,
            "trade": 72.0,
        })

        # Dinamik parametreler
        self.params = {
            "sl_atr_mult": 1.5,
            "tp_atr_mult": 2.0,
            "risk_pct": 1.0
        }

        self._init_db()
        self._load_best_params()

    def _init_db(self):
        pass

    def _load_best_params(self):
        try:
            with _open_db(self.db_path) as conn:
                best = conn.execute("SELECT * FROM best_params ORDER BY profit_factor DESC LIMIT 1").fetchone()
                if best:
                    self.params = json.loads(best["params_json"])
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
            with _open_db(self.db_path) as conn:
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
            with _open_db(self.db_path) as conn:
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

    def _optimize_params(self):
        """Geçmiş işlemlere göre parametreleri optimize eder."""
        try:
            with _open_db(self.db_path) as conn:
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
            with _open_db(self.db_path) as conn:
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
            with _open_db(self.db_path) as conn:
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
            self._update_threshold_from_ghost()

        except Exception as e:
            logger.error(f"AI öğrenme hatası: {e}")

    def _update_threshold_from_ghost(self):
        """
        Ghost sonuclarina gore TRADE_THRESHOLD'u adaptif ayarla.
        ghost_win_rate > 0.65 -> threshold 2 puan dusur (daha cok trade)
        ghost_win_rate < 0.35 -> threshold 2 puan artir (daha secici)
        Degisim ai_learning tablosuna log'lanir.
        """
        try:
            from core.ghost_learning import get_ghost_learning_stats
            stats = get_ghost_learning_stats()
            total = stats.get("total", 0)
            if total < 20:
                return  # Yetersiz veri

            gwr = stats.get("ghost_win_rate", 0)
            old_threshold = self.thresholds["trade"]
            new_threshold = old_threshold

            if gwr > 0.65:
                new_threshold = max(60.0, old_threshold - 2.0)
            elif gwr < 0.35:
                new_threshold = min(85.0, old_threshold + 2.0)

            if new_threshold == old_threshold:
                return

            self.thresholds["trade"] = new_threshold
            with _open_db(self.db_path) as conn:
                conn.execute(
                    """INSERT INTO ai_learning (symbol, trade_result, pnl, setup_quality)
                       VALUES ('SYSTEM', 'THRESHOLD_UPDATE', 0.0, ?)""",
                    (
                        json.dumps({
                            "old_threshold": old_threshold,
                            "new_threshold": new_threshold,
                            "ghost_win_rate": round(gwr, 4),
                            "ghost_total": total,
                        }),
                    ),
                )
                # system_state tablosuna da yaz (dinamik config için)
                conn.execute("""
                    INSERT INTO system_state (key, value, updated_at)
                    VALUES ('trade_threshold', ?, datetime('now'))
                    ON CONFLICT(key) DO UPDATE SET value=?, updated_at=datetime('now')
                """, (str(new_threshold), str(new_threshold)))
                conn.commit()
            logger.info(
                f"[AI] TRADE_THRESHOLD {old_threshold:.1f} -> {new_threshold:.1f} "
                f"(ghost_wr={gwr:.1%} n={total})"
            )
        except Exception as e:
            logger.debug(f"[AI] _update_threshold_from_ghost atlandı: {e}")

    def evaluate(self, sig) -> dict:
        """
        scalp_bot.py line 436: decision = ai_engine.evaluate(sig)
        classify_signal() fonksiyonunu çağırır, scalp_bot'un beklediği
        formatta dict döner.
        """
        try:
            if hasattr(sig, 'entry_zone') and (sig.entry_zone or 0) > 0:
                sig.entry_price = sig.entry_zone
            # 0-10 ölçeğini 0-100'e normalize et: *4, *3, *3
            _trigger = float(getattr(sig, 'trigger_score', 0) or 0)
            _trend   = float(getattr(sig, 'trend_score',   0) or 0)
            _risk    = float(getattr(sig, 'risk_score',    0) or 0)
            # BUG FIX: 0-100 ölçeğinden gelenler varsa normalize et
            _max_score = max(_trigger, _trend, _risk)
            if _max_score > 10:
                _trigger = _trigger / 10.0
                _trend   = _trend   / 10.0
                _risk    = _risk    / 10.0
            sig.score = round(
                min(100.0,
                    _trigger * 4.0 +   # 0-10 → 0-40
                    _trend   * 3.0 +   # 0-10 → 0-30
                    _risk    * 3.0),   # 0-10 → 0-30
                1
            )
            try:
                from database import get_market_regime as _get_regime
                _regime = _get_regime()
            except Exception:
                _regime = "NEUTRAL"
            _ctx = {
                "confluence_score": float(getattr(sig, "confluence_score", 2) or 2),
                "market_regime":    _regime,
            }
            result = classify_signal(sig, _ctx)
            return {
                "decision":    result.decision,
                "final_score": float(result.score_adjusted or sig.score or 50.0),
                "confidence":  float(result.confidence or 0.5),
                "reason":      result.reason or "",
                "ai_score":    float(result.score_adjusted or sig.score or 50.0),
            }
        except Exception as e:
            logger.warning("evaluate() fallback: %s", e)
            score = float(getattr(sig, 'score', 0) or 0)
            if score <= 0:
                score = 50.0
            return {
                "decision":    "ALLOW" if score >= 30 else "VETO",
                "final_score": score,
                "confidence":  0.5,
                "reason":      f"fallback_evaluate: {e}",
                "ai_score":    score,
            }

    def learn_from_outcome(self, symbol: str, net_pnl: float, reason: str):
        """
        Trade sonucundan öğren. Coin profilini tam olarak güncelle.
        execution_engine._finalize() tarafından çağrılır.
        """
        try:
            with _open_db(self.db_path) as conn:
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
            self._update_threshold_from_ghost()
        except Exception as e:
            logger.warning(f"[AI] learn_from_outcome hatası {symbol}: {e}")


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
            conn = _open_db(self.db_path, timeout=15)
            try:
                rows = conn.execute(
                    """
                    SELECT virtual_outcome as status, COUNT(*) as cnt
                    FROM ghost_results
                    WHERE symbol = ? AND evaluated_at >= ?
                    AND virtual_outcome IN ('WIN', 'LOSS')
                    GROUP BY virtual_outcome
                    """,
                    (symbol, cutoff),
                ).fetchall()

                tp_hits = 0
                sl_hits = 0
                for r in rows:
                    if r["status"] == "WIN":
                        tp_hits = r["cnt"]
                    elif r["status"] == "LOSS":
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
            conn = _open_db(self.db_path, timeout=15)
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
        Dinamik ghost ağırlığı ile etki büyüklüğü ölçeklenir.
        Range: 0.5 – 1.5
        """
        stats = self.get_symbol_ghost_stats(symbol)
        if stats["total"] < 3:
            return 1.0  # Yetersiz veri

        wr = stats["ghost_winrate"]
        if wr >= 70:
            raw_mult = 1.3
        elif wr >= 55:
            raw_mult = 1.1
        elif wr <= 30:
            raw_mult = 0.7
        elif wr <= 45:
            raw_mult = 0.85
        else:
            return 1.0

        # Dinamik ağırlık: ghost ne kadar güvenilirse etki o kadar büyük.
        # weight=0.15 → etki %15, weight=0.45 → etki tam
        try:
            from core.ghost_learning import calculate_dynamic_ghost_weight
            weight = calculate_dynamic_ghost_weight()
        except Exception:
            weight = 0.30
        # Çarpanı 1.0 etrafında ağırlıklı olarak ölçekle
        return round(1.0 + (raw_mult - 1.0) * (weight / 0.45), 4)


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

        # Min sample guard — yetersiz veriyle çarpan uygulama
        try:
            _ghost_stats = self.ghost.get_symbol_ghost_stats(signal.symbol)
            total_trades = _ghost_stats.get("total", 0) or 0
        except Exception:
            total_trades = 0

        # 1. Ghost multiplier
        if total_trades < 10:
            logger.debug(f"[Adaptive] {signal.symbol}: yetersiz ghost verisi ({total_trades}<10), ghost_mult=1.0 kullanılıyor")
            ghost_mult = 1.0
        else:
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

        # 6. ML score factor (0-100, 50=neutral)
        ml_score = float(getattr(signal, "ml_score", 50.0) or 50.0)
        if ml_score >= 70:
            adjusted *= 1.15
        elif ml_score >= 60:
            adjusted *= 1.08
        elif ml_score <= 30:
            adjusted *= 0.85
        elif ml_score <= 40:
            adjusted *= 0.92
        if ml_score != 50:
            logger.debug(
                "[ML] %s ml_score=%.0f → adjusted=%.1f",
                getattr(signal, "symbol", "?"), ml_score, adjusted,
            )

        # 7. Confluence score factor (0-4 TF alignment)
        confluence = float(
            getattr(signal, "confluence_score", None)
            or context.get("confluence_score", 2)
        )
        if confluence == 4:
            adjusted *= 1.15
            logger.debug("[Confluence] 4/4 aligned → +15%%")
        elif confluence == 3:
            adjusted *= 1.08
        elif confluence <= 1:
            adjusted *= 0.85
            logger.debug("[Confluence] %d/4 aligned → -15%%", int(confluence))

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

    # ── Portföy Korelasyon Kalkanı (Faz 5) ──────────────────────────
    try:
        from database import get_open_trades
        open_trades = get_open_trades()
        same_direction_count = sum(1 for t in open_trades if (t.get("direction") or t.get("side", "LONG")) == signal.side)
        if same_direction_count >= 3:
            # 3'ten fazla aynı yönde pozisyon var, risk yarıya düşürülmeli veya VETO verilmeli
            if adjusted_score < 75:
                return AIDecisionResult(
                    decision=SignalDecision.VETO.value,
                    reason=f"Korelasyon Kalkanı: Zaten {same_direction_count} adet {signal.side} pozisyon açık",
                    confidence=0.8,
                )
            else:
                # Skor iyiyse riski düşürtüp izin ver
                signal.risk_pct *= 0.5
    except Exception as e:
        logger.debug(f"[AI] Korelasyon kalkanı atlandı: {e}")

    # ── Macro Sentiment Kalkanı (Faz 6) ────────────────────────────────
    try:
        from core.services.macro_service import macro_service
        sentiment = macro_service.get_market_sentiment()
        fng_val = sentiment.get("fng_value", 50)
        
        # Aşırı korku varsa LONG işlemlere kısıtlama
        if fng_val < 25 and signal.side == "LONG":
            if adjusted_score < 40:
                return AIDecisionResult(
                    decision=SignalDecision.VETO.value,
                    reason=f"Macro Kalkanı: Piyasa Aşırı Korku seviyesinde (FNG={fng_val}). LONG işlemi reddedildi.",
                    confidence=0.9,
                )
            else:
                signal.risk_pct *= 0.5 # Riski yarıya düşür
                
        # Aşırı coşku varsa SHORT işlemlere kısıtlama
        elif fng_val > 75 and signal.side == "SHORT":
            if adjusted_score < 40:
                return AIDecisionResult(
                    decision=SignalDecision.VETO.value,
                    reason=f"Macro Kalkanı: Piyasa Aşırı Açgözlülük seviyesinde (FNG={fng_val}). SHORT işlemi reddedildi.",
                    confidence=0.9,
                )
            else:
                signal.risk_pct *= 0.5
    except Exception as e:
        logger.debug(f"[AI] Macro kalkanı atlandı: {e}")

    # ── Piyasa Rejimi Filtresi ────────────────────────────────────────
    try:
        from database import get_market_regime as _get_regime
        regime = _get_regime()
    except Exception:
        regime = ctx.get("market_regime", "NEUTRAL")

    side_upper = str(getattr(signal, "side", "") or getattr(signal, "direction", "")).upper()

    if regime == "CHOPPY":
        setup_quality = str(getattr(signal, "setup_quality", "") or "").upper()
        
        import config
        is_scalp_mode = not getattr(config, "HUMAN_MODE", False)
        confluence = float(ctx.get("confluence_score", getattr(signal, "confluence_score", 0)) or 0)
        
        if is_scalp_mode and setup_quality in ("A", "B", "C") and confluence >= 1:
            logger.debug("[Regime] CHOPPY'de Scalp Modu istisnası: A/B kalite + Confluence kabul edildi.")
        elif setup_quality not in ("S", "A+", "A", "B"):
            return AIDecisionResult(
                decision=SignalDecision.VETO.value,
                reason=f"CHOPPY piyasa — {setup_quality} kalite yetersiz (min A+)",
                confidence=0.85,
                score_adjusted=adjusted_score,
            )
    elif regime == "BULLISH" and side_upper == "SHORT":
        adjusted_score = round(adjusted_score * 0.88, 1)
        logger.debug("[Regime] BULLISH + SHORT → score *0.88 → %.1f", adjusted_score)
    elif regime == "BEARISH" and side_upper == "LONG":
        adjusted_score = round(adjusted_score * 0.88, 1)
        logger.debug("[Regime] BEARISH + LONG → score *0.88 → %.1f", adjusted_score)
    # ─────────────────────────────────────────────────────────────────

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
    if ghost_stats["ghost_winrate"] >= 65 and ghost_stats["total"] >= 15:  # min 15 sample
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

    # ── Ghost Learning hook ───────────────────────────────────────────
    # VETO veya WATCH kararında ghost_signals'a kaydet (tüm çağırıcılar için)
    if result.decision in (SignalDecision.VETO.value, SignalDecision.WATCH.value):
        try:
            from core.ghost_learning import maybe_ghost_log
            maybe_ghost_log(
                {
                    "symbol":       signal.symbol,
                    "direction":    signal.side,
                    "entry":        signal.entry_price,
                    "sl":           signal.stop_loss,
                    "tp1":          getattr(signal, "tp1", None) or 0,
                    "confidence":   result.confidence,
                    "trigger_type": getattr(signal, "trigger_type", "UNKNOWN"),
                    "final_score":  result.score_adjusted,
                },
                reason=result.decision,
            )
        except Exception as _ghost_err:
            logger.debug("[classify_signal] ghost log atlandı: %s", _ghost_err)
    # ─────────────────────────────────────────────────────────────────

    return result


# ── Ghost learning summary ────────────────────────────────────────────

def get_learning_summary() -> dict:
    """Tüm ghost learning istatistiklerini döner."""
    ghost = _get_ghost_manager()
    try:
        conn = _open_db(ghost.db_path, timeout=15)
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
