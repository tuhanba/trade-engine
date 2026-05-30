import logging
import asyncio
import database
from core.event_bus import event_bus
from core.event_types import Event, EventType
from execution_engine import ExecutionEngine
from core.data_layer import SignalData

logger = logging.getLogger("ax.services.execution")

class ExecutionService:
    def __init__(self):
        self.execution_engine = ExecutionEngine()
        self._trade_lock = asyncio.Lock()
        event_bus.subscribe(EventType.AI_VALIDATED, self.handle_ai_validated)
        self._monitor_task = None

    async def start(self):
        """Monitoring loop'u başlat. async_scalp_engine.py'den çağrılır."""
        self._monitor_task = asyncio.create_task(self._monitoring_loop())

    async def _monitoring_loop(self):
        prev_open_ids = set()
        while True:
            try:
                await asyncio.to_thread(self.execution_engine.update_open_trades)
                current_open = await asyncio.to_thread(database.get_open_trades)
                current_ids = {t['id'] for t in current_open}
                closed_ids = prev_open_ids - current_ids
                if closed_ids:
                    for trade_id in closed_ids:
                        closed = await asyncio.to_thread(database.get_trade_by_id, trade_id)
                        if closed:
                            await event_bus.publish(Event(
                                type=EventType.TRADE_CLOSED,
                                payload={
                                    "trade_id":      trade_id,
                                    "symbol":        closed.get("symbol"),
                                    "direction":     closed.get("direction"),
                                    "net_pnl":       closed.get("net_pnl", 0),
                                    "reason":        closed.get("close_reason", "unknown"),
                                    "balance_after": await asyncio.to_thread(database.get_paper_balance),
                                }
                            ))
                prev_open_ids = current_ids
            except Exception as e:
                logger.error(f"[ExecutionService] Monitor loop error: {e}")
            await asyncio.sleep(1)

    async def handle_ai_validated(self, event: Event):
        async with self._trade_lock:
            await self._execute_trade(event)

    async def _execute_trade(self, event: Event):
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
                # EXECUTION_APPROVED: abone yok, ilerideki journaling için bırakıldı
                # await event_bus.publish(Event(type=EventType.EXECUTION_APPROVED, payload=payload))
                
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
                        "trade_id":      trade_id,
                        "symbol":        symbol,
                        "signal_id":     sig.id,
                        "direction":     sig.direction,
                        "entry":         sig.entry_price,
                        "sl":            sig.stop_loss,
                        "tp1":           sig.tp1,
                        "tp2":           sig.tp2,
                        "tp3":           sig.tp3,
                        "leverage":      sig.leverage_suggestion or sig.leverage,
                        "risk_usd":      sig.max_loss,
                        "setup_quality": sig.setup_quality,
                        "final_score":   sig.final_score,
                        "rr":            sig.rr,
                        "risk_pct":      sig.risk_pct,
                        "position_size": sig.position_size,
                        "notional":      sig.notional_size,
                    }
                    await event_bus.publish(Event(type=EventType.TRADE_OPENED, payload=trade_payload))
                    logger.info(f"[ExecutionService] {symbol} trade opened: #{trade_id}")
            else:
                logger.debug(f"[ExecutionService] {symbol} score {sig.final_score} below trade threshold {trade_thr}")

        except Exception as e:
            logger.error(f"[ExecutionService] Error executing {symbol}: {e}")
