import logging
import asyncio
from typing import Dict, Any
from core.event_bus import event_bus
from core.event_types import Event, EventType, TradeLifecycleState
from core.trend_engine import TrendEngine
from core.data_layer import data_layer

logger = logging.getLogger("ax.services.trend")

class TrendService:
    def __init__(self, client):
        self.trend_engine = TrendEngine(client)
        event_bus.subscribe(EventType.SCANNED, self.handle_scanned)

    async def handle_scanned(self, event: Event):
        # Offload sync blocking operation to threadpool
        payload = event.payload
        symbol = payload.get("symbol")
        tradeability_score = payload.get("tradeability_score", 0.0)

        if not symbol:
            return

        try:
            # We wrap the synchronous analyze call
            trend_result = await asyncio.to_thread(self.trend_engine.analyze, symbol)
            
            if trend_result["direction"] == "NO TRADE":
                logger.debug(f"[TrendService] {symbol} rejected: NO TRADE direction")
                try:
                    from database import save_signal_event
                    await asyncio.to_thread(
                        save_signal_event, None, "TREND_REJECTED",
                        symbol=symbol, reject_reason="NO_TRADE"
                    )
                except Exception:
                    pass
                return
            
            # Create the initial signal id in database
            signal = await asyncio.to_thread(data_layer.create_signal, symbol)
            
            # Prepare payload for the next pipeline step
            next_payload = {
                "symbol": symbol,
                "signal_id": signal.id,
                "tradeability_score": tradeability_score,
                "trend_result": trend_result
            }
            
            await event_bus.publish(Event(type=EventType.TREND_CHECKED, payload=next_payload))
            logger.debug(f"[TrendService] {symbol} passed trend check: {trend_result['direction']}")

        except Exception as e:
            logger.error(f"[TrendService] Error processing {symbol}: {e}")
