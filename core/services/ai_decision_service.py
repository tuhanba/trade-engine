import logging
import asyncio
from core.event_bus import event_bus
from core.event_types import Event, EventType
from core.ai_decision_engine import AIDecisionEngine
from core.data_layer import data_layer

logger = logging.getLogger("ax.services.ai_decision")

class AIDecisionService:
    def __init__(self):
        self.ai_engine = AIDecisionEngine()
        event_bus.subscribe(EventType.RISK_APPROVED, self.handle_risk_approved)

    async def handle_risk_approved(self, event: Event):
        payload = event.payload
        symbol = payload.get("symbol")
        signal_id = payload.get("signal_id")
        trend_result = payload.get("trend_result", {})
        trigger_result = payload.get("trigger_result", {})
        risk_result = payload.get("risk_result", {})
        tradeability_score = payload.get("tradeability_score")

        try:
            # We first assemble the signal object that AIDecisionEngine expects
            sig = await asyncio.to_thread(data_layer.get_signal, signal_id)
            if not sig:
                return

            sig.direction = trend_result["direction"]
            sig.coin_score = tradeability_score
            sig.trend_score = trend_result["score"]
            sig.trigger_score = trigger_result["score"]
            sig.risk_score = risk_result["score"]
            sig.setup_quality = trigger_result["quality"]
            sig.ml_score = trigger_result.get("ml_score", 50)
            sig.confluence_score = trigger_result.get("confluence_total", 2)
            sig.entry_zone = trigger_result["entry"]
            sig.stop_loss = risk_result["sl"]
            sig.tp1 = risk_result["tp1"]
            sig.tp2 = risk_result["tp2"]
            sig.tp3 = risk_result["tp3"]
            sig.rr = risk_result["rr"]
            sig.risk_percent = risk_result["risk_pct"]
            sig.position_size = risk_result["position_size"]
            sig.notional_size = risk_result["notional"]
            sig.leverage_suggestion = risk_result["leverage"]
            sig.max_loss = risk_result["max_loss"]
            sig.status = "ready"
            
            decision = await asyncio.to_thread(self.ai_engine.evaluate, sig)
            
            sig.final_score = decision["final_score"]
            sig.confidence = decision["confidence"]
            sig.reason = decision["reason"]

            if decision["decision"] == "VETO":
                logger.debug(f"[AIDecisionService] {symbol} vetoed by AI: {decision['reason']}")
                return

            # Note: The data_layer is updated here (simulated state changes)
            await asyncio.to_thread(data_layer.save_signal, sig)

            next_payload = {
                "symbol": symbol,
                "signal_id": signal_id,
                "signal_data": sig.to_dict(),
                "ai_decision": decision
            }

            await event_bus.publish(Event(type=EventType.AI_VALIDATED, payload=next_payload))
            logger.debug(f"[AIDecisionService] {symbol} validated by AI (Score: {decision['final_score']})")

        except Exception as e:
            logger.error(f"[AIDecisionService] Error processing {symbol}: {e}")
