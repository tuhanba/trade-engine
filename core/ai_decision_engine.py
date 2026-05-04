"""
AI Decision Engine - Profesyonel Surum v4.3
===========================================
Final karar katmani. Sinyalleri onaylar veya reddeder.
Ozellikler:
  - Coin-specific ogrenme (her coin icin ayri istatistik)
  - Markov zinciri (son N trade sonucuna gore dinamik threshold)
  - Saat filtresi (BAD_HOURS_UTC)
  - Gunluk sinyal limiti
  - ALLOW / WATCH / VETO kararlari
"""
import logging
import sqlite3
import json
from datetime import datetime, timedelta, timezone
from collections import defaultdict

logger = logging.getLogger(__name__)

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
    )
except ImportError:
    _BAD_HOURS   = []
    _GOOD_HOURS  = list(range(24))
    _ALLOWED_QUAL = ["S", "A+", "A", "B", "C"]
    DATA_THRESHOLD      = 30
    WATCHLIST_THRESHOLD = 50
    TELEGRAM_THRESHOLD  = 60
    TRADE_THRESHOLD     = 70


class AIDecisionEngine:
    def __init__(self, db_path="trading.db"):
        self.db_path = db_path
        self.daily_signals   = 0
        self.max_daily_signals = 40
        self.recent_coins    = []
        self.last_reset_date = datetime.now(timezone.utc).date()
        self.thresholds = {
            "data":      DATA_THRESHOLD,
            "watchlist": WATCHLIST_THRESHOLD,
            "telegram":  TELEGRAM_THRESHOLD,
            "trade":     TRADE_THRESHOLD,
        }
        self.params = {"sl_atr_mult": 1.5, "tp_atr_mult": 2.0, "risk_pct": 1.0}
        self._init_db()

    # ─────────────────────────────────────────────────────────────────────────
    # DB INIT
    # ─────────────────────────────────────────────────────────────────────────
    def _init_db(self):
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
                    total_pnl REAL DEFAULT 0,
                    danger_score REAL DEFAULT 0,
                    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )""")
                conn.execute("""CREATE TABLE IF NOT EXISTS ai_postmortem (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    trade_id INTEGER,
                    symbol TEXT,
                    result TEXT,
                    pnl REAL,
                    setup_quality TEXT,
                    hold_minutes REAL DEFAULT 0,
                    mfe_r REAL DEFAULT 0,
                    mae_r REAL DEFAULT 0,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )""")
                conn.commit()
        except Exception as e:
            logger.error(f"AI DB init hatasi: {e}")

    # ─────────────────────────────────────────────────────────────────────────
    # COIN-SPECIFIC ISTATISTIK
    # ─────────────────────────────────────────────────────────────────────────
    def _get_coin_stats(self, symbol):
        """Coin bazli win_rate, total_trades, danger_score doner."""
        try:
            with sqlite3.connect(self.db_path) as conn:
                row = conn.execute(
                    "SELECT win_rate, total_trades, total_pnl, danger_score FROM coin_profiles WHERE symbol=?",
                    (symbol,)
                ).fetchone()
                if row:
                    return {
                        "win_rate":     row[0] or 0,
                        "total_trades": row[1] or 0,
                        "total_pnl":    row[2] or 0,
                        "danger_score": row[3] or 0,
                    }
        except Exception:
            pass
        return {"win_rate": 0, "total_trades": 0, "total_pnl": 0, "danger_score": 0}

    def _get_recent_results(self, symbol, n=5):
        """Son N trade sonucunu doner (coin-specific)."""
        try:
            with sqlite3.connect(self.db_path) as conn:
                rows = conn.execute(
                    """SELECT trade_result FROM ai_learning
                       WHERE symbol=?
                       ORDER BY created_at DESC LIMIT ?""",
                    (symbol, n)
                ).fetchall()
                return [r[0] for r in rows]
        except Exception:
            return []

    def _markov_adjustment(self, symbol):
        """
        Son 3 trade sonucuna gore dinamik threshold ayarlamasi.
        3 ust uste LOSS: +10 puan (daha secici)
        3 ust uste WIN:  -5 puan (daha agresif)
        """
        results = self._get_recent_results(symbol, n=3)
        if len(results) < 3:
            return 0
        if all(r == "LOSS" for r in results):
            return 10   # Daha secici
        if all(r == "WIN" for r in results):
            return -5   # Daha agresif
        return 0

    # ─────────────────────────────────────────────────────────────────────────
    # GUNLUK LIMIT RESET
    # ─────────────────────────────────────────────────────────────────────────
    def _check_daily_reset(self):
        today = datetime.now(timezone.utc).date()
        if today != self.last_reset_date:
            self.daily_signals   = 0
            self.last_reset_date = today
            logger.info("[AI] Gunluk sinyal sayaci sifirlandı")

    # ─────────────────────────────────────────────────────────────────────────
    # ANA KARAR MOTORU
    # ─────────────────────────────────────────────────────────────────────────
    def evaluate(self, signal_data):
        """
        Sinyal degerlendir.
        Returns:
            {
                "decision": "ALLOW" | "WATCH" | "VETO",
                "final_score": float,
                "reason": str,
            }
        """
        self._check_daily_reset()
        symbol  = signal_data.symbol
        quality = getattr(signal_data, "setup_quality", "B")

        # ── 1. Saat Filtresi ─────────────────────────────────────────────────
        current_hour = datetime.now(timezone.utc).hour
        if _BAD_HOURS and current_hour in _BAD_HOURS:
            return self._veto(f"bad_hour:{current_hour}")

        # ── 2. Kalite Filtresi ───────────────────────────────────────────────
        if quality not in _ALLOWED_QUAL:
            return self._veto(f"quality_filtered:{quality}")

        # ── 3. Gunluk Limit ──────────────────────────────────────────────────
        if self.daily_signals >= self.max_daily_signals:
            return self._veto("daily_limit_reached")

        # ── 4. Coin Danger Score ─────────────────────────────────────────────
        coin_stats = self._get_coin_stats(symbol)
        danger     = coin_stats["danger_score"]
        if danger >= 80:
            return self._veto(f"danger_score:{danger:.0f}")

        # ── 5. Skor Hesabi ───────────────────────────────────────────────────
        coin_score    = getattr(signal_data, "coin_score",    0) or 0
        trend_score   = getattr(signal_data, "trend_score",   0) or 0
        trigger_score = getattr(signal_data, "trigger_score", 0) or 0
        risk_score    = getattr(signal_data, "risk_score",    0) or 0
        ml_score      = getattr(signal_data, "ml_score",      50) or 50
        confidence    = getattr(signal_data, "confidence",    0.8) or 0.8

        # Agirlikli skor (toplam 100)
        base_score = (
            coin_score    * 0.10 +
            trend_score   * 0.30 +
            trigger_score * 0.30 +
            risk_score    * 0.20 +
            (ml_score / 100) * 10
        ) * 10

        # Coin win_rate bonusu (coin-specific ogrenme)
        win_rate = coin_stats["win_rate"]
        if coin_stats["total_trades"] >= 5:
            if win_rate >= 65:
                base_score += 5   # Iyi gecmisi olan coin
            elif win_rate <= 35:
                base_score -= 10  # Kotu gecmisi olan coin

        # Danger score cezasi
        if danger > 50:
            base_score -= (danger - 50) * 0.2

        # Confidence carpani
        base_score *= confidence

        final_score = max(0.0, min(100.0, base_score))

        # ── 6. Markov Dinamik Threshold ──────────────────────────────────────
        markov_adj  = self._markov_adjustment(symbol)
        trade_thr   = self.thresholds["trade"]   + markov_adj
        watch_thr   = self.thresholds["telegram"] + markov_adj

        # ── 7. Karar ─────────────────────────────────────────────────────────
        if final_score >= trade_thr:
            self.daily_signals += 1
            decision = "ALLOW"
            reason   = f"score:{final_score:.1f} >= trade_thr:{trade_thr:.0f}"
        elif final_score >= watch_thr:
            decision = "WATCH"
            reason   = f"score:{final_score:.1f} >= watch_thr:{watch_thr:.0f}"
        else:
            decision = "VETO"
            reason   = f"score:{final_score:.1f} < watch_thr:{watch_thr:.0f}"

        logger.info(
            f"[AI] {symbol} {decision} score={final_score:.1f} "
            f"quality={quality} danger={danger:.0f} markov_adj={markov_adj:+d}"
        )
        return {
            "decision":    decision,
            "final_score": round(final_score, 1),
            "reason":      reason,
        }

    def _veto(self, reason):
        logger.info(f"[AI] VETO: {reason}")
        return {"decision": "VETO", "final_score": 0.0, "reason": reason}

    # ─────────────────────────────────────────────────────────────────────────
    # OGRENME
    # ─────────────────────────────────────────────────────────────────────────
    def learn_from_trade(self, symbol, result, pnl, setup_quality="B",
                         hold_minutes=0, mfe_r=0, mae_r=0, trade_id=None):
        """
        Trade kapaninca AI'a ogret.
        - ai_learning tablosuna yazar (coin-specific)
        - coin_profiles tablosunu gunceller
        - ai_postmortem tablosuna yazar
        """
        try:
            with sqlite3.connect(self.db_path) as conn:
                # ai_learning
                conn.execute(
                    """INSERT INTO ai_learning
                           (symbol, trade_result, pnl, setup_quality, created_at)
                       VALUES (?, ?, ?, ?, datetime('now'))""",
                    (symbol, result, pnl, setup_quality)
                )
                # ai_postmortem
                if trade_id:
                    conn.execute(
                        """INSERT OR REPLACE INTO ai_postmortem
                               (trade_id, symbol, result, pnl, setup_quality,
                                hold_minutes, mfe_r, mae_r, created_at)
                           VALUES (?, ?, ?, ?, ?, ?, ?, ?, datetime('now'))""",
                        (trade_id, symbol, result, pnl, setup_quality,
                         hold_minutes, mfe_r, mae_r)
                    )
                # coin_profiles guncelle (coin-specific)
                row = conn.execute(
                    "SELECT total_trades, win_rate, total_pnl FROM coin_profiles WHERE symbol=?",
                    (symbol,)
                ).fetchone()
                if row:
                    total  = (row[0] or 0) + 1
                    wins   = conn.execute(
                        "SELECT COUNT(*) FROM ai_learning WHERE symbol=? AND trade_result='WIN'",
                        (symbol,)
                    ).fetchone()[0] or 0
                    wr     = round(wins / total * 100, 1)
                    t_pnl  = (row[2] or 0) + pnl
                    # Danger score: son 5 tradede 3+ LOSS ise artar
                    recent = conn.execute(
                        """SELECT trade_result FROM ai_learning
                           WHERE symbol=? ORDER BY created_at DESC LIMIT 5""",
                        (symbol,)
                    ).fetchall()
                    loss_count = sum(1 for r in recent if r[0] == "LOSS")
                    danger = min(100, max(0, loss_count * 20 - 20))
                    conn.execute(
                        """UPDATE coin_profiles
                           SET win_rate=?, total_trades=?, total_pnl=?,
                               danger_score=?, updated_at=datetime('now')
                           WHERE symbol=?""",
                        (wr, total, round(t_pnl, 4), danger, symbol)
                    )
                else:
                    wr    = 100.0 if result == "WIN" else 0.0
                    conn.execute(
                        """INSERT INTO coin_profiles
                               (symbol, win_rate, total_trades, total_pnl, danger_score)
                           VALUES (?, ?, 1, ?, 0)""",
                        (symbol, wr, round(pnl, 4))
                    )
                conn.commit()
            logger.info(
                f"[AI Learn] {symbol} {result} pnl={pnl:+.3f}$ quality={setup_quality}"
            )
        except Exception as e:
            logger.error(f"AI learn_from_trade hatasi: {e}")

    # ─────────────────────────────────────────────────────────────────────────
    # ISTATISTIK
    # ─────────────────────────────────────────────────────────────────────────
    def get_brain_stats(self):
        """Genel AI istatistiklerini doner."""
        try:
            with sqlite3.connect(self.db_path) as conn:
                total = conn.execute("SELECT COUNT(*) FROM ai_learning").fetchone()[0] or 0
                wins  = conn.execute(
                    "SELECT COUNT(*) FROM ai_learning WHERE trade_result='WIN'"
                ).fetchone()[0] or 0
                avg_pnl = conn.execute(
                    "SELECT AVG(pnl) FROM ai_learning"
                ).fetchone()[0] or 0
                return {
                    "total_learned": total,
                    "win_rate":      round(wins / total * 100, 1) if total > 0 else 0,
                    "avg_pnl":       round(avg_pnl, 4),
                }
        except Exception as e:
            logger.error(f"get_brain_stats hatasi: {e}")
            return {"total_learned": 0, "win_rate": 0, "avg_pnl": 0}

    def learn_from_paper_outcome(
        self,
        symbol: str,
        tracked_from: str,
        would_have_won: int,
        mfe_r: float = 0.0,
        mae_r: float = 0.0,
        first_touch: str = "neither_horizon",
        skip_correct: int = 0,
    ):
        """
        Ghost-tracker (WATCH/VETO) sinyallerinden ogrenme.
        Trade yapilmamis ama fiyat yolu simule edilmis sinyallerden veri toplar.
        Bu sayede sadece yapilan trade'lerden degil, yapilmayan trade'lerden de ogrenilir.
        """
        result = "WIN" if would_have_won else "LOSS"
        # pnl: simule (mfe_r = kazanc potansiyeli, mae_r = risk)
        sim_pnl = round(mfe_r * 0.01, 4) if would_have_won else round(-mae_r * 0.01, 4)
        try:
            with sqlite3.connect(self.db_path) as conn:
                conn.execute(
                    """INSERT INTO ai_learning
                           (symbol, trade_result, pnl, setup_quality, created_at)
                       VALUES (?, ?, ?, ?, datetime('now'))""",
                    (symbol, f"GHOST_{result}", sim_pnl, tracked_from)
                )
                # coin_profiles guncelle (ghost sinyaller daha az agirlik tasir)
                row = conn.execute(
                    "SELECT total_trades, win_rate, total_pnl FROM coin_profiles WHERE symbol=?",
                    (symbol,)
                ).fetchone()
                if row:
                    total = (row[0] or 0) + 1
                    wins  = conn.execute(
                        """SELECT COUNT(*) FROM ai_learning
                           WHERE symbol=? AND trade_result IN ('WIN','GHOST_WIN')""",
                        (symbol,)
                    ).fetchone()[0] or 0
                    wr    = round(wins / total * 100, 1)
                    t_pnl = (row[2] or 0) + sim_pnl
                    recent = conn.execute(
                        """SELECT trade_result FROM ai_learning
                           WHERE symbol=? ORDER BY created_at DESC LIMIT 5""",
                        (symbol,)
                    ).fetchall()
                    loss_count = sum(1 for r in recent if "LOSS" in r[0])
                    danger = min(100, max(0, loss_count * 20 - 20))
                    conn.execute(
                        """UPDATE coin_profiles
                           SET win_rate=?, total_trades=?, total_pnl=?,
                               danger_score=?, updated_at=datetime('now')
                           WHERE symbol=?""",
                        (wr, total, round(t_pnl, 4), danger, symbol)
                    )
                else:
                    wr = 100.0 if would_have_won else 0.0
                    conn.execute(
                        """INSERT INTO coin_profiles
                               (symbol, win_rate, total_trades, total_pnl, danger_score)
                           VALUES (?, ?, 1, ?, 0)""",
                        (symbol, wr, round(sim_pnl, 4))
                    )
                conn.commit()
            logger.info(
                f"[AI Ghost Learn] {symbol} {tracked_from} {result} "
                f"mfe={mfe_r:.2f}R mae={mae_r:.2f}R skip_ok={skip_correct}"
            )
        except Exception as e:
            logger.error(f"learn_from_paper_outcome hatasi: {e}")
