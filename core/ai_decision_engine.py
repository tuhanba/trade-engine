"""
core/ai_decision_engine.py — AX AI Brain v5.0 (PAPER-ONLY / LIVE-BLOCKED)
===========================================================
FAZ 10: Ghost Learning + Coin Personality + Expectancy

Sistem açtığı VE açmadığı trade'lerden öğrenir.
Tüm sinyaller kaydedilir: ALLOW, WATCH, VETO, SKIPPED_BY_RISK, etc.

Ghost learning:
- WATCH/VETO/SKIPPED sinyalleri paper outcome ile izlenir.
- TP1 stop'tan önce gelirse GHOST_WIN.
- Stop TP1'den önce gelirse GHOST_LOSS.
- Ağırlık: Gerçek=1.0, Ghost=0.3, Son 30 gün ağır.

Coin profile:
- win_rate, avg_R, profit_factor, TP1/TP2 hit rate, danger_score, etc.
"""
import logging
import sqlite3
import json
import math
from datetime import datetime, timezone, timedelta

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
                    event TEXT, symbol TEXT, decision TEXT,
                    score REAL, confidence REAL, reason TEXT, data TEXT,
                    timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )""")
                conn.execute("""CREATE TABLE IF NOT EXISTS coin_personality (
                    symbol TEXT PRIMARY KEY,
                    win_rate REAL DEFAULT 0,
                    avg_pnl REAL DEFAULT 0,
                    avg_r REAL DEFAULT 0,
                    profit_factor REAL DEFAULT 0,
                    tp1_hit_rate REAL DEFAULT 0,
                    tp2_hit_rate REAL DEFAULT 0,
                    runner_contribution REAL DEFAULT 0,
                    fakeout_rate REAL DEFAULT 0,
                    fee_drag REAL DEFAULT 0,
                    best_hour INTEGER,
                    long_bias REAL DEFAULT 0.5,
                    danger_score REAL DEFAULT 0,
                    sample_size INTEGER DEFAULT 0,
                    ghost_win_rate REAL DEFAULT 0,
                    ghost_sample INTEGER DEFAULT 0,
                    volatility_rank INTEGER DEFAULT 3,
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )""")
                conn.commit()
        except Exception as e:
            logger.error(f"AI DB init hatası: {e}")

    def decide(self, signal_data) -> tuple:
        """Sinyali analiz eder ve karar verir. Returns (decision, reason)."""
        if isinstance(signal_data, dict):
            symbol = signal_data.get("symbol", "")
            score = signal_data.get("final_score", 0)
            confidence = signal_data.get("confidence", 0.8)
            quality = signal_data.get("setup_quality", "C")
        else:
            symbol = getattr(signal_data, 'symbol', "")
            score = getattr(signal_data, 'final_score', 0)
            confidence = getattr(signal_data, 'confidence', 0.8)
            quality = getattr(signal_data, 'setup_quality', "C")

        # Coin personality kontrolü
        coin_data = self._get_coin_personality(symbol)
        danger = coin_data.get("danger_score", 0) if coin_data else 0
        win_rate = coin_data.get("win_rate", 0) if coin_data else 0.5
        ghost_wr = coin_data.get("ghost_win_rate", 0) if coin_data else 0.5
        sample = (coin_data.get("sample_size", 0) + coin_data.get("ghost_sample", 0)) if coin_data else 0

        # AI Expectancy Score (Öğrenme tabanlı beklenti)
        # Sistemi tamamen benzersiz kılan self-optimizing (kendi kendini ayarlayan) eşik mekanizması
        dynamic_threshold = 7.0
        blended_wr = 0.5
        
        if sample >= 5:
            # Gerçek trade ağırlıklı (%70), Ghost trade ağırlıklı (%30) karma win_rate
            blended_wr = (win_rate * 0.7) + (ghost_wr * 0.3)
            
            # Öğrendikçe Karar Mekanizmasını Esnet veya Katılaştır:
            if blended_wr >= 0.65:
                dynamic_threshold = 5.5  # Çok kârlı coin, eşiği esnet
            elif blended_wr >= 0.55:
                dynamic_threshold = 6.0  # Karlı coin
            elif blended_wr <= 0.35:
                dynamic_threshold = 8.5  # Çok zararlı coin, mükemmel değilse girme
            elif blended_wr <= 0.45:
                dynamic_threshold = 7.5  # Zararlı coin, katı kurallar

        # Karar Mantığı
        if danger > 0.8:
            decision = "VETO"
            reason = f"Yüksek risk (Danger Score: {danger:.2f})"
        elif score >= dynamic_threshold and quality in ("S", "A+", "A", "B"):
            if sample >= 5:
                reason = f"AI Edge: Score={score:.1f} (Hedef: {dynamic_threshold:.1f}) | WR={blended_wr*100:.1f}%"
            else:
                reason = f"Score={score:.1f} Quality={quality}"
            decision = "ALLOW"
        elif score >= 5.0:
            decision = "WATCH"
            reason = f"Ghost tracking: score={score:.1f} (Hedef={dynamic_threshold:.1f})"
        else:
            decision = "VETO"
            reason = f"Düşük skor: {score:.1f} (Gerekli: {dynamic_threshold:.1f})"

        # Log
        self._log("DECISION", symbol, decision, score, confidence, reason, signal_data)
        return decision, reason

    def evaluate(self, signal_data):
        """Uyumluluk wrapper — decide() çağırır."""
        decision, reason = self.decide(signal_data)
        return {"decision": decision, "reason": reason}

    def learn_from_outcome(self, symbol, pnl, reason):
        """Kapanan gerçek trade'den öğren (weight=1.0)."""
        self._update_personality(symbol, pnl, weight=1.0, source="real")

    def learn_from_trade(self, symbol, result, pnl, setup_quality="B"):
        """Gerçek trade sonucu öğren."""
        self._update_personality(symbol, pnl, weight=1.0, source="real")
        self._log("LEARN_REAL", symbol, result, pnl, 0, f"quality={setup_quality}", {})

    def learn_from_paper_outcome(self, symbol, tracked_from, would_have_won,
                                  mfe_r=0, mae_r=0, first_touch="",
                                  skip_correct=0):
        """Ghost/paper outcome'dan öğren (weight=0.3)."""
        ghost_pnl = 1.0 if would_have_won else -1.0
        self._update_personality(symbol, ghost_pnl, weight=0.3, source="ghost")

        # Ghost win/loss log
        result = "GHOST_WIN" if would_have_won else "GHOST_LOSS"
        if first_touch == "neither_horizon":
            result = "EXPIRED"

        self._log("LEARN_GHOST", symbol, result, ghost_pnl, 0,
                  f"tracked={tracked_from} mfe_r={mfe_r:.2f} mae_r={mae_r:.2f}", {})

    def _update_personality(self, symbol, pnl, weight=1.0, source="real"):
        """Coin personality güncelle — EMA benzeri decay."""
        try:
            with sqlite3.connect(self.db_path) as conn:
                conn.row_factory = sqlite3.Row
                row = conn.execute(
                    "SELECT * FROM coin_personality WHERE symbol=?", (symbol,)
                ).fetchone()

                decay = 0.95  # Son veriler daha ağır
                is_win = 1 if pnl > 0 else 0

                if row:
                    old_wr = row["win_rate"] or 0
                    old_pnl = row["avg_pnl"] or 0
                    sample = (row["sample_size"] or 0)
                    ghost_sample = row["ghost_sample"] or 0
                    ghost_wr = row["ghost_win_rate"] or 0

                    if source == "real":
                        new_wr = old_wr * decay + is_win * (1 - decay) * weight
                        new_pnl = old_pnl * decay + pnl * (1 - decay) * weight
                        sample += 1
                    else:
                        new_wr = old_wr
                        new_pnl = old_pnl
                        ghost_wr = ghost_wr * decay + is_win * (1 - decay) * weight
                        ghost_sample += 1

                    # Danger score (düşük win_rate + yüksek sample = tehlikeli)
                    danger = 0
                    if sample >= 5 and new_wr < 0.35:
                        danger = min(1.0, (0.35 - new_wr) * 3)

                    conn.execute("""
                        UPDATE coin_personality SET
                        win_rate=?, avg_pnl=?, sample_size=?,
                        ghost_win_rate=?, ghost_sample=?,
                        danger_score=?, updated_at=CURRENT_TIMESTAMP
                        WHERE symbol=?
                    """, (round(new_wr, 4), round(new_pnl, 4), sample,
                          round(ghost_wr, 4), ghost_sample,
                          round(danger, 4), symbol))
                else:
                    conn.execute("""
                        INSERT INTO coin_personality
                        (symbol, win_rate, avg_pnl, sample_size,
                         ghost_win_rate, ghost_sample, danger_score)
                        VALUES (?, ?, ?, ?, ?, ?, ?)
                    """, (symbol, round(is_win * weight, 4), round(pnl * weight, 4),
                          1 if source == "real" else 0,
                          round(is_win * weight, 4) if source == "ghost" else 0,
                          1 if source == "ghost" else 0, 0))

        except Exception as e:
            logger.error(f"AI personality update hatası: {e}")

    def _get_coin_personality(self, symbol):
        try:
            with sqlite3.connect(self.db_path) as conn:
                conn.row_factory = sqlite3.Row
                row = conn.execute(
                    "SELECT * FROM coin_personality WHERE symbol=?", (symbol,)
                ).fetchone()
                return dict(row) if row else None
        except Exception:
            return None

    def get_expectancy_report(self, days=30):
        """FAZ 11: Expectancy raporu."""
        try:
            with sqlite3.connect(self.db_path) as conn:
                conn.row_factory = sqlite3.Row
                since = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()

                rows = conn.execute("""
                    SELECT net_pnl, r_multiple, setup_quality, direction,
                           symbol, tp1_hit, tp2_hit
                    FROM trades
                    WHERE status='closed' AND is_valid_for_stats=1
                    AND close_time >= ?
                """, (since,)).fetchall()

                if not rows:
                    return {"expectancy": 0, "total": 0}

                wins = [r for r in rows if r["net_pnl"] > 0]
                losses = [r for r in rows if r["net_pnl"] <= 0]
                total = len(rows)
                win_rate = len(wins) / total if total > 0 else 0
                avg_win = sum(r["net_pnl"] for r in wins) / len(wins) if wins else 0
                avg_loss = abs(sum(r["net_pnl"] for r in losses) / len(losses)) if losses else 0

                expectancy = win_rate * avg_win - (1 - win_rate) * avg_loss

                tp1_hits = sum(1 for r in rows if r["tp1_hit"])
                tp2_hits = sum(1 for r in rows if r["tp2_hit"])

                return {
                    "expectancy": round(expectancy, 4),
                    "total": total,
                    "win_rate": round(win_rate, 4),
                    "avg_win": round(avg_win, 4),
                    "avg_loss": round(avg_loss, 4),
                    "tp1_hit_rate": round(tp1_hits / total, 4) if total > 0 else 0,
                    "tp2_hit_rate": round(tp2_hits / total, 4) if total > 0 else 0,
                    "avg_r": round(sum(r["r_multiple"] or 0 for r in rows) / total, 3) if total else 0,
                }
        except Exception as e:
            logger.error(f"Expectancy report hatası: {e}")
            return {"error": str(e)}

    def _log(self, event, symbol, decision, score, confidence, reason, data):
        try:
            with sqlite3.connect(self.db_path) as conn:
                conn.execute("""
                    INSERT INTO ai_logs (event, symbol, decision, score, confidence, reason, data)
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                """, (event, symbol, decision, score, confidence, reason,
                      json.dumps(data) if isinstance(data, dict) else str(data)))
        except Exception as e:
            logger.debug(f"AI log hatası: {e}")
