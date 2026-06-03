import logging
import asyncio
from core.event_bus import event_bus
from core.event_types import Event, EventType
from core.trigger_engine import TriggerEngine
from database import save_candidate_signal

logger = logging.getLogger("ax.services.trigger")

class TriggerService:
    def __init__(self, client):
        self.trigger_engine = TriggerEngine(client)
        event_bus.subscribe(EventType.TREND_CHECKED, self.handle_trend_checked)

    async def handle_trend_checked(self, event: Event):
        payload = event.payload
        symbol = payload.get("symbol")
        trend_result = payload.get("trend_result", {})
        signal_id = payload.get("signal_id")
        tradeability_score = payload.get("tradeability_score")

        try:
            # Wrap synchronous call
            trigger_result = await asyncio.to_thread(
                self.trigger_engine.analyze,
                symbol,
                trend_result["direction"],
                trend_result.get("btc_trend", "NEUTRAL"),
                trend_confluence=trend_result.get("confluence_raw", 1)
            )

            # Regime-Switching Filter: Choppy piyasada min A+ kalitesi şartı
            try:
                from database import get_market_regime
                regime = await asyncio.to_thread(get_market_regime)
                quality = trigger_result.get("quality", "C")
                if regime in ("CHOPPY", "CHOPPY_HIGH_VOL", "CHOPPY_LOW_VOL") and quality not in ("S", "A+"):
                    logger.info(f"[TriggerService] {symbol} rejected by Regime Filter: quality {quality} in CHOPPY market.")
                    try:
                        from database import save_signal_event
                        await asyncio.to_thread(
                            save_signal_event, signal_id, "REGIME_REJECTED",
                            symbol=symbol, reject_reason=f"choppy_market_quality_{quality}"
                        )
                    except Exception:
                        pass
                    return
            except Exception as rex:
                logger.debug(f"[TriggerService] Regime check failed: {rex}")

            if trigger_result["quality"] == "D":
                logger.debug(f"[TriggerService] {symbol} rejected: quality D")
                try:
                    from database import save_signal_event
                    _reject = trigger_result.get("reject_reason", "quality_D")
                    await asyncio.to_thread(
                        save_signal_event, signal_id, "TRIGGER_REJECTED",
                        symbol=symbol, reject_reason=_reject
                    )
                except Exception:
                    pass
                return

            next_payload = {
                "symbol": symbol,
                "signal_id": signal_id,
                "tradeability_score": tradeability_score,
                "trend_result": trend_result,
                "trigger_result": trigger_result
            }

            # DB'ye kaydet
            try:
                candidate_payload = {
                    "signal_id": signal_id,
                    "symbol": symbol,
                    "direction": trend_result["direction"],
                    "entry": trigger_result.get("entry_price", 0),
                    "sl": trigger_result.get("stop_loss", 0),
                    "tp1": trigger_result.get("tp1", 0),
                    "setup_quality": trigger_result.get("quality", "C"),
                    "decision": "PENDING",
                    "market_regime": trend_result.get("market_trend", "NEUTRAL")
                }
                candidate_id = await asyncio.to_thread(save_candidate_signal, candidate_payload)
                if candidate_id:
                    next_payload["candidate_id"] = candidate_id
            except Exception as e:
                logger.error(f"[TriggerService] DB Save Error: {e}")

            await event_bus.publish(Event(type=EventType.TRIGGER_CHECKED, payload=next_payload))
            logger.debug(f"[TriggerService] {symbol} passed trigger check: quality {trigger_result['quality']}")

        except Exception as e:
            logger.error(f"[TriggerService] Error processing {symbol}: {e}")
