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
    force=True,
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler("db/bot.log" if os.path.exists("db") else "bot.log", encoding="utf-8")
    ]
)
logger = logging.getLogger("ax.async_engine")

class AsyncScalpEngine:
    def __init__(self):
        self.market_data = AsyncMarketDataService(config.BINANCE_API_KEY or "", config.BINANCE_API_SECRET or "")
        self.client = Client(config.BINANCE_API_KEY or "", config.BINANCE_API_SECRET or "")

    async def start(self):
        logger.info("Starting Event-Driven Async Scalp Engine...")
        
        # Init Redis (SQLite lock baskısını azaltır — yoksa SQLite fallback)
        if getattr(config, "REDIS_ENABLED", True):
            try:
                from core import redis_state
                redis_state.init(
                    host=config.REDIS_HOST,
                    port=config.REDIS_PORT,
                    db=config.REDIS_DB,
                    password=config.REDIS_PASSWORD,
                )
            except Exception as _re:
                logger.warning("Redis başlatılamadı: %s — SQLite fallback aktif", _re)

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
        execution_svc = ExecutionService()
        asyncio.create_task(execution_svc.start())
        NotificationService()
        self.scanner_service = ScannerService()
        asyncio.create_task(self.scanner_service.start())

        # Start ML Background Training Loop
        asyncio.create_task(self._ml_training_loop())

        # Start DB Maintenance Loop
        asyncio.create_task(self._db_maintenance_loop())

        # Start Heartbeat Loop
        asyncio.create_task(self._heartbeat_loop())

        # Start Ghost Learning Loop
        asyncio.create_task(self._ghost_learning_loop())

        # Start AI Brain Nightly Optimizer
        asyncio.create_task(self._ai_brain_loop())

        # Start Market Regime Loop
        asyncio.create_task(self._market_regime_loop())

        # Start Watchdog
        try:
            from core.watchdog import SystemWatchdog
            db_path = os.path.join(os.path.dirname(__file__), "db", "trading.db")
            self.watchdog = SystemWatchdog(db_path)
            self.watchdog.start()
        except Exception as e:
            logger.error(f"Watchdog başlatılamadı: {e}")

        # Start Telegram Command Manager
        self.telegram_manager = TelegramManager(telegram_delivery.send_message)
        self.telegram_manager.start()

        # Start Macro Service
        try:
            from core.services.macro_service import macro_service
            asyncio.create_task(macro_service.start_background_task())
        except Exception as e:
            logger.error(f"MacroService başlatılamadı: {e}")

        # Start News Service
        try:
            from core.services.news_service import news_service
            asyncio.create_task(news_service.start_background_task())
        except Exception as e:
            logger.error(f"NewsService başlatılamadı: {e}")

        # Start WebSocket Data Feed
        await self.market_data.initialize()
        
        # Sinyal geldiğinde event bus'a bas (örnek - devre dışı bırakıldı)
        async def on_ticker_update(data):
            try:
                from core.market_data import set_cached_price
                if 'symbol' in data and 'last' in data:
                    set_cached_price(data['symbol'], float(data['last']))
            except Exception:
                pass
        self.market_data.on_ticker(on_ticker_update)
        
        # Tüm market için stream başlat
        await self.market_data.start_all_tickers()

        # Keep engine running
        while True:
            await asyncio.sleep(1)

    async def _ml_training_loop(self):
        """Train the ML signal scorer every 24 hours, or when 50 new trades close."""
        from core.ml_signal_scorer import train_model
        from database import get_conn

        def _get_closed_count() -> int:
            try:
                with get_conn() as conn:
                    row = conn.execute("SELECT COUNT(*) FROM trades WHERE status='closed'").fetchone()
                    return int(row[0] or 0)
            except Exception:
                return 0

        last_trained_at_count = _get_closed_count()

        while True:
            try:
                success = await asyncio.to_thread(train_model)
                if success:
                    last_trained_at_count = _get_closed_count()
                    logger.info("[ML] Background training completed (count=%d).", last_trained_at_count)
            except Exception as e:
                logger.error("[ML] Background training failed: %s", e)

            # Sleep 1h at a time; check trade count every hour for early trigger
            for _ in range(24):
                await asyncio.sleep(3600)
                try:
                    current_count = await asyncio.to_thread(_get_closed_count)
                    if current_count - last_trained_at_count >= 50:
                        logger.info(
                            "[ML] 50 yeni trade kapandı (%d→%d), erken yeniden eğitim tetiklendi.",
                            last_trained_at_count, current_count,
                        )
                        break
                except Exception:
                    pass

    async def _heartbeat_loop(self):
        """Update heartbeat in database every 10 seconds."""
        from database import update_bot_status
        from datetime import datetime, timezone
        while True:
            try:
                await asyncio.to_thread(update_bot_status, "heartbeat", datetime.now(timezone.utc).isoformat())
                await asyncio.to_thread(update_bot_status, "status", "running")
            except Exception as e:
                logger.error(f"Heartbeat failed: {e}")
            await asyncio.sleep(10)

    async def stop(self):
        logger.info("Stopping engine...")
        if hasattr(self, 'scanner_service'):
            self.scanner_service.stop()
        if hasattr(self, 'telegram_manager'):
            self.telegram_manager.stop()
        if hasattr(self, 'watchdog'):
            self.watchdog.stop()
        if self.market_data:
            await self.market_data.stop()
        
        try:
            from core.services.macro_service import macro_service
            macro_service.stop()
        except: pass
        
        try:
            from core.services.news_service import news_service
            news_service.stop()
        except: pass
        
        await event_bus.stop()

    async def _ghost_learning_loop(self):
        """Ghost sinyallerini 30 dakikada bir simüle eder ve AI'ya geri besler."""
        from core.ghost_learning import process_pending_results
        await asyncio.sleep(300)
        while True:
            try:
                processed = await asyncio.to_thread(process_pending_results, self.client)
                logger.info(f"[Ghost] process_pending_results tamamlandı: {processed} sinyal")
            except Exception as e:
                logger.error(f"[Ghost] Loop hatası: {e}")
            await asyncio.sleep(1800)

    async def _ai_brain_loop(self):
        """Nightly parametre optimizasyonu — 24 saatte bir çalışır."""
        import sys as _sys
        import os as _os
        _sys.path.insert(0, _os.path.join(_os.path.dirname(__file__), "archive"))
        await asyncio.sleep(3600)
        while True:
            try:
                from ai_brain import analyze_and_adapt, set_client
                set_client(self.client)
                result = await asyncio.to_thread(analyze_and_adapt)
                logger.info(f"[AIBrain] analyze_and_adapt tamamlandı: {str(result)[:120]}")
            except Exception as e:
                logger.error(f"[AIBrain] Nightly loop hatası: {e}")
            await asyncio.sleep(86400)

    async def _market_regime_loop(self):
        """BTC piyasa rejimini 15 dakikada bir tespit eder ve DB'ye yazar.

        Rejim tespiti:
          BULLISH  — BTC 1h + 4h her ikisi de bullish
          BEARISH  — BTC 1h + 4h her ikisi de bearish
          CHOPPY   — 1h ile 4h ters yönde VEYA BTC 15m ATR% > 1.5%
          NEUTRAL  — yukarıdakilerin hiçbiri
        """
        import pandas as pd
        from core.trend_engine import TrendEngine
        from database import set_market_regime

        trend_engine = TrendEngine(self.client)
        prev_regime = "NEUTRAL"
        await asyncio.sleep(90)  # Startup'ta diğer servisler oturtu
        while True:
            try:
                btc_trend = await asyncio.to_thread(trend_engine.get_btc_trend)
                regime = "NEUTRAL"

                if btc_trend == "BULLISH":
                    regime = "BULLISH"
                elif btc_trend == "BEARISH":
                    regime = "BEARISH"
                else:
                    # NEUTRAL BTC: 1h vs 4h ters yöndeyse CHOPPY
                    t1h = await asyncio.to_thread(trend_engine.get_1h_trend, "BTCUSDT")
                    t4h = await asyncio.to_thread(trend_engine.get_4h_trend, "BTCUSDT")
                    if t1h != "NEUTRAL" and t4h != "NEUTRAL" and t1h != t4h:
                        regime = "CHOPPY"
                    else:
                        # ATR volatility check: 15m ATR% > 1.5% → CHOPPY
                        try:
                            df15 = await asyncio.to_thread(
                                trend_engine.get_candles, "BTCUSDT", "15m", 30
                            )
                            if not df15.empty:
                                h, l, c = df15["high"], df15["low"], df15["close"]
                                tr = pd.concat(
                                    [h - l, (h - c.shift()).abs(), (l - c.shift()).abs()],
                                    axis=1,
                                ).max(axis=1)
                                atr_pct = float(tr.rolling(14).mean().iloc[-1]) / float(c.iloc[-1])
                                if atr_pct > 0.015:
                                    regime = "CHOPPY"
                        except Exception:
                            pass

                await asyncio.to_thread(set_market_regime, regime)
                logger.info("[Regime] Piyasa rejimi: %s (BTC=%s)", regime, btc_trend)

                if regime != prev_regime:
                    _emoji = {"BULLISH": "📈", "BEARISH": "📉", "CHOPPY": "⚡", "NEUTRAL": "➡️"}.get(regime, "")
                    _desc = {
                        "BULLISH": "Trend piyasası — LONG sinyaller öncelikli",
                        "BEARISH": "Düşüş trendi — SHORT sinyaller öncelikli",
                        "CHOPPY": "Kaotik piyasa — eşik yükseltildi (min A+)",
                        "NEUTRAL": "Normal piyasa — standart kurallar geçerli",
                    }.get(regime, "")
                    try:
                        import telegram_delivery
                        await asyncio.to_thread(
                            telegram_delivery.send_message,
                            f"{_emoji} <b>Piyasa Rejimi Değişti</b>\n"
                            f"{prev_regime} → <b>{regime}</b>\n"
                            f"{_desc}",
                        )
                    except Exception:
                        pass
                    prev_regime = regime

            except Exception as exc:
                logger.error("[Regime] Loop hatası: %s", exc)
            await asyncio.sleep(900)  # 15 dakika

    async def _db_maintenance_loop(self):
        """Perform SQLite VACUUM and WAL checkpoint every 24 hours."""
        from database import get_conn
        
        def _run_vacuum():
            logger.info("[Maintenance] Starting daily SQLite maintenance...")
            try:
                with get_conn() as conn:
                    conn.execute("PRAGMA wal_checkpoint(TRUNCATE);")
                    conn.execute("VACUUM;")
                logger.info("[Maintenance] SQLite maintenance completed.")
            except Exception as e:
                logger.error(f"[Maintenance] Failed: {e}")

        while True:
            await asyncio.sleep(86400) # 24h
            await asyncio.to_thread(_run_vacuum)

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
