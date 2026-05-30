import asyncio
import unittest
from core.event_bus import event_bus, EventType
from core.event_types import Event

class TestEventSystem(unittest.IsolatedAsyncioTestCase):
    async def test_pub_sub(self):
        received_events = []
        
        async def handler(event):
            received_events.append(event.payload)
            
        # Start event bus
        await event_bus.start()
            
        # Subscribe
        event_bus.subscribe(EventType.SCANNED, handler)
        
        # Publish
        test_payload = {"symbol": "BTCUSDT", "price": 50000}
        await event_bus.publish(Event(type=EventType.SCANNED, payload=test_payload))
        
        # Give event loop time to process
        await asyncio.sleep(0.1)
        
        self.assertEqual(len(received_events), 1)
        self.assertEqual(received_events[0], test_payload)
        
        # Stop
        await event_bus.stop()
        
    async def test_multiple_subscribers(self):
        counter = {"count": 0}
        
        async def handler1(event):
            counter["count"] += 1
            
        async def handler2(event):
            counter["count"] += 1
            
        await event_bus.start()
            
        event_bus.subscribe(EventType.TREND_CHECKED, handler1)
        event_bus.subscribe(EventType.TREND_CHECKED, handler2)
        
        await event_bus.publish(Event(type=EventType.TREND_CHECKED, payload={"symbol": "ETHUSDT"}))
        await asyncio.sleep(0.1)
        
        self.assertEqual(counter["count"], 2)
        await event_bus.stop()

if __name__ == "__main__":
    unittest.main()
