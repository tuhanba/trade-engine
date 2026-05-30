import logging
import asyncio
from core.event_bus import event_bus
from core.event_types import Event, EventType
from core.risk_engine import RiskEngine
from database import get_paper_balance

logger = logging.getLogger("ax.services.risk")

class RiskService:
    def __init__(self, client):
        self.risk_engine = RiskEngine(client)
        event_bus.subscribe(EventType.TRIGGER_CHECKED, self.handle_trigger_checked)

    async def handle_trigger_checked(self, event: Event):
        payload = event.payload
        symbol = payload.get("symbol")
        trend_result = payload.get("trend_result", {})
        trigger_result = payload.get("trigger_result", {})
        signal_id = payload.get("signal_id")
        tradeability_score = payload.get("tradeability_score")

        try:
            # We fetch balance once here or use a cached state
            balance = await asyncio.to_thread(get_paper_balance)

            risk_result = await asyncio.to_thread(
                self.risk_engine.calculate,
                symbol, 
                trend_result["direction"],
                trigger_result["entry"],
                trigger_result["quality"],
                balance
            )

            if not risk_result.get("valid"):
                logger.debug(f"[RiskService] {symbol} rejected: {risk_result.get('risk_reject_reason')}")
                return

            next_payload = {
                "symbol": symbol,
                "signal_id": signal_id,
                "candidate_id": payload.get("candidate_id"),
                "tradeability_score": tradeability_score,
                "trend_result": trend_result,
                "trigger_result": trigger_result,
                "risk_result": risk_result
            }

            await event_bus.publish(Event(type=EventType.RISK_APPROVED, payload=next_payload))
            logger.debug(f"[RiskService] {symbol} passed risk check.")

        except Exception as e:
            logger.error(f"[RiskService] Error processing {symbol}: {e}")
