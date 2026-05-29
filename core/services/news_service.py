import logging
import asyncio
import aiohttp
import xml.etree.ElementTree as ET
from typing import List
from core.event_bus import event_bus
from core.event_types import Event, EventType

logger = logging.getLogger("ax.news")

class NewsService:
    """
    Kripto haber RSS akışlarını tarayarak piyasayı çökertebilecek "Panik" haberleri
    arar (Örn: SEC davaları, borsa hacklenmesi). Tehlike sezildiğinde KILL_SWITCH tetikler.
    """
    
    _instance = None
    
    def __new__(cls):
        if cls._instance is None:
            cls._instance = super(NewsService, cls).__new__(cls)
            cls._instance._is_running = False
            cls._instance.panic_keywords = ["sec sues", "hacked", "bankrupt", "crash", "doj charges", "fbi", "launder"]
            cls._instance.rss_url = "https://cointelegraph.com/rss"
        return cls._instance

    async def check_news(self):
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(self.rss_url) as response:
                    if response.status == 200:
                        content = await response.text()
                        root = ET.fromstring(content)
                        
                        for item in root.findall('.//item'):
                            title = item.find('title').text.lower()
                            
                            for kw in self.panic_keywords:
                                if kw in title:
                                    logger.critical(f"[NEWS AI] PANIC KEYWORD DETECTED: '{kw}' in '{title}'")
                                    await event_bus.publish(Event(
                                        type=EventType.KILL_SWITCH_ACTIVATED,
                                        payload={"reason": f"News AI Panic: {kw}", "title": title}
                                    ))
                                    # Bir kere tetiklendi mi yeterli, geri kalan haberlere bakmaya gerek yok
                                    return
        except Exception as e:
            logger.error(f"[NEWS AI] Haber tarama hatasi: {e}")

    async def start_background_task(self):
        if self._is_running:
            return
        self._is_running = True
        logger.info("[NEWS AI] Flash News Scanner başlatıldı.")
        
        while self._is_running:
            await self.check_news()
            await asyncio.sleep(300)  # Her 5 dakikada bir kontrol et

    def stop(self):
        self._is_running = False

news_service = NewsService()
