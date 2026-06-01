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
            
            sig.metadata = {
                "adx": trigger_result.get("adx", 0.0),
                "rv": trigger_result.get("rv", 1.0),
                "rsi5": trigger_result.get("rsi5", 50.0),
                "rsi1": trigger_result.get("rsi1", 50.0),
                "ml_score": trigger_result.get("ml_score", 50.0),
                "direction": trend_result.get("direction", "LONG"),
                "session": trigger_result.get("session", "OFF"),
                "hold_minutes": 0.0,
                "partial_exit": 0,
                "funding_favorable": trigger_result.get("funding_favorable", 0),
                "bb_width_pct": trigger_result.get("bb_width", 0.0),
                "ob_ratio": trigger_result.get("ob_ratio", 1.0),
                "volume_m": trigger_result.get("volume_m", 0.0),
                "btc_trend": trend_result.get("btc_trend", "NEUTRAL"),
                "bb_width_chg": trigger_result.get("bb_width_chg", 0.0),
                "momentum_3c": trigger_result.get("momentum_3c", 0.0),
                "prev_result": trigger_result.get("prev_result", "NONE"),
                "funding_rate": trigger_result.get("funding_rate", 0.0),
                "cvd_value": trigger_result.get("cvd_value", 0.0),
                "oi_change_pct": trigger_result.get("oi_change_pct", 0.0),
            }
            
            decision = await asyncio.to_thread(self.ai_engine.evaluate, sig)
            
            sig.final_score = decision["final_score"]
            sig.confidence = decision["confidence"]
            sig.reason = decision["reason"]

            candidate_id = payload.get("candidate_id")
            if candidate_id:
                try:
                    from database import update_candidate_status
                    await asyncio.to_thread(
                        update_candidate_status,
                        candidate_id,
                        decision=decision["decision"],
                        reject_reason=decision["reason"],
                        ai_score=decision["final_score"]
                    )
                except Exception as db_e:
                    logger.error(f"[AIDecisionService] DB Update Error: {db_e}")

            if decision["decision"] == "VETO":
                logger.debug(f"[AIDecisionService] {symbol} vetoed by AI: {decision['reason']}")
                try:
                    from database import save_signal_event
                    await asyncio.to_thread(
                        save_signal_event, signal_id, "AI_VETOED",
                        symbol=symbol, reject_reason=decision.get("reason", "ai_veto")
                    )
                except Exception:
                    pass
                return

            # Signal record is already live in data_layer memory — no separate save needed.
            # data_layer.save_signal() was removed (method does not exist on DataLayer).

            next_payload = {
                "symbol": symbol,
                "signal_id": signal_id,
                "signal_data": sig.to_dict(),
                "ai_decision": decision
            }

            await event_bus.publish(Event(type=EventType.AI_VALIDATED, payload=next_payload))
            logger.info(f"[AIDecisionService] {symbol} AI_VALIDATED published (score={decision['final_score']:.1f} decision={decision['decision']})")


        except Exception as e:
            logger.error(f"[AIDecisionService] Error processing {symbol}: {e}")
