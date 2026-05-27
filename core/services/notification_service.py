import logging
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

    async def handle_trade_opened(self, event: Event):
        try:
            # Sync to dashboard
            if event_manager:
                from database import get_open_trades
                event_manager.broadcast_live_update(get_open_trades())
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
        except Exception as e:
            logger.error(f"[NotificationService] Error in trade_closed: {e}")

    async def handle_tp_sl(self, event: Event):
        try:
            if event_manager:
                from database import get_open_trades
                event_manager.broadcast_live_update(get_open_trades())
        except Exception as e:
            logger.error(f"[NotificationService] Error in tp_sl: {e}")
