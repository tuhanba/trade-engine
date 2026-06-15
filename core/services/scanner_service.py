"""
core/services/scanner_service.py — Scanner Aşaması v6.1

Değişiklikler:
  - KILL_SWITCH_ACTIVATED: scanner pause (not stop) — scans resume on deactivation
  - KILL_SWITCH_DEACTIVATED: resumes scan loop
  - Dashboard funnel counters written to bot_status
"""

import logging
import asyncio
from core.event_bus import event_bus
from core.event_types import Event, EventType
from core.async_market_scanner import AsyncMarketScanner
from database import get_open_trades

logger = logging.getLogger("ax.services.scanner")


class ScannerService:
    def __init__(self, interval_seconds: int = 45):
        self.scanner  = AsyncMarketScanner()
        self.interval = interval_seconds
        self._running = False
        self._paused  = False
        self._scan_count = 0
        event_bus.subscribe(EventType.KILL_SWITCH_ACTIVATED, self.handle_kill_switch)
        event_bus.subscribe(EventType.KILL_SWITCH_DEACTIVATED, self.handle_kill_switch_deactivated)

    async def handle_kill_switch(self, event: Event):
        logger.critical("[ScannerService] KILL SWITCH — scanner duraklatılıyor (paused).")
        self._paused = True

    async def handle_kill_switch_deactivated(self, event: Event):
        logger.info("[ScannerService] Kill switch kaldırıldı — scanner devam ediyor.")
        self._paused = False

    async def start(self):
        self._running = True
        logger.info("[ScannerService] Başladı (interval=%ds)", self.interval)

        while self._running:
            if self._paused:
                await asyncio.sleep(30)
                continue

            # Check Telegram Pause/Finish state
            try:
                from database import get_state
                tg_paused = await asyncio.to_thread(get_state, "tg_is_paused")
                tg_finish = await asyncio.to_thread(get_state, "tg_is_finish_mode")
                
                if tg_paused == "True":
                    logger.debug("[ScannerService] Bot is paused via Telegram (tg_is_paused=True). Skipping scan.")
                    await asyncio.sleep(10)
                    continue
                    
                if tg_finish == "True":
                    open_trades = await asyncio.to_thread(get_open_trades)
                    if not open_trades:
                        logger.info("[ScannerService] Finish mode completed. No open trades. Skipping scan.")
                        await asyncio.sleep(10)
                        continue
                    else:
                        logger.debug("[ScannerService] Bot is in Finish mode but open trades exist. Skipping scan to prevent new entries.")
                        await asyncio.sleep(10)
                        continue
            except Exception as e:
                logger.error("[ScannerService] Pause/Finish state check error: %s", e)

            try:
                open_trades  = await asyncio.to_thread(get_open_trades)
                open_symbols = {t["symbol"] for t in open_trades}

                candidates = await self.scanner.scan()
                self._scan_count += 1

                from database import is_coin_muted
                eligible = []
                for c in candidates:
                    sym = c.get("symbol")
                    if c.get("status") in ("Eligible", "Watch") and sym not in open_symbols:
                        is_muted = await asyncio.to_thread(is_coin_muted, sym)
                        if is_muted:
                            logger.info(f"[ScannerService] Skipping muted symbol: {sym}")
                            continue
                        eligible.append(c)

                for c in eligible:
                    await event_bus.publish(Event(type=EventType.SCANNED, payload=c))

                # Dashboard funnel sayaçları
                try:
                    from database import update_bot_status
                    await asyncio.to_thread(
                        update_bot_status, "pipeline_scanned", str(len(candidates))
                    )
                    await asyncio.to_thread(
                        update_bot_status, "pipeline_eligible", str(len(eligible))
                    )
                    await asyncio.to_thread(
                        update_bot_status, "scanner_total_loops", str(self._scan_count)
                    )
                except Exception as _e:
                    logger.debug("[ScannerService] bot_status yazılamadı: %s", _e)

                logger.debug(
                    "[ScannerService] #%d → taranan=%d eligible=%d open_symbols=%d",
                    self._scan_count, len(candidates), len(eligible), len(open_symbols),
                )

                await asyncio.sleep(self.interval)

            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error("[ScannerService] döngü hatası: %s", e)
                await asyncio.sleep(5)

    def stop(self):
        self._running = False
        logger.info("[ScannerService] Durduruldu.")
