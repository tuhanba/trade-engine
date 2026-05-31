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
