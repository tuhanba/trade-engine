# src/core/event_system.py

import queue
import threading
from typing import Callable, Dict, List
from utils.logger import setup_logger


class EventSystem:
    """
    Basit bir event bus.
    Üreticiler (producer) event yayar, tüketiciler (consumer) abone olur.
    Thread-safe: Queue üzerinden çalışır.
    """

    def __init__(self):
        self.logger = setup_logger(__name__)
        self._subscribers: Dict[str, List[Callable]] = {}
        self._queue: queue.Queue = queue.Queue()
        self._lock = threading.Lock()
        self._running = False
        self._dispatcher_thread = None

    def subscribe(self, event_type: str, callback: Callable):
        """Bir event türüne abone ol."""
        with self._lock:
            if event_type not in self._subscribers:
                self._subscribers[event_type] = []
            self._subscribers[event_type].append(callback)
            self.logger.debug(f"Subscribed to '{event_type}': {callback.__qualname__}")

    def publish(self, event_type: str, data: dict):
        """Event kuyruğuna yeni bir olay ekle."""
        self._queue.put({"type": event_type, "data": data})

    def start(self):
        """Dispatcher thread'ini başlat."""
        self._running = True
        self._dispatcher_thread = threading.Thread(
            target=self._dispatch_loop,
            name="EventDispatcher",
            daemon=True
        )
        self._dispatcher_thread.start()
        self.logger.info("EventSystem started.")

    def stop(self):
        """Dispatcher'ı durdur."""
        self._running = False
        self._queue.put(None)  # Poison pill — döngüyü sonlandırır
        if self._dispatcher_thread:
            self._dispatcher_thread.join(timeout=5)
        self.logger.info("EventSystem stopped.")

    def _dispatch_loop(self):
        while self._running:
            event = self._queue.get()
            if event is None:
                break
            event_type = event["type"]
            data = event["data"]
            with self._lock:
                callbacks = self._subscribers.get(event_type, []).copy()
            for callback in callbacks:
                try:
                    callback(data)
                except Exception as e:
                    self.logger.error(f"Error in callback '{callback.__qualname__}': {e}")
