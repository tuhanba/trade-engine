import logging
import asyncio
from core.event_bus import event_bus
from core.event_types import Event, EventType
from core.async_market_scanner import AsyncMarketScanner
from database import get_open_trades

logger = logging.getLogger("ax.services.scanner")

class ScannerService:
    def __init__(self, interval_seconds: int = 45):
        self.scanner = AsyncMarketScanner()
        self.interval = interval_seconds
        self._running = False
        event_bus.subscribe(EventType.KILL_SWITCH_ACTIVATED, self.handle_kill_switch)

    async def handle_kill_switch(self, event: Event):
        logger.critical("[ScannerService] KILL SWITCH ACTIVATED! Stopping scanner.")
        self.stop()

    async def start(self):
        self._running = True
        logger.info("[ScannerService] Started")
        
        while self._running:
            try:
                open_trades = await asyncio.to_thread(get_open_trades)
                open_symbols = {t["symbol"] for t in open_trades}

                candidates = await self.scanner.scan()
                for c in candidates:
                    if c["symbol"] in open_symbols:
                        continue
                    
                    if c["status"] in ("Eligible", "Watch"):
                        await event_bus.publish(Event(
                            type=EventType.SCANNED, 
                            payload=c
                        ))
                
                await asyncio.sleep(self.interval)
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"[ScannerService] Error: {e}")
                await asyncio.sleep(5)

    def stop(self):
        self._running = False
        logger.info("[ScannerService] Stopped")
