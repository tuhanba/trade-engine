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

    def subscribe(self, event_type: EventType, callback: Callable[[Event], Awaitable[None]]):
        if callback not in self._subscribers[event_type]:
            self._subscribers[event_type].append(callback)
            logger.debug(f"Subscribed {callback.__name__} to {event_type.name}")

    def unsubscribe(self, event_type: EventType, callback: Callable[[Event], Awaitable[None]]):
        if callback in self._subscribers[event_type]:
            self._subscribers[event_type].remove(callback)
            logger.debug(f"Unsubscribed {callback.__name__} from {event_type.name}")

    async def publish(self, event: Event):
        if self._queue:
            await self._queue.put(event)

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
                    await asyncio.gather(*tasks, return_exceptions=True)
                
                self._queue.task_done()
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Error processing event {event.type.name}: {e}")

    async def start(self):
        if not self._running:
            self._queue = asyncio.Queue()
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
