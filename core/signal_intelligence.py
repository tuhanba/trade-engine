"""
signal_intelligence.py — Historical Signal Intelligence Engine
==============================================================
Geçmiş sinyalleri analiz eder, başarı oranlarını hesaplar ve 
AI Decision Engine için istihbarat sağlar.
"""
import sqlite3
import logging
from datetime import datetime, timezone, timedelta
from collections import defaultdict

logger = logging.getLogger(__name__)

class SignalIntelligence:
    def __init__(self, db_path="trade_engine.db"):
        self.db_path = db_path

    def _get_conn(self):
        return sqlite3.connect(self.db_path)

    def get_symbol_performance(self, symbol: str, days: int = 30) -> dict:
        """Belirli bir coin için son N gündeki performansı getirir."""
        since = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
        try:
            with self._get_conn() as conn:
                conn.row_factory = sqlite3.Row
                # Tamamlanmış işlemler
                trades = conn.execute("""
                    SELECT net_pnl, setup_quality, direction 
                    FROM trades 
                    WHERE symbol = ? AND close_time > ? AND status LIKE 'closed%'
                """, (symbol, since)).fetchall()
                
                # Paper sonuçları (girilmeyen ama takip edilen)
                paper = conn.execute("""
                    SELECT would_have_won, setup_worked, tracked_from
                    FROM paper_results
                    WHERE symbol = ? AND finalized_at > ?
                """, (symbol, since)).fetchall()
                
                total_trades = len(trades)
                wins = sum(1 for t in trades if t['net_pnl'] > 0)
                
                total_paper = len(paper)
                paper_wins = sum(1 for p in paper if p['would_have_won'] == 1 or p['setup_worked'] == 1)
                
                combined_total = total_trades + total_paper
                combined_wins = wins + paper_wins
                
                win_rate = combined_wins / combined_total if combined_total > 0 else 0
                
                return {
                    "symbol": symbol,
                    "win_rate": round(win_rate, 2),
                    "total_samples": combined_total,
                    "live_trades": total_trades,
                    "paper_samples": total_paper,
                    "status": "high_performer" if win_rate > 0.6 and combined_total >= 5 else "neutral"
                }
        except Exception as e:
            logger.error(f"Symbol performance error for {symbol}: {e}")
            return {"symbol": symbol, "win_rate": 0, "total_samples": 0, "status": "unknown"}

    def get_quality_intelligence(self, days: int = 30) -> dict:
        """Setup kalitelerine (A+, A, B, C) göre başarı istatistikleri."""
        since = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
        try:
            with self._get_conn() as conn:
                conn.row_factory = sqlite3.Row
                rows = conn.execute("""
                    SELECT setup_quality, net_pnl 
                    FROM trades 
                    WHERE close_time > ? AND status LIKE 'closed%'
                """, (since,)).fetchall()
                
                stats = defaultdict(lambda: {"total": 0, "wins": 0})
                for r in rows:
                    q = r['setup_quality'] or "Unknown"
                    stats[q]["total"] += 1
                    if r['net_pnl'] > 0:
                        stats[q]["wins"] += 1
                
                result = {}
                for q, s in stats.items():
                    result[q] = {
                        "win_rate": round(s["wins"] / s["total"], 2) if s["total"] > 0 else 0,
                        "sample_size": s["total"]
                    }
                return result
        except Exception as e:
            logger.error(f"Quality intelligence error: {e}")
            return {}

    def get_market_regime_intelligence(self) -> dict:
        """Piyasa rejimine göre başarı analizi."""
        # Not: Market regime verisi trades tablosunda tutuluyor olmalı
        try:
            with self._get_conn() as conn:
                conn.row_factory = sqlite3.Row
                rows = conn.execute("""
                    SELECT market_regime, net_pnl 
                    FROM trades 
                    WHERE status LIKE 'closed%'
                """).fetchall()
                
                regimes = defaultdict(lambda: {"total": 0, "wins": 0})
                for r in rows:
                    reg = r['market_regime'] or "Neutral"
                    regimes[reg]["total"] += 1
                    if r['net_pnl'] > 0:
                        regimes[reg]["wins"] += 1
                
                return {reg: round(s["wins"]/s["total"], 2) for reg, s in regimes.items() if s["total"] > 0}
        except Exception as e:
            logger.error(f"Market regime intelligence error: {e}")
            return {}

    def get_ai_boost_recommendation(self, symbol: str, quality: str) -> float:
        """
        Geçmiş verilere dayanarak AI skoru için bir 'boost' veya 'penalty' önerir.
        """
        perf = self.get_symbol_performance(symbol)
        q_perf = self.get_quality_intelligence()
        
        boost = 0.0
        
        # Coin bazlı boost
        if perf["win_rate"] > 0.7 and perf["total_samples"] >= 3:
            boost += 1.5
        elif perf["win_rate"] < 0.3 and perf["total_samples"] >= 3:
            boost -= 2.0
            
        # Kalite bazlı boost
        q_stats = q_perf.get(quality, {"win_rate": 0.5, "sample_size": 0})
        if q_stats["win_rate"] > 0.65 and q_stats["sample_size"] >= 10:
            boost += 1.0
        elif q_stats["win_rate"] < 0.4 and q_stats["sample_size"] >= 10:
            boost -= 1.0
            
        return boost
