"""
signal_replay.py — Signal Replay & Backtest Engine
==================================================
Geçmiş sinyalleri farklı parametrelerle tekrar oynatır (replay) 
ve strateji optimizasyonu için backtest yapar.
"""
import sqlite3
import logging
import json
from datetime import datetime, timezone
from typing import List, Dict

logger = logging.getLogger(__name__)

class SignalReplay:
    def __init__(self, db_path="trade_engine.db"):
        self.db_path = db_path

    def _get_conn(self):
        return sqlite3.connect(self.db_path)

    def replay_signal(self, signal_id: str, new_params: dict) -> dict:
        """
        Belirli bir sinyali yeni parametrelerle (SL, TP, Risk) simüle eder.
        """
        try:
            with self._get_conn() as conn:
                conn.row_factory = sqlite3.Row
                # Sinyal verisini al
                sig = conn.execute("SELECT * FROM signal_candidates WHERE uuid = ?", (signal_id,)).fetchone()
                if not sig:
                    return {"status": "error", "message": "Signal not found"}
                
                # Sinyal sonucunu al (eğer varsa)
                result = conn.execute("SELECT * FROM paper_results WHERE signal_id = ?", (signal_id,)).fetchone()
                
                # Simülasyon mantığı
                # Bu kısım normalde o anki fiyat hareketlerini (kline) gerektirir.
                # Mevcut DB'de kline verisi yoksa, paper_results'daki MFE/MAE üzerinden basitleştirilmiş simülasyon yapılır.
                
                if not result:
                    return {"status": "pending", "message": "No outcome data for replay"}
                
                mfe = result['max_favorable_excursion']
                mae = result['max_adverse_excursion']
                
                # Yeni parametrelerle SL/TP kontrolü
                new_sl_mult = new_params.get("sl_atr_mult", 1.5)
                new_tp_mult = new_params.get("tp_atr_mult", 2.0)
                
                # Basitleştirilmiş Replay: 
                # Eğer MAE yeni SL'den büyükse STOP, değilse ve MFE yeni TP'den büyükse WIN.
                # Not: Bu gerçek zamanlı mum takibi kadar hassas değildir.
                
                # Varsayımsal SL/TP mesafeleri (ATR bazlı)
                atr = sig.get('atr', 0.01)
                sl_dist = atr * new_sl_mult
                tp_dist = atr * new_tp_mult
                
                # Normalize edilmiş MFE/MAE (fiyat farkı / giriş fiyatı * 100 gibi bir oransa)
                # Burada MAE ve MFE'nin R-multiple cinsinden olduğunu varsayıyoruz (core/paper_tracker.py'ye göre)
                
                new_outcome = "LOSS"
                if mae < new_sl_mult: # Stop olmadı
                    if mfe >= new_tp_mult:
                        new_outcome = "WIN"
                    else:
                        new_outcome = "TIMEOUT/BREAKEVEN"
                else:
                    new_outcome = "LOSS"
                    
                return {
                    "signal_id": signal_id,
                    "original_outcome": "WIN" if result['would_have_won'] else "LOSS",
                    "new_outcome": new_outcome,
                    "params_used": new_params
                }
        except Exception as e:
            logger.error(f"Signal replay error: {e}")
            return {"status": "error", "message": str(e)}

    def run_batch_backtest(self, signals: List[str], params: dict) -> dict:
        """Birden fazla sinyal üzerinde toplu backtest yapar."""
        results = []
        for sid in signals:
            res = self.replay_signal(sid, params)
            if res.get("status") != "error":
                results.append(res)
        
        wins = sum(1 for r in results if r.get("new_outcome") == "WIN")
        total = len(results)
        
        return {
            "total_signals": total,
            "wins": wins,
            "win_rate": round(wins / total, 2) if total > 0 else 0,
            "params": params
        }

    def optimize_parameters(self, sample_size: int = 100) -> dict:
        """Geçmiş veriler üzerinde kaba kuvvet (brute-force) ile parametre optimizasyonu yapar."""
        try:
            with self._get_conn() as conn:
                rows = conn.execute("""
                    SELECT uuid FROM signal_candidates 
                    WHERE uuid IN (SELECT signal_id FROM paper_results WHERE status IN ('finalized', 'completed'))
                    ORDER BY id DESC LIMIT ?
                """, (sample_size,)).fetchall()
                sids = [r[0] for r in rows]
            
            if not sids:
                return {"status": "error", "message": "Not enough data for optimization"}
            
            best_params = {}
            best_wr = 0
            
            # Parametre uzayında arama (Örnek)
            for sl in [1.0, 1.5, 2.0]:
                for tp in [1.5, 2.0, 3.0]:
                    params = {"sl_atr_mult": sl, "tp_atr_mult": tp}
                    res = self.run_batch_backtest(sids, params)
                    if res["win_rate"] > best_wr:
                        best_wr = res["win_rate"]
                        best_params = params
            
            return {
                "best_params": best_params,
                "best_win_rate": best_wr,
                "sample_size": len(sids)
            }
        except Exception as e:
            logger.error(f"Optimization error: {e}")
            return {"status": "error", "message": str(e)}
