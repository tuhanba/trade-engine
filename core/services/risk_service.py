"""
core/services/risk_service.py — Risk Aşaması v6.0

Değişiklikler:
  - entry key normalizasyonu: entry veya entry_price, hangisi dolu ise
  - RISK_REJECTED → signal_events'e yaz + candidate status güncelle
  - RISK_APPROVED → signal_events'e yaz (pipeline gözlemlenebilirliği)
  - pipeline_risk_reject → bot_status'a yaz (dashboard funnel)
"""

import logging
import asyncio
from core.event_bus import event_bus
from core.event_types import Event, EventType
from core.risk_engine import RiskEngine
from database import get_active_balance

logger = logging.getLogger("ax.services.risk")


class RiskService:
    def __init__(self, client):
        self.client = client
        self.risk_engine = RiskEngine(client)
        event_bus.subscribe(EventType.TRIGGER_CHECKED, self.handle_trigger_checked)

    async def handle_trigger_checked(self, event: Event):
        payload     = event.payload
        symbol      = payload.get("symbol")
        trend_result   = payload.get("trend_result", {})
        trigger_result = payload.get("trigger_result", {})
        signal_id   = payload.get("signal_id")
        candidate_id = payload.get("candidate_id")
        tradeability_score = payload.get("tradeability_score")

        try:
            # entry key normalizasyonu — TriggerEngine bazen entry_price döner
            _entry = float(
                trigger_result.get("entry")
                or trigger_result.get("entry_price")
                or 0
            )
            if _entry == 0:
                logger.warning("[RiskService] %s entry=0 — trigger_result keys: %s",
                               symbol, list(trigger_result.keys()))

            balance = await asyncio.to_thread(get_active_balance, self.client)

            risk_result = await asyncio.to_thread(
                self.risk_engine.calculate,
                symbol,
                trend_result.get("direction", "LONG"),
                _entry,
                trigger_result.get("quality", "C"),
                balance,
                tradeability_score or 0.0,
            )

            if not risk_result.get("valid"):
                reject_reason = risk_result.get(
                    "risk_reject_reason", "risk_guard_failed"
                )
                logger.debug("[RiskService] %s reddedildi: %s", symbol, reject_reason)

                # signal_events'e yaz
                try:
                    from database import save_signal_event
                    await asyncio.to_thread(
                        save_signal_event, signal_id, "RISK_REJECTED",
                        symbol=symbol, reject_reason=reject_reason,
                    )
                except Exception as _e:
                    logger.debug("[RiskService] signal_event yazılamadı: %s", _e)

                try:
                    from websocket_events import event_manager
                    if event_manager:
                        event_manager.broadcast_signal_rejected(symbol, trend_result.get("direction", "LONG"), reject_reason)
                except Exception:
                    pass

                # Candidate status güncelle
                if candidate_id:
                    try:
                        from database import update_candidate_status
                        await asyncio.to_thread(
                            update_candidate_status,
                            candidate_id,
                            decision="RISK_REJECTED",
                            reject_reason=reject_reason,
                        )
                    except Exception as _e:
                        logger.debug("[RiskService] candidate_status: %s", _e)

                # Dashboard funnel sayacı
                try:
                    from database import update_bot_status
                    await asyncio.to_thread(
                        update_bot_status, "last_risk_reject",
                        f"{symbol}:{reject_reason}",
                    )
                except Exception:
                    pass

                # Log as ghost signal if cooldown rejected
                if reject_reason in ("coin_cooldown", "friday_boss_cooldown") or "cooldown" in reject_reason:
                    try:
                        from core.ghost_learning import maybe_ghost_log
                        sig_to_log = {
                            "symbol": symbol,
                            "direction": trend_result.get("direction", "LONG"),
                            "entry": _entry,
                            "sl": trigger_result.get("stop_loss") or trigger_result.get("sl") or 0.0,
                            "tp1": trigger_result.get("tp1") or 0.0,
                            "tp2": trigger_result.get("tp2") or 0.0,
                            "tp3": trigger_result.get("tp3") or 0.0,
                            "confidence": float(tradeability_score or 50) / 100.0,
                            "trigger_type": trigger_result.get("quality", "C"),
                            "final_score": tradeability_score or 50.0,
                            "market_regime": trend_result.get("market_trend", "NEUTRAL"),
                            "rsi": trigger_result.get("rsi5") or 50.0,
                            "cvd_slope": trigger_result.get("cvd_slope") or 0.0,
                        }
                        await asyncio.to_thread(maybe_ghost_log, sig_to_log, reason=f"SKIPPED_BY_{reject_reason.upper()}")
                    except Exception as _e:
                        logger.debug(f"[RiskService] Ghost logging error: {_e}")

                return  # pipeline durdu

            # PASS: bir sonraki aşamaya geç
            try:
                from database import save_signal_event
                await asyncio.to_thread(
                    save_signal_event, signal_id, "RISK_APPROVED",
                    symbol=symbol, reject_reason="",
                )
            except Exception:
                pass

            next_payload = {
                "symbol":            symbol,
                "signal_id":         signal_id,
                "candidate_id":      candidate_id,
                "tradeability_score": tradeability_score,
                "trend_result":      trend_result,
                # normalize edilmiş entry'i taşı
                "trigger_result":    {**trigger_result, "entry": _entry},
                "risk_result":       risk_result,
            }

            await event_bus.publish(
                Event(type=EventType.RISK_APPROVED, payload=next_payload)
            )
            logger.debug("[RiskService] %s risk onayı geçti.", symbol)

        except Exception as e:
            logger.error("[RiskService] %s işlenirken hata: %s", symbol, e)
