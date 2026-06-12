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
_log_dir = os.environ.get("LOG_DIR", "logs")
os.makedirs(_log_dir, exist_ok=True)
_log_file = os.path.join(_log_dir, "bot.log")

from logging.handlers import RotatingFileHandler as _RFH
_file_handler = _RFH(_log_file, maxBytes=20 * 1024 * 1024, backupCount=3, encoding="utf-8")
_file_handler.setFormatter(logging.Formatter("%(asctime)s | %(levelname)s | %(name)s | %(message)s"))

logging.basicConfig(
    level=logging.INFO,
    force=True,
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
    handlers=[
        logging.StreamHandler(sys.stdout),
        _file_handler,
    ]
)
logger = logging.getLogger("ax.async_engine")

class AsyncScalpEngine:
    def __init__(self):
        self.market_data = AsyncMarketDataService(config.BINANCE_API_KEY or "", config.BINANCE_API_SECRET or "")
        # Robust Client initialization with fallback API endpoints
        self.client = None
        api_endpoints = [
            'https://api.binance.com/api',
            'https://api1.binance.com/api',
            'https://api2.binance.com/api',
            'https://api3.binance.com/api'
        ]
        import binance.client
        for url in api_endpoints:
            try:
                binance.client.Client.API_URL = url
                logger.info(f"[Engine] Attempting to initialize Client with endpoint: {url}")
                self.client = Client(config.BINANCE_API_KEY or "", config.BINANCE_API_SECRET or "")
                logger.info(f"[Engine] Client successfully initialized with endpoint: {url}")
                break
            except Exception as e:
                logger.warning(f"[Engine] Failed to connect using endpoint {url}: {e}")
        
        if self.client is None:
            logger.error("[Engine] All Binance API endpoints failed to connect. Bypassing ping to allow paper trading boot...")
            binance.client.Client.ping = lambda self: {}
            self.client = Client(config.BINANCE_API_KEY or "", config.BINANCE_API_SECRET or "")
        self._last_trade_opened_at = time.time()

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

        # Initialize last trade opened time from DB if possible
        try:
            from database import get_conn
            from execution_engine import parse_utc_datetime
            with get_conn() as conn:
                row = conn.execute("SELECT open_time FROM trades ORDER BY id DESC LIMIT 1").fetchone()
                if row and row[0]:
                    dt = parse_utc_datetime(row[0])
                    self._last_trade_opened_at = dt.timestamp()
                    logger.info(f"[Engine] Last trade opened time loaded from DB: {row[0]}")
        except Exception as _e:
            logger.debug(f"[Engine] Could not load last trade time from DB: {_e}")

        # Subscribe to TRADE_OPENED event
        async def on_trade_opened(event):
            self._last_trade_opened_at = time.time()
            logger.info("[ThresholdDecay] Trade opened event received. Resetting inactivity decay tracker.")
            try:
                from database import get_conn
                import config
                base_thr = getattr(config, "_STATIC_DEFAULTS", {}).get("TRADE_THRESHOLD", 55.0)
                with get_conn() as conn:
                    conn.execute("""
                        INSERT INTO system_state (key, value, updated_at)
                        VALUES ('trade_threshold', ?, datetime('now'))
                        ON CONFLICT(key) DO UPDATE SET value=?, updated_at=datetime('now')
                    """, (str(base_thr), str(base_thr)))
                    conn.commit()
                logger.info(f"[ThresholdDecay] Reset trade_threshold in system_state to baseline: {base_thr:.1f}")
            except Exception as _e:
                logger.debug(f"[ThresholdDecay] Failed to reset threshold in DB: {_e}")

        from core.event_types import Event, EventType
        event_bus.subscribe(EventType.TRADE_OPENED, on_trade_opened)

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

        # Start Self-Healing Parameter Optimization Loop
        asyncio.create_task(self._self_healing_optuna_loop())

        # Start Weekly Telegram Performance Digest Loop
        asyncio.create_task(self._weekly_digest_loop())

        # Start Optuna Hyperparameter Tuner Loop
        asyncio.create_task(self._optuna_tuning_loop())

        # Start Friday CEO Agent Loop
        self.friday_ceo = None
        try:
            from core.friday_ceo import FridayCeo
            self.friday_ceo = FridayCeo(self.client)
            asyncio.create_task(self._friday_ceo_loop())
            asyncio.create_task(self._friday_monitor_loop())
        except Exception as e:
            logger.error(f"Friday CEO başlatılamadı: {e}")

        # Start Watchdog
        try:
            from core.watchdog import SystemWatchdog
            db_path = config.DB_PATH
            self.watchdog = SystemWatchdog(db_path)
            self.watchdog.start()
        except Exception as e:
            logger.error(f"Watchdog başlatılamadı: {e}")

        # Start Telegram Command Manager
        self.telegram_manager = TelegramManager(telegram_delivery.send_message, friday_ceo=self.friday_ceo)
        self.telegram_manager.start()

        # Recover queued Telegram messages on startup
        try:
            telegram_delivery.recover_queued_messages()
        except Exception as e:
            logger.error(f"Telegram queue recovery failed: {e}")

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

        # Start Sentiment Scraper Agent
        try:
            from core.services.sentiment_scraper import sentiment_scraper
            asyncio.create_task(sentiment_scraper.start_background_task())
        except Exception as e:
            logger.error(f"SentimentScraper başlatılamadı: {e}")

        # Start WebSocket Data Feed
        await self.market_data.initialize()
        
        # Sinyal geldiğinde event bus'a bas (örnek - devre dışı bırakıldı)
        def on_ticker_update(data):
            try:
                from core.market_data import set_cached_price, set_cached_ticker
                sym = data.get('s')
                if sym:
                    price_str = data.get('c')
                    if price_str:
                        set_cached_price(sym, float(price_str))
                    set_cached_ticker(sym, data)
            except Exception:
                pass
        self.market_data.on_ticker(on_ticker_update)
        
        # Tüm market için stream başlat
        await self.market_data.start_all_tickers()

        try:
            from telegram_delivery import send_message
            send_message("🟢 <b>Sistem Başlatıldı!</b>\n🤖 Asenkron Scalp Motoru piyasayı taramaya başladı.")
        except Exception:
            pass

        # Engine çalışmaya devam eder — shutdown ana coroutine'de yönetilir
        # (while True döngüsü kaldırıldı; ana task CancelledError ile bitecek)

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

    async def _optuna_tuning_loop(self):
        """Run Optuna parameter optimization loop every 4 hours."""
        from core.hyperparameter_tuner import optimize_parameters
        # Delay startup execution by 10 minutes (600s) to let the bot stabilize
        await asyncio.sleep(600)
        while True:
            try:
                await asyncio.to_thread(optimize_parameters)
            except Exception as e:
                logger.error(f"[Tuner Loop] Background optimization task failed: {e}")
            await asyncio.sleep(14400)  # 4 hours

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

    async def _friday_ceo_loop(self):
        """Run Friday CEO Agent loop using dynamic interval."""
        # Initial startup delay (e.g. 5 minutes) to let bot collect statistics
        await asyncio.sleep(300)
        while True:
            try:
                await asyncio.to_thread(self.friday_ceo.evaluate_and_decide)
            except Exception as e:
                logger.error(f"[Friday CEO Loop] Task failed: {e}")
            interval = getattr(config, "FRIDAY_CEO_LOOP_INTERVAL", 3600)
            await asyncio.sleep(max(60, interval))

    async def _friday_monitor_loop(self):
        """Run Friday CEO Autonomous Monitoring loop every 5 minutes."""
        # Initial delay to let the bot stabilize (e.g., 2 minutes)
        await asyncio.sleep(120)
        while True:
            try:
                if self.friday_ceo:
                    await asyncio.to_thread(self.friday_ceo.run_autonomous_monitoring)
            except Exception as e:
                logger.error(f"[Friday Monitor Loop] Task failed: {e}")
            await asyncio.sleep(300) # 5 minutes

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
        except Exception:
            pass

        try:
            from core.services.news_service import news_service
            news_service.stop()
        except Exception:
            pass

        try:
            from core.services.sentiment_scraper import sentiment_scraper
            sentiment_scraper.stop()
        except Exception:
            pass
        
        await event_bus.stop()

    def _update_trade_threshold_in_db(self, new_val: float):
        try:
            from database import get_conn
            with get_conn() as conn:
                conn.execute("""
                    INSERT INTO system_state (key, value, updated_at)
                    VALUES ('trade_threshold', ?, datetime('now'))
                    ON CONFLICT(key) DO UPDATE SET value=?, updated_at=datetime('now')
                """, (str(new_val), str(new_val)))
                conn.commit()
        except Exception as e:
            logger.error(f"[ThresholdDecay] DB update error: {e}")

    async def _ghost_learning_loop(self):
        """Ghost sinyallerini sık aralıklarla (12s) simüle eder, periyodik olarak otonom eşikleri analiz eder ve uygular."""
        from core.ghost_learning import (
            process_pending_results,
            generate_threshold_suggestions,
            apply_ghost_suggestions_v2,
        )
        from core.market_data import _PRICE_CACHE
        
        # 5 dk bekleme başlangıçta stabilizasyon için
        await asyncio.sleep(300)
        
        last_suggestion_time = 0.0
        SUGGESTION_INTERVAL = 600.0  # 10 dakika (600 saniye)
        
        while True:
            try:
                # 1. Bekleyen ghost/paper sinyallerini mevcut önbellek fiyatlarıyla simüle et
                cached_prices = dict(_PRICE_CACHE)
                
                processed = await asyncio.to_thread(process_pending_results, self.client, cached_prices)
                if processed > 0:
                    logger.debug(f"[Ghost] process_pending_results (cached): {processed} sinyal işlendi")
                
                # 2. Periyodik optimizasyon analizi ve eşik güncellemesi (10 dakikada bir)
                now = time.time()
                if now - last_suggestion_time >= SUGGESTION_INTERVAL:
                    logger.info("[Ghost] Otonom optimizasyon analizi tetikleniyor...")
                    await asyncio.to_thread(generate_threshold_suggestions)
                    applied = await asyncio.to_thread(apply_ghost_suggestions_v2)
                    if applied:
                        logger.info(f"[Ghost] Otonom optimizasyon uygulandı: {len(applied)} kural")
                    
                    # Run dynamic weight auto-tuning (Phase I Upgrade)
                    try:
                        from core.weight_tuner import tune_agent_weights
                        logger.info("[Ghost] Otonom ajan ağırlıkları auto-tuning tetikleniyor...")
                        tuned = await asyncio.to_thread(tune_agent_weights)
                        if tuned:
                            logger.info(f"[Ghost] Otonom ajan ağırlıkları güncellendi: {list(tuned.keys())}")
                    except Exception as _wt_err:
                        logger.error(f"[Ghost] Weight tuner execution error: {_wt_err}")
                        
                    last_suggestion_time = now

                # 3. İnaktivite Eşik Çürümesi (Threshold Decay)
                # Son trade'den bu yana geçen süreyi kontrol et
                # 6 saat = 21600 saniye. 2 saat = 7200 saniye.
                elapsed_since_trade = now - self._last_trade_opened_at
                if elapsed_since_trade >= 21600:
                    decay_steps = int((elapsed_since_trade - 21600) / 7200)
                    decay_amount = float(decay_steps + 1)
                    
                    import config
                    current_thr = getattr(config, "TRADE_THRESHOLD", 55.0)
                    base_thr = getattr(config, "_STATIC_DEFAULTS", {}).get("TRADE_THRESHOLD", 55.0)
                    
                    target_thr = max(base_thr - decay_amount, 50.0)
                    
                    if current_thr > target_thr:
                        logger.info(
                            f"[ThresholdDecay] Inactivity detected ({elapsed_since_trade/3600:.1f} hours). "
                            f"Decaying trade_threshold: {current_thr:.1f} -> {target_thr:.1f}"
                        )
                        await asyncio.to_thread(self._update_trade_threshold_in_db, target_thr)

            except Exception as e:
                logger.error(f"[Ghost] Loop hatası: {e}")
                
            await asyncio.sleep(12)  # Yüksek çözünürlüklü simülasyon (12 saniye)

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
        """BTC piyasa rejimini 15 dakikada bir ML (KMeans) ile sınıflandırır ve DB'ye yazar."""
        from core.trend_engine import MLMarketRegimeClassifier
        from database import set_market_regime

        classifier = MLMarketRegimeClassifier(self.client)
        prev_regime = "NEUTRAL"
        await asyncio.sleep(90)  # Startup'ta diğer servisler otursun
        while True:
            try:
                regime = await asyncio.to_thread(classifier.classify, "BTCUSDT")
                await asyncio.to_thread(set_market_regime, regime)
                logger.info("[Regime] Piyasa rejimi otonom sınıflandırıldı: %s", regime)

                if regime != prev_regime:
                    _emoji = {
                        "TRENDING_HIGH_VOL": "📈🔥",
                        "TRENDING_LOW_VOL": "📈⏳",
                        "CHOPPY_HIGH_VOL": "⚡🔥",
                        "CHOPPY_LOW_VOL": "⚡❄️",
                        "NEUTRAL": "➡️"
                    }.get(regime, "➡️")
                    _desc = {
                        "TRENDING_HIGH_VOL": "Yüksek Volatiliteli Trend Piyasası (Dinamik Risk: 1.2x, Eşik: -2)",
                        "TRENDING_LOW_VOL": "Düşük Volatiliteli Trend Piyasası (Dinamik Risk: 1.0x, Eşik: 0)",
                        "CHOPPY_HIGH_VOL": "Yüksek Volatiliteli Dalgalı Piyasa (Dinamik Risk: 0.5x, Eşik: +5)",
                        "CHOPPY_LOW_VOL": "Düşük Volatiliteli Dalgalı Piyasa (Dinamik Risk: 0.75x, Eşik: +3)",
                    }.get(regime, "Normal piyasa")
                    try:
                        import telegram_delivery
                        await asyncio.to_thread(
                            telegram_delivery.send_message,
                            f"{_emoji} <b>Piyasa Rejimi Değişti (ML)</b>\n"
                            f"{prev_regime} → <b>{regime}</b>\n"
                            f"{_desc}",
                        )
                    except Exception:
                        pass
                    prev_regime = regime

            except Exception as exc:
                logger.error("[Regime] Loop hatası: %s", exc)
            await asyncio.sleep(900)  # 15 dakika

    async def _self_healing_optuna_loop(self):
        """
        Periodically check if the win rate of the last 20 closed trades has dropped below 50%.
        If so, automatically run Optuna optimization on ghost_signals to tune RSI_LIMIT and CVD_FILTER_VAL.
        """
        await asyncio.sleep(300)  # Startup delay
        while True:
            try:
                from core.hyperparameter_tuner import check_win_rate_and_trigger_opt
                import config
                
                triggered = await asyncio.to_thread(check_win_rate_and_trigger_opt, config.DB_PATH)
                if triggered:
                    logger.info("[Self-Healing] Win rate of last 20 trades is below 50%. Triggering parameter optimization...")
                    
                    from core.hyperparameter_tuner import optimize_ghost_filters
                    res = await asyncio.to_thread(optimize_ghost_filters, config.DB_PATH)
                    if res:
                        best_rsi_limit, best_cvd_filter_val, best_val = res
                        
                        # Save to db
                        from database import update_system_state
                        await asyncio.to_thread(update_system_state, "rsi_limit", str(round(best_rsi_limit, 1)))
                        await asyncio.to_thread(update_system_state, "cvd_filter_val", str(round(best_cvd_filter_val, 4)))
                        
                        logger.info(f"[Self-Healing] Parameters updated: RSI_LIMIT={best_rsi_limit:.1f}, CVD_FILTER_VAL={best_cvd_filter_val:.4f}")
                        
                        # Send voice note
                        msg = (
                            f"Canım boss'um, son yirmi işlemimizdeki başarı oranı yüzde ellinin altına düşünce hemen işe koyuldum "
                            f"ve ghost sinyallerimizi otonom olarak taradım! Piyasaya daha iyi uyum sağlamak için "
                            f"yeni RSI limitini {best_rsi_limit:.1f} ve yeni CVD filtre değerini {best_cvd_filter_val:.4f} olarak güncelledim. "
                            f"Artık çok daha güvendeyiz tatlım, işlemlerimiz ışıldasın!"
                        )
                        
                        if self.friday_ceo:
                            voice_bytes = await asyncio.to_thread(self.friday_ceo.generate_voice_from_text, msg)
                            if voice_bytes:
                                import telegram_delivery
                                await asyncio.to_thread(
                                    telegram_delivery.send_voice, 
                                    voice_bytes, 
                                    caption="Friday Otonom Parametre İyileştirme"
                                )
                                logger.info("[Self-Healing] Sent voice note to boss.")
                            else:
                                import telegram_delivery
                                await asyncio.to_thread(
                                    telegram_delivery.send_message,
                                    f"👻 <b>Friday Otonom Parametre İyileştirme</b>\n\n{msg}"
                                )
                        else:
                            import telegram_delivery
                            await asyncio.to_thread(
                                telegram_delivery.send_message,
                                f"👻 <b>Friday Otonom Parametre İyileştirme</b>\n\n{msg}"
                            )
                else:
                    logger.debug("[Self-Healing] Win rate check passed (>=50% or not enough trades).")
            except Exception as e:
                logger.error(f"[Self-Healing] Loop error: {e}")
                
            await asyncio.sleep(1800)  # Check every 30 minutes

    async def _db_maintenance_loop(self):
        """Perform SQLite VACUUM and WAL checkpoint every 12 hours."""
        from database import get_conn
        
        def _run_vacuum():
            logger.info("[Maintenance] Starting semi-daily SQLite maintenance...")
            try:
                with get_conn() as conn:
                    # 1. Prune signal_events older than 30 days
                    c1 = conn.execute("DELETE FROM signal_events WHERE created_at < datetime('now', '-30 days')").rowcount
                    # 2. Prune signal_candidates older than 30 days
                    c2 = conn.execute("DELETE FROM signal_candidates WHERE created_at < datetime('now', '-30 days')").rowcount
                    # 3. Prune telegram_messages older than 30 days
                    c3 = conn.execute("DELETE FROM telegram_messages WHERE created_at < datetime('now', '-30 days')").rowcount
                    # 4. Prune scanned_coins older than 30 days
                    c4 = conn.execute("DELETE FROM scanned_coins WHERE scanned_at < datetime('now', '-30 days')").rowcount
                    # 5. Prune ghost_results and ghost_signals older than 60 days
                    c5 = conn.execute("""
                        DELETE FROM ghost_results 
                        WHERE ghost_id IN (SELECT id FROM ghost_signals WHERE created_at < datetime('now', '-60 days'))
                    """).rowcount
                    c6 = conn.execute("DELETE FROM ghost_signals WHERE created_at < datetime('now', '-60 days')").rowcount
                    
                    logger.info(
                        f"[Maintenance] Pruned database records: signal_events={c1}, "
                        f"signal_candidates={c2}, telegram_messages={c3}, scanned_coins={c4}, "
                        f"ghost_results={c5}, ghost_signals={c6}"
                    )
                    
                    conn.execute("PRAGMA wal_checkpoint(TRUNCATE);")
                    conn.execute("VACUUM;")
                logger.info("[Maintenance] SQLite maintenance completed.")
                
                # Otonom günlük sıcak yedek oluşturma (hot backup)
                try:
                    from database import create_hot_backup
                    create_hot_backup()
                except Exception as b_err:
                    logger.error(f"[Maintenance] Hot backup failed: {b_err}")
            except Exception as e:
                logger.error(f"[Maintenance] Failed: {e}")

        while True:
            await asyncio.sleep(43200) # 12h
            await asyncio.to_thread(_run_vacuum)

    async def _weekly_digest_loop(self):
        """Haftalık özet raporunu Pazar günleri saat 21:00 UTC'de otomatik gönderir."""
        from database import get_system_state, update_system_state
        from datetime import datetime, timezone
        import telegram_delivery
        
        while True:
            try:
                now = datetime.now(timezone.utc)
                # Sunday (weekday=6) and 21:00 UTC
                if now.weekday() == 6 and now.hour == 21:
                    last_sent = get_system_state("last_weekly_digest_date", default="")
                    today_str = now.strftime("%Y-%m-%d")
                    
                    if last_sent != today_str:
                        logger.info("[WeeklyDigest] Haftalık rapor otomatik oluşturuluyor ve gönderiliyor...")
                        # Send to Telegram
                        await asyncio.to_thread(telegram_delivery.send_weekly_digest)
                        # Update state to avoid duplicate sending
                        await asyncio.to_thread(update_system_state, "last_weekly_digest_date", today_str)
            except Exception as e:
                logger.error(f"[WeeklyDigest] Hata oluştu: {e}")
                
            await asyncio.sleep(600)  # 10 dakikada bir kontrol et

def handle_exception(loop, context):
    msg = context.get("exception", context["message"])
    logger.error(f"Caught exception: {msg}")
    try:
        from telegram_delivery import send_message
        import traceback
        exc = context.get("exception")
        tb_str = ""
        if exc:
            tb_str = "\n<code>" + "".join(traceback.format_exception(type(exc), exc, exc.__traceback__))[:1500] + "</code>"
        send_message(f"⚠️ <b>KRİTİK HATA (Asyncio):</b>\n{str(msg)[:500]}{tb_str}")
    except Exception:
        pass

async def main():
    loop = asyncio.get_running_loop()
    loop.set_exception_handler(handle_exception)

    engine = AsyncScalpEngine()
    # SIGTERM FIX: Event-tabanlı graceful shutdown.
    # shutdown_signal() sadece event set eder; ana coroutine
    # event'i bekleyip engine.stop()'u await eder.
    # Böylece systemd SIGTERM sonrası SIGKILL atmak zorunda kalmaz.
    _shutdown_event = asyncio.Event()

    def shutdown_signal():
        logger.info("[Shutdown] SIGTERM/SIGINT alındı — graceful shutdown başlıyor...")
        _shutdown_event.set()

    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, shutdown_signal)
        except NotImplementedError:
            try:
                def _handle_sig(signum, frame):
                    logger.info(f"[Shutdown] Signal {signum} received — triggering graceful shutdown...")
                    loop.call_soon_threadsafe(_shutdown_event.set)
                signal.signal(sig, _handle_sig)
            except Exception as _sig_err:
                logger.warning(f"Could not register signal handler for {sig}: {_sig_err}")

    # Engine'i başlat (kurulumu tamamla)
    try:
        await engine.start()
    except Exception as e:
        logger.error(f"Engine başlatılırken kritik hata oluştu: {e}")
        try:
            from telegram_delivery import send_message
            import traceback
            tb_str = "\n<code>" + "".join(traceback.format_exception(type(e), e, e.__traceback__))[:1500] + "</code>"
            send_message(f"🚨 <b>KRİTİK ENGINE BAŞLATMA HATASI:</b>\n{str(e)[:500]}{tb_str}")
        except Exception:
            pass
        return

    # Sadece shutdown sinyalini bekle
    await _shutdown_event.wait()

    # Temiz kapanma
    logger.info("[Shutdown] Engine durduruluyor...")
    try:
        await asyncio.wait_for(engine.stop(), timeout=8.0)
    except asyncio.TimeoutError:
        logger.warning("[Shutdown] engine.stop() 8 saniyede tamamlanamadı, zorla çıkılıyor.")

    logger.info("[Shutdown] Temiz kapanma tamamlandı.")


if __name__ == "__main__":
    if sys.platform == "win32":
        asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())
    asyncio.run(main())
