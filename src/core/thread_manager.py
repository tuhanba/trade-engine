# src/core/thread_manager.py

import threading
from typing import Callable, Dict
from utils.logger import setup_logger


class ThreadManager:
    """
    Thread başlatma, durdurma ve izleme işlemlerini merkezi yönetir.
    """

    def __init__(self):
        self.logger = setup_logger(__name__)
        self._threads: Dict[str, threading.Thread] = {}

    def start_thread(self, name: str, target: Callable, daemon: bool = True):
        """Yeni bir thread başlatır."""
        if name in self._threads and self._threads[name].is_alive():
            self.logger.warning(f"Thread '{name}' already running.")
            return
        t = threading.Thread(target=target, name=name, daemon=daemon)
        t.start()
        self._threads[name] = t
        self.logger.info(f"Thread '{name}' started.")

    def stop_all(self, timeout: float = 5.0):
        """Tüm thread'lerin bitmesini bekler."""
        for name, thread in self._threads.items():
            thread.join(timeout=timeout)
            if thread.is_alive():
                self.logger.warning(f"Thread '{name}' did not stop in time.")
            else:
                self.logger.info(f"Thread '{name}' stopped.")

    def status(self) -> Dict[str, bool]:
        """Her thread'in canlı olup olmadığını döner."""
        return {name: t.is_alive() for name, t in self._threads.items()}
