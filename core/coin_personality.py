"""
coin_personality.py — Coin Personality Engine
==============================================
Her coin'in karakteristik davranışlarını (volatilite, fakeout eğilimi, 
trend sadakati vb.) analiz eder ve adaptif strateji profilleri oluşturur.
"""
import sqlite3
import logging
from typing import Dict

logger = logging.getLogger(__name__)

class CoinPersonalityEngine:
    def __init__(self, db_path="trade_engine.db"):
        self.db_path = db_path

    def _get_conn(self):
        return sqlite3.connect(self.db_path)

    def analyze_personality(self, symbol: str) -> dict:
        """Coin'in kişiliğini geçmiş verilere göre analiz eder."""
        try:
            with self._get_conn() as conn:
                conn.row_factory = sqlite3.Row
                profile = conn.execute("SELECT * FROM coin_profiles WHERE symbol=?", (symbol,)).fetchone()
                
                if not profile:
                    return {"personality": "unknown", "traits": []}
                
                traits = []
                wr = profile['win_rate']
                fakeout = profile.get('fakeout_rate', 0)
                mae = profile.get('avg_mae', 0)
                mfe = profile.get('avg_mfe', 0)
                
                # Kişilik Atamaları
                if fakeout > 0.4 or mae > 1.2:
                    personality = "The Faker"
                    traits.append("high_fakeout_risk")
                    traits.append("needs_wider_sl")
                elif wr > 0.6 and mfe > 2.0:
                    personality = "The Runner"
                    traits.append("strong_trend_follower")
                    traits.append("suitable_for_trailing_stop")
                elif wr < 0.3:
                    personality = "The Grinder"
                    traits.append("low_win_rate")
                    traits.append("needs_high_rr_to_survive")
                else:
                    personality = "The Average Joe"
                    traits.append("predictable_behavior")
                
                return {
                    "symbol": symbol,
                    "personality": personality,
                    "traits": traits,
                    "metrics": {
                        "win_rate": wr,
                        "fakeout_rate": fakeout,
                        "avg_mfe": mfe,
                        "avg_mae": mae
                    }
                }
        except Exception as e:
            logger.error(f"Personality analysis error for {symbol}: {e}")
            return {"personality": "error", "message": str(e)}

    def get_adaptive_params(self, symbol: str) -> dict:
        """Coin'in kişiliğine göre optimize edilmiş parametreler önerir."""
        personality_data = self.analyze_personality(symbol)
        personality = personality_data.get("personality")
        
        # Varsayılanlar
        params = {
            "sl_atr_mult": 1.5,
            "tp_atr_mult": 2.0,
            "risk_pct": 1.0,
            "leverage": 10
        }
        
        if personality == "The Faker":
            params["sl_atr_mult"] = 2.0  # Daha geniş stop
            params["risk_pct"] = 0.7     # Daha düşük risk
            params["leverage"] = 5       # Düşük kaldıraç
        elif personality == "The Runner":
            params["tp_atr_mult"] = 3.0  # Daha uzak hedef
            params["risk_pct"] = 1.2     # Daha yüksek güven
            params["leverage"] = 15      # Daha yüksek kaldıraç
        elif personality == "The Grinder":
            params["sl_atr_mult"] = 1.2  # Sıkı stop
            params["tp_atr_mult"] = 2.5  # Yüksek RR hedefi
            params["risk_pct"] = 0.5     # Minimum risk
            
        return params
