import logging
import asyncio
from core.event_bus import event_bus
from core.event_types import Event, EventType
from core.trigger_engine import TriggerEngine
from database import save_candidate_signal

logger = logging.getLogger("ax.services.trigger")

class TriggerService:
    def __init__(self, client):
        self.trigger_engine = TriggerEngine(client)
        event_bus.subscribe(EventType.TREND_CHECKED, self.handle_trend_checked)

    async def handle_trend_checked(self, event: Event):
        payload = event.payload
        symbol = payload.get("symbol")
        trend_result = payload.get("trend_result", {})
        signal_id = payload.get("signal_id")
        tradeability_score = payload.get("tradeability_score")

        try:
            # Wrap synchronous call
            trigger_result = await asyncio.to_thread(
                self.trigger_engine.analyze,
                symbol,
                trend_result["direction"],
                trend_result.get("btc_trend", "NEUTRAL"),
                trend_confluence=trend_result.get("confluence_raw", 1)
            )

            if trigger_result["quality"] == "D":
                logger.debug(f"[TriggerService] {symbol} rejected: quality D")
                return

            if trigger_result["quality"] == "C":
                # Watchlist only, log it but don't proceed to RISK
                logger.debug(f"[TriggerService] {symbol} quality C, watchlist only")
                return

            next_payload = {
                "symbol": symbol,
                "signal_id": signal_id,
                "tradeability_score": tradeability_score,
                "trend_result": trend_result,
                "trigger_result": trigger_result
            }

            await event_bus.publish(Event(type=EventType.TRIGGER_CHECKED, payload=next_payload))
            logger.debug(f"[TriggerService] {symbol} passed trigger check: quality {trigger_result['quality']}")

        except Exception as e:
            logger.error(f"[TriggerService] Error processing {symbol}: {e}")
