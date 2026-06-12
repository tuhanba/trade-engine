"""
tests/test_event_bus_thread_safety.py
=====================================
P0 REGRESSION TESTİ — publish_sync worker thread güvenliği.

Bug: EventBus.publish_sync() içinde asyncio.get_running_loop() çağrılıyordu.
execution_engine._finalize() asyncio.to_thread worker thread'inde çalıştığı
için running loop yoktu → RuntimeError yutuluyor → TRADE_CLOSED ve
TP_OR_SL_TRIGGERED eventleri sessizce kayboluyordu (kapanış Telegram
bildirimleri hiç gitmiyordu).

Fix: EventBus.start() ana loop referansını yakalar (self._loop);
publish_sync bu referans üzerinden call_soon_threadsafe yapar.
"""
import asyncio

import pytest

from core.event_bus import EventBus
from core.event_types import Event, EventType


@pytest.mark.asyncio
async def test_publish_sync_from_worker_thread_delivers_event():
    """asyncio.to_thread içinden publish_sync → subscriber'a ulaşmalı."""
    bus = EventBus()
    await bus.start()

    received = []

    async def on_closed(ev):
        received.append(ev.payload)

    bus.subscribe(EventType.TRADE_CLOSED, on_closed)

    def sync_worker():
        # Worker thread: running event loop YOK.
        bus.publish_sync(
            Event(type=EventType.TRADE_CLOSED,
                  payload={"trade_id": 42, "symbol": "BTCUSDT"})
        )

    await asyncio.to_thread(sync_worker)
    await asyncio.sleep(0.3)
    await bus.stop()

    assert received, "TRADE_CLOSED event worker thread'den kayboldu (publish_sync bug)"
    assert received[0]["trade_id"] == 42


@pytest.mark.asyncio
async def test_publish_sync_tp_sl_from_worker_thread():
    """TP_OR_SL_TRIGGERED de aynı yoldan kaybolmamalı."""
    bus = EventBus()
    await bus.start()

    received = []

    async def on_tp(ev):
        received.append(ev.payload)

    bus.subscribe(EventType.TP_OR_SL_TRIGGERED, on_tp)

    def sync_worker():
        bus.publish_sync(
            Event(type=EventType.TP_OR_SL_TRIGGERED,
                  payload={"symbol": "ETHUSDT", "level": 1})
        )

    await asyncio.to_thread(sync_worker)
    await asyncio.sleep(0.3)
    await bus.stop()

    assert received and received[0]["symbol"] == "ETHUSDT"
