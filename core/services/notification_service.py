import logging
from core.event_bus import event_bus
from core.event_types import Event, EventType
import telegram_delivery
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

    async def handle_trade_opened(self, event: Event):
        payload = event.payload
        try:
            # Sync to telegram
            telegram_delivery.send_trade_open(payload)
            # Sync to dashboard
            if event_manager:
                from database import get_open_trades
                event_manager.broadcast_live_update(get_open_trades())
        except Exception as e:
            logger.error(f"[NotificationService] Error in trade_opened: {e}")

    async def handle_trade_closed(self, event: Event):
        payload = event.payload
        try:
            telegram_delivery.send_trade_close(
                symbol=payload.get("symbol"),
                net_pnl=payload.get("net_pnl", 0),
                total_fee=payload.get("total_fee", 0),
                reason=payload.get("reason", ""),
                duration_str=payload.get("duration_str", ""),
                direction=payload.get("direction", ""),
                r_multiple=payload.get("r_multiple", 0),
                balance_after=payload.get("balance_after", 0)
            )
            if event_manager:
                from database import get_open_trades
                event_manager.broadcast_live_update(get_open_trades())
                event_manager.broadcast_trade_closed(
                    payload.get("symbol"),
                    payload.get("direction"),
                    payload.get("net_pnl"),
                    payload.get("reason")
                )
        except Exception as e:
            logger.error(f"[NotificationService] Error in trade_closed: {e}")

    async def handle_tp_sl(self, event: Event):
        payload = event.payload
        try:
            telegram_delivery.send_tp_hit(
                symbol=payload.get("symbol"),
                tp_level=payload.get("tp_level"),
                net_pnl=payload.get("net_pnl"),
                remaining_qty=payload.get("remaining_qty"),
                balance_after=payload.get("balance_after", 0)
            )
            if event_manager:
                from database import get_open_trades
                event_manager.broadcast_live_update(get_open_trades())
        except Exception as e:
            logger.error(f"[NotificationService] Error in tp_sl: {e}")
