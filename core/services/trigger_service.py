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

            # Regime-Switching Filter: Choppy piyasada kalite filtresi kontrolü
            try:
                import config
                if getattr(config, "REGIME_FILTER_ENABLED", True):
                    from database import get_market_regime
                    regime = await asyncio.to_thread(get_market_regime)
                    quality = trigger_result.get("quality", "C")
                    min_quality = getattr(config, "REGIME_FILTER_MIN_QUALITY_IN_CHOPPY", "A+")
                    
                    quality_order = ["C", "B", "A", "A+", "S"]
                    if regime in ("CHOPPY", "CHOPPY_HIGH_VOL", "CHOPPY_LOW_VOL"):
                        import sys
                        is_paper = (getattr(config, "EXECUTION_MODE", "paper") == "paper")
                        is_testing = "pytest" in sys.modules or "unittest" in sys.modules
                        bypass_shields = getattr(config, "BYPASS_LIVE_RISK_SHIELDS", False)
                        if not is_testing:
                            bypass_shields = bypass_shields or is_paper
                        if not bypass_shields:
                            q_idx = quality_order.index(quality) if quality in quality_order else -1
                            min_idx = quality_order.index(min_quality) if min_quality in quality_order else -1
                            if q_idx < min_idx:
                                bypass_regime = False
                                if getattr(config, "GHOST_WARMUP_ENABLED", False):
                                    import database
                                    lookback = getattr(config, "GHOST_WARMUP_TRADES_LOOKBACK", 10)
                                    min_win_rate = getattr(config, "GHOST_WARMUP_MIN_WIN_RATE", 0.55)
                                    win_rate, count = await asyncio.to_thread(
                                        database.get_ghost_warmup_win_rate, None, lookback
                                    )
                                    if count >= 3 and win_rate >= min_win_rate:
                                        logger.info(f"[Ghost Warm-up] Bypassing regime filter in choppy market: global virtual win rate {win_rate:.2%} (count={count}) >= {min_win_rate:.2%}")
                                        bypass_regime = True
                                
                                if not bypass_regime:
                                    logger.info(f"[TriggerService] {symbol} rejected by Regime Filter: quality {quality} is below min {min_quality} in CHOPPY market.")
                                    try:
                                        from database import save_signal_event
                                        await asyncio.to_thread(
                                            save_signal_event, signal_id, "REGIME_REJECTED",
                                            symbol=symbol, reject_reason=f"choppy_market_quality_{quality}_below_{min_quality}"
                                        )
                                    except Exception:
                                        pass
                                    try:
                                        from websocket_events import event_manager
                                        if event_manager:
                                            event_manager.broadcast_signal_rejected(symbol, trend_result.get("direction", "LONG"), f"regime_filter_quality_{quality}_below_{min_quality}")
                                    except Exception:
                                        pass
                                    
                                    # Log as ghost signal
                                    try:
                                        from core.ghost_learning import maybe_ghost_log
                                        sig_to_log = {
                                            "symbol": symbol,
                                            "direction": trend_result.get("direction", "LONG"),
                                            "entry": trigger_result.get("entry_price") or trigger_result.get("entry") or 0.0,
                                            "sl": trigger_result.get("stop_loss") or trigger_result.get("sl") or 0.0,
                                            "tp1": trigger_result.get("tp1") or 0.0,
                                            "tp2": trigger_result.get("tp2") or 0.0,
                                            "tp3": trigger_result.get("tp3") or 0.0,
                                            "confidence": float(tradeability_score or 50) / 100.0,
                                            "trigger_type": trigger_result.get("quality", "C"),
                                            "final_score": tradeability_score or 50.0,
                                            "market_regime": regime,
                                            "rsi": trigger_result.get("rsi5") or 50.0,
                                            "cvd_slope": trigger_result.get("cvd_slope") or 0.0,
                                        }
                                        await asyncio.to_thread(maybe_ghost_log, sig_to_log, reason="SKIPPED_BY_REGIME")
                                    except Exception as _e:
                                        logger.debug(f"[TriggerService] Ghost logging error: {_e}")
                                        
                                    return
                        else:
                            logger.info(f"[Bypass] TriggerService Regime Filter check bypassed for {symbol}.")
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
                try:
                    from websocket_events import event_manager
                    if event_manager:
                        event_manager.broadcast_signal_rejected(symbol, trend_result.get("direction", "LONG"), _reject)
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
