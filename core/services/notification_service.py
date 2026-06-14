import logging
import asyncio
from core.event_bus import event_bus
from core.event_types import Event, EventType

try:
    from websocket_events import event_manager
except Exception:
    event_manager = None

logger = logging.getLogger("ax.services.notification")

class NotificationService:
    def __init__(self):
        event_bus.subscribe(EventType.TRADE_OPENED, self.handle_trade_opened)
        event_bus.subscribe(EventType.TRADE_CLOSED, self.handle_trade_closed)
        event_bus.subscribe(EventType.TP_OR_SL_TRIGGERED, self.handle_tp_sl)
        event_bus.subscribe(EventType.AI_VALIDATED, self.handle_ai_validated)

    async def handle_ai_validated(self, event: Event):
        payload = event.payload
        symbol = payload.get("symbol")
        signal_dict = payload.get("signal_data")
        ai_decision = payload.get("ai_decision", {})
        decision = ai_decision.get("decision", "WATCH")

        try:
            from core.data_layer import SignalData
            sig = SignalData.from_dict(signal_dict)

            # 1. Real-time Dashboard Update
            if event_manager:
                event_manager.broadcast_signal_generated(
                    symbol, sig.direction, sig.setup_quality, sig.final_score
                )

            # 2. Telegram Alert for Non-Auto-Executed Signals
            if decision in ("ALLOW", "WATCH"):
                import config
                # Determine if this signal will be auto-executed
                is_scalp = not getattr(config, "HUMAN_MODE", False)
                trade_thr = (
                    config.HUMAN_TRADE_THRESHOLD if not is_scalp
                    else getattr(config, "TRADE_THRESHOLD", 55.0)
                )
                
                # Check execution criteria
                qualities = getattr(config, "EXECUTABLE_QUALITIES", ("S", "A+", "A", "B", "C", "M"))
                will_execute = (
                    sig.final_score >= trade_thr
                    and sig.setup_quality in qualities
                )

                # Send Telegram message if NOT auto-executed and passes Telegram threshold
                tg_threshold = getattr(config, "TELEGRAM_THRESHOLD", 35.0)
                if not will_execute and sig.final_score >= tg_threshold:
                    from telegram_delivery import deliver_signal
                    await asyncio.to_thread(deliver_signal, sig)
                    logger.info("[NotificationService] Telegram signal alert delivered for %s (not auto-executed)", symbol)
        except Exception as e:
            logger.error(f"[NotificationService] Error in handle_ai_validated: {e}")

    async def handle_trade_opened(self, event: Event):
        try:
            # Sync to dashboard
            if event_manager:
                from database import get_open_trades
                event_manager.broadcast_live_update(get_open_trades())
            
            # Send Telegram notification
            try:
                from telegram_delivery import send_trade_open
                send_trade_open(event.payload)
            except Exception as tg_err:
                logger.error(f"[NotificationService] Telegram Error (open): {tg_err}")
        except Exception as e:
            logger.error(f"[NotificationService] Error in trade_opened: {e}")

    async def handle_trade_closed(self, event: Event):
        payload = event.payload
        try:
            if event_manager:
                from database import get_open_trades
                event_manager.broadcast_live_update(get_open_trades())
                event_manager.broadcast_trade_closed(
                    payload.get("symbol"),
                    payload.get("direction"),
                    payload.get("net_pnl"),
                    payload.get("reason")
                )
            
            # Send Telegram notification
            try:
                from telegram_delivery import send_trade_close
                send_trade_close(
                    symbol=payload.get("symbol", "?"),
                    net_pnl=float(payload.get("net_pnl", 0)),
                    total_fee=float(payload.get("total_fee", 0)),  # Or pass it if payload has it
                    reason=payload.get("reason", "unknown"),
                    duration_str=payload.get("duration", "?"),
                    direction=payload.get("direction", ""),
                    r_multiple=float(payload.get("r_multiple", 0)),
                    balance_after=float(payload.get("balance_after", 0))
                )
            except Exception as tg_err:
                logger.error(f"[NotificationService] Telegram Error (close): {tg_err}")
        except Exception as e:
            logger.error(f"[NotificationService] Error in trade_closed: {e}")

    async def handle_tp_sl(self, event: Event):
        payload = event.payload
        try:
            if event_manager:
                from database import get_open_trades
                event_manager.broadcast_live_update(get_open_trades())
            
            # Send Telegram notification
            try:
                from telegram_delivery import send_tp_hit
                send_tp_hit(
                    symbol=payload.get("symbol", "?"),
                    tp_level=int(payload.get("level", 1)),
                    net_pnl=float(payload.get("net_pnl", 0)),
                    remaining_qty=float(payload.get("remaining_qty", 0)),
                    balance_after=float(payload.get("balance_after", 0))
                )
            except Exception as tg_err:
                logger.error(f"[NotificationService] Telegram Error (tp/sl): {tg_err}")
        except Exception as e:
            logger.error(f"[NotificationService] Error in tp_sl: {e}")
