import asyncio
import logging
from typing import Callable, Awaitable, Dict, List
from core.event_types import EventType, Event

logger = logging.getLogger("ax.event_bus")

class EventBus:
    def __init__(self):
        self._subscribers: Dict[EventType, List[Callable[[Event], Awaitable[None]]]] = {
            event_type: [] for event_type in EventType
        }
        self._queue = None
        self._running = False
        self._task = None
        self._loop = None  # P0 FIX: ana event loop referansı (publish_sync için)

    def subscribe(self, event_type: EventType, callback: Callable[[Event], Awaitable[None]]):
        if callback not in self._subscribers[event_type]:
            self._subscribers[event_type].append(callback)
            logger.debug(f"Subscribed {callback.__name__} to {event_type.name}")

    def unsubscribe(self, event_type: EventType, callback: Callable[[Event], Awaitable[None]]):
        if callback in self._subscribers[event_type]:
            self._subscribers[event_type].remove(callback)
            logger.debug(f"Unsubscribed {callback.__name__} from {event_type.name}")

    async def publish(self, event: Event):
        # NEDEN (Fix E): queue None iken event'i sessizce düşürmek "sinyal kayboldu
        # ama log temiz" tablosunu üretiyordu. Artık düşen event WARNING'e işlenir.
        if not self._queue:
            logger.warning("[EventBus] queue yok — event DÜŞTÜ: %s", event.type.name)
            return
        await self._queue.put(event)

    def publish_sync(self, event: Event):
        """Thread-safe way to publish events from synchronous code.

        P0 BUG FIX: asyncio.get_running_loop() worker thread'lerde
        (asyncio.to_thread içinden çağrıldığında) RuntimeError fırlatır.
        execution_engine._finalize() bu yoldan çalıştığı için
        TRADE_CLOSED ve TP_OR_SL_TRIGGERED eventleri sessizce kayboluyordu
        (kapanış Telegram bildirimleri hiç gitmiyordu).
        Çözüm: start() anında yakalanan ana loop referansı kullanılır.
        """
        if self._queue and self._running:
            try:
                loop = self._loop
                if loop is None or loop.is_closed():
                    # Fallback: ana loop yoksa çağıran thread'in loop'unu dene
                    loop = asyncio.get_running_loop()
                loop.call_soon_threadsafe(self._queue.put_nowait, event)
            except Exception as e:
                logger.error(f"Error in publish_sync: {e}")

    async def _process_events(self):
        while self._running:
            try:
                if not self._queue:
                    await asyncio.sleep(0.1)
                    continue
                    
                event = await self._queue.get()
                if event.type == EventType.SYSTEM_SHUTDOWN:
                    self._running = False
                    self._queue.task_done()
                    break

                handlers = self._subscribers.get(event.type, [])
                if handlers:
                    # Run handlers concurrently
                    tasks = [asyncio.create_task(handler(event)) for handler in handlers]
                    results = await asyncio.gather(*tasks, return_exceptions=True)
                    # NEDEN (Fix E): gather sonuçları daha önce incelenmiyordu →
                    # handler'daki yakalanmamış istisna sessizce yutuluyor, event
                    # pipeline'da kayboluyordu. Artık her hata handler adıyla loglanır.
                    for h, r in zip(handlers, results):
                        if isinstance(r, Exception):
                            logger.error(
                                "[EventBus] %s handler hata (%s): %r",
                                event.type.name, getattr(h, "__name__", h), r,
                                exc_info=r,
                            )

                self._queue.task_done()
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Error processing event {event.type.name}: {e}")

    async def start(self):
        if not self._running:
            self._queue = asyncio.Queue()
            self._loop = asyncio.get_running_loop()  # P0 FIX: ana loop'u yakala
            self._running = True
            self._task = asyncio.create_task(self._process_events())
            logger.info("EventBus started")

    async def stop(self):
        if self._running:
            await self.publish(Event(type=EventType.SYSTEM_SHUTDOWN, payload={}))
            if self._task:
                await self._task
            self._running = False
            self._queue = None
            logger.info("EventBus stopped")

# Global Event Bus instance
event_bus = EventBus()
