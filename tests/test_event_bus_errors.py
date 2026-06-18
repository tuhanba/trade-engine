"""
tests/test_event_bus_errors.py
==============================
Fix E REGRESYON TESTİ — Event-bus istisna gözlemlenebilirliği.

Bug: _process_events içindeki asyncio.gather(..., return_exceptions=True)
sonuçları incelenmiyordu → bir handler'da yakalanmamış istisna olursa event
sessizce tüketiliyor, hata hiçbir yere loglanmıyordu (sinyal pipeline'da
sessizce ölüyordu). Ayrıca publish() queue None ise event'i sessizce
düşürüyordu.

Fix: gather sonuçları handler bazında incelenir ve istisnalar ERROR loglanır;
publish() queue yoksa WARNING loglar.
"""
import asyncio
import logging

import pytest

from core.event_bus import EventBus
from core.event_types import Event, EventType


@pytest.mark.asyncio
async def test_handler_exception_logged(caplog):
    """Bir handler patlarsa EventBus hatayı YUTMAMALI — ERROR loglamalı."""
    bus = EventBus()

    async def boom(ev):
        raise ValueError("patla")

    bus.subscribe(EventType.SCANNED, boom)
    await bus.start()
    with caplog.at_level(logging.ERROR, logger="ax.event_bus"):
        await bus.publish(Event(type=EventType.SCANNED, payload={}))
        await asyncio.sleep(0.1)
    await bus.stop()

    assert any("handler hata" in r.getMessage() for r in caplog.records), (
        "Handler istisnası sessizce yutuldu — loglanmadı"
    )


@pytest.mark.asyncio
async def test_publish_without_queue_logs_warning(caplog):
    """Bus başlatılmadan publish edilirse event sessizce DÜŞMEMELİ — WARNING."""
    bus = EventBus()  # start() çağrılmadı → _queue None

    with caplog.at_level(logging.WARNING, logger="ax.event_bus"):
        await bus.publish(Event(type=EventType.SCANNED, payload={}))

    assert any("event DÜŞTÜ" in r.getMessage() for r in caplog.records), (
        "Queue yokken düşen event loglanmadı"
    )


@pytest.mark.asyncio
async def test_healthy_handler_still_runs_alongside_failing(caplog):
    """Bir handler patlasa bile diğer handler'lar çalışmaya devam etmeli."""
    bus = EventBus()
    received = []

    async def boom(ev):
        raise RuntimeError("patla")

    async def ok(ev):
        received.append(ev.payload)

    bus.subscribe(EventType.SCANNED, boom)
    bus.subscribe(EventType.SCANNED, ok)
    await bus.start()
    with caplog.at_level(logging.ERROR, logger="ax.event_bus"):
        await bus.publish(Event(type=EventType.SCANNED, payload={"x": 1}))
        await asyncio.sleep(0.1)
    await bus.stop()

    assert received == [{"x": 1}], "Sağlam handler patlayan handler yüzünden çalışmadı"
    assert any("handler hata" in r.getMessage() for r in caplog.records)
