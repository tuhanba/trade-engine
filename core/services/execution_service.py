import logging
import asyncio
from core.event_bus import event_bus
from core.event_types import Event, EventType
from execution_engine import ExecutionEngine
from core.data_layer import SignalData

logger = logging.getLogger("ax.services.execution")

class ExecutionService:
    def __init__(self):
        self.execution_engine = ExecutionEngine()
        event_bus.subscribe(EventType.AI_VALIDATED, self.handle_ai_validated)

    async def handle_ai_validated(self, event: Event):
        payload = event.payload
        symbol = payload.get("symbol")
        signal_dict = payload.get("signal_data")

        try:
            # Check thresholds (execution mode etc.)
            import config
            trade_thr = config.HUMAN_TRADE_THRESHOLD if config.HUMAN_MODE else config.TRADE_THRESHOLD
            
            sig = SignalData.from_dict(signal_dict)
            
            if sig.final_score >= trade_thr and sig.setup_quality in config.EXECUTABLE_QUALITIES:
                # Dispatch execution approval
                await event_bus.publish(Event(type=EventType.EXECUTION_APPROVED, payload=payload))
                
                # Execute paper trade
                trade_id = await asyncio.to_thread(self.execution_engine.process_signal, sig)
                
                if trade_id:
                    trade_payload = {
                        "trade_id": trade_id,
                        "symbol": symbol,
                        "signal_id": sig.id,
                        "direction": sig.direction,
                        "entry": sig.entry_price,
                        "sl": sig.stop_loss
                    }
                    await event_bus.publish(Event(type=EventType.TRADE_OPENED, payload=trade_payload))
                    logger.info(f"[ExecutionService] {symbol} trade opened: #{trade_id}")
            else:
                logger.debug(f"[ExecutionService] {symbol} score {sig.final_score} below trade threshold {trade_thr}")

        except Exception as e:
            logger.error(f"[ExecutionService] Error executing {symbol}: {e}")
