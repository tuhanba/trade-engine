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
        asyncio.create_task(self._monitoring_loop())

    async def _monitoring_loop(self):
        while True:
            try:
                await asyncio.to_thread(self.execution_engine.update_open_trades)
            except Exception as e:
                logger.error(f"[ExecutionService] Monitor loop error: {e}")
            await asyncio.sleep(1)

    async def handle_ai_validated(self, event: Event):
        payload = event.payload
        symbol = payload.get("symbol")
        signal_dict = payload.get("signal_data")

        try:
            # Check thresholds (execution mode etc.)
            import config
            is_scalp = not getattr(config, "HUMAN_MODE", False)
            trade_thr = config.HUMAN_TRADE_THRESHOLD if not is_scalp else getattr(config, "TRADE_THRESHOLD", 55.0)
            
            sig = SignalData.from_dict(signal_dict)
            
            # Dinamik eşik: Scalp modunda ML skoru >= 65 ise barajı 3 puan düşür (Makineli tüfek bonusu)
            ml_score = float(getattr(sig, "ml_score", 50.0) or 50.0)
            if is_scalp and ml_score >= 65:
                trade_thr -= 3.0
                logger.debug(f"[ExecutionService] {symbol} Scalp ML Bonus: trade_thr {trade_thr+3.0} -> {trade_thr}")
            
            if sig.final_score >= trade_thr and sig.setup_quality in getattr(config, "EXECUTABLE_QUALITIES", ("S", "A+", "A", "B", "C")):
                # Dispatch execution approval
                await event_bus.publish(Event(type=EventType.EXECUTION_APPROVED, payload=payload))
                
                # Execute trade (Paper or Live)
                if config.EXECUTION_MODE == "live":
                    if not hasattr(self, "live_execution_engine"):
                        from core.live_execution import LiveExecutionEngine
                        self.live_execution_engine = LiveExecutionEngine()
                    trade_id = await asyncio.to_thread(self.live_execution_engine.open_live_trade, sig)
                else:
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
