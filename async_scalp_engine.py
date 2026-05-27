import os
import sys
import time
import signal
import asyncio
import logging

import config
from binance.client import Client
from database import init_db, init_paper_account
from core.event_bus import event_bus
from core.async_market_data import AsyncMarketDataService
from core.recovery_service import RecoveryService
from core.global_risk_manager import GlobalRiskManager
from core.metrics import start_metrics_server
from core.services.trend_service import TrendService
from core.services.trigger_service import TriggerService
from core.services.risk_service import RiskService
from core.services.ai_decision_service import AIDecisionService
from core.services.execution_service import ExecutionService
from core.services.notification_service import NotificationService
from core.services.scanner_service import ScannerService
from telegram_manager import TelegramManager
import telegram_delivery

os.makedirs("logs", exist_ok=True)
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler("bot.log", encoding="utf-8")
    ]
)
logger = logging.getLogger("ax.async_engine")

class AsyncScalpEngine:
    def __init__(self):
        self.market_data = AsyncMarketDataService(config.BINANCE_API_KEY or "", config.BINANCE_API_SECRET or "")
        self.client = Client(config.BINANCE_API_KEY or "", config.BINANCE_API_SECRET or "")

    async def start(self):
        logger.info("Starting Event-Driven Async Scalp Engine...")
        
        # Init DB
        await asyncio.to_thread(init_db)
        await asyncio.to_thread(init_paper_account)

        # Start Prometheus Metrics Server
        start_metrics_server(port=8000)

        # State Recovery (Çöken işlemleri kurtar)
        recovery_svc = RecoveryService()
        await recovery_svc.perform_state_recovery()

        # Start Global Risk Manager (Kill Switch)
        risk_manager = GlobalRiskManager(drawdown_limit_pct=5.0)
        await risk_manager.start()

        # Start Event Bus
        await event_bus.start()

        # Initialize Services
        TrendService(self.client)
        TriggerService(self.client)
        RiskService(self.client)
        AIDecisionService()
        ExecutionService()
        NotificationService()
        self.scanner_service = ScannerService()
        asyncio.create_task(self.scanner_service.start())

        # Start ML Background Training Loop
        asyncio.create_task(self._ml_training_loop())

        # Start Heartbeat Loop
        asyncio.create_task(self._heartbeat_loop())

        # Start Telegram Command Manager
        self.telegram_manager = TelegramManager(telegram_delivery.send_message)
        self.telegram_manager.start()

        # Start WebSocket Data Feed
        await self.market_data.initialize()
        
        # Sinyal geldiğinde event bus'a bas (örnek - devre dışı bırakıldı)
        # async def on_ticker_update(data):
        #     await event_bus.publish("market_data_update", data)
        # self.market_data.on_ticker(on_ticker_update)
        
        # Tüm market için stream başlat
        await self.market_data.start_all_tickers()

        # Keep engine running
        while True:
            await asyncio.sleep(1)

    async def _ml_training_loop(self):
        """Train the ML signal scorer every 24 hours."""
        from core.ml_signal_scorer import train_model
        while True:
            try:
                # Train immediately on start, then every 24h
                success = await asyncio.to_thread(train_model)
                if success:
                    logger.info("Background ML Training completed successfully.")
            except Exception as e:
                logger.error(f"Background ML Training failed: {e}")
            await asyncio.sleep(86400) # 24h

    async def _heartbeat_loop(self):
        """Update heartbeat in database every 10 seconds."""
        from database import set_bot_status
        from datetime import datetime, timezone
        while True:
            try:
                await asyncio.to_thread(set_bot_status, "heartbeat", datetime.now(timezone.utc).isoformat())
                await asyncio.to_thread(set_bot_status, "status", "running")
            except Exception:
                pass
            await asyncio.sleep(10)

    async def stop(self):
        logger.info("Stopping engine...")
        if hasattr(self, 'scanner_service'):
            self.scanner_service.stop()
        if hasattr(self, 'telegram_manager'):
            self.telegram_manager.stop()
        if self.market_data:
            await self.market_data.stop()
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
        from telegram_delivery import send_message
        send_message("🟢 <b>Sistem Başlatıldı!</b>\n🤖 Asenkron Scalp Motoru piyasayı taramaya başladı.")
        await engine.start()
    except asyncio.CancelledError:
        pass

if __name__ == "__main__":
    if sys.platform == "win32":
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    asyncio.run(main())
