"""
AI Decision Engine
Final karar katmanı. Sinyalleri onaylar veya reddeder.
"""
import logging

logger = logging.getLogger(__name__)

class AIDecisionEngine:
    def __init__(self):
        self.daily_signals = 0
        self.max_daily_signals = 40
        self.recent_coins = []

    def evaluate(self, signal_data) -> dict:
        """Sinyali değerlendirir ve final kararını verir."""
        if not signal_data.is_valid():
            return {"decision": "VETO", "reason": "Invalid data"}

        # Günlük limit kontrolü
        if self.daily_signals >= self.max_daily_signals:
            return {"decision": "VETO", "reason": "Daily limit reached"}

        # Aynı coin spam kontrolü
        if signal_data.symbol in self.recent_coins[-3:]:
            return {"decision": "VETO", "reason": "Coin spam protection"}

        # Final skor hesaplama
        final_score = (
            signal_data.coin_score * 0.1 +
            signal_data.trend_score * 0.3 +
            signal_data.trigger_score * 0.3 +
            signal_data.risk_score * 0.3
        )
        
        confidence = final_score / 10.0

        # Karar kuralları
        if final_score >= 8.0 and signal_data.setup_quality in ["A+", "A"]:
            decision = "ALLOW"
            reason = "High quality setup"
        elif final_score >= 6.0 and signal_data.setup_quality == "B":
            decision = "ALLOW"
            reason = "Acceptable setup"
        else:
            decision = "VETO"
            reason = "Low score or quality"

        if decision == "ALLOW":
            self.daily_signals += 1
            self.recent_coins.append(signal_data.symbol)
            if len(self.recent_coins) > 10:
                self.recent_coins.pop(0)

        return {
            "decision": decision,
            "final_score": round(final_score, 1),
            "confidence": round(confidence, 2),
            "reason": reason
        }
