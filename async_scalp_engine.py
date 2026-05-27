import os
import sys
import time
import signal
import asyncio
import logging
from binance.client import Client

import config
from database import init_db, init_paper_account
from core.event_bus import event_bus
from core.services.scanner_service import ScannerService
from core.services.trend_service import TrendService
from core.services.trigger_service import TriggerService
from core.services.risk_service import RiskService
from core.services.ai_decision_service import AIDecisionService
from core.services.execution_service import ExecutionService
from core.services.notification_service import NotificationService

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
)
logger = logging.getLogger("ax.async_engine")

class AsyncScalpEngine:
    def __init__(self):
        self.scanner_service = None
        
        # Initialize Binance Client
        try:
            self.client = Client(config.BINANCE_API_KEY or "", config.BINANCE_API_SECRET or "")
            self.client.ping()
            logger.info("Binance connection OK")
        except Exception as e:
            logger.warning(f"Binance connection failed: {e}. Using public endpoints.")
            self.client = Client("", "")

    async def start(self):
        logger.info("Starting Event-Driven Async Scalp Engine...")
        
        # Init DB
        await asyncio.to_thread(init_db)
        await asyncio.to_thread(init_paper_account)

        # Start Event Bus
        await event_bus.start()

        # Initialize Services
        TrendService(self.client)
        TriggerService(self.client)
        RiskService(self.client)
        AIDecisionService()
        ExecutionService()
        NotificationService()

        # Start Scanner Loop
        self.scanner_service = ScannerService(interval_seconds=config.SCAN_INTERVAL)
        asyncio.create_task(self.scanner_service.start())

        # Keep engine running
        while True:
            await asyncio.sleep(1)

    async def stop(self):
        logger.info("Stopping engine...")
        if self.scanner_service:
            self.scanner_service.stop()
        await event_bus.stop()

def handle_exception(loop, context):
    msg = context.get("exception", context["message"])
    logger.error(f"Caught exception: {msg}")

async def main():
    loop = asyncio.get_running_loop()
    loop.set_exception_handler(handle_exception)
    
    engine = AsyncScalpEngine()
    
    def shutdown_signal():
        logger.info("Received shutdown signal")
        asyncio.create_task(engine.stop())
        
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, shutdown_signal)
        
    try:
        await engine.start()
    except asyncio.CancelledError:
        pass

if __name__ == "__main__":
    if sys.platform == "win32":
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    asyncio.run(main())
