import logging
import asyncio
import aiohttp
import xml.etree.ElementTree as ET
from typing import List
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
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
            # Sadece gercek piyasa tehdidi icin cok kelimeli, spesifik ifadeler
            # "crash" tek basina cok fazla yanlis alarm uretir (ornek: "crash bug", "fix crash" vb.)
            cls._instance.panic_keywords = [
                "sec sues",
                "sec charges",
                "exchange hacked",
                "exchange hack",
                "binance hacked",
                "coinbase hacked",
                "market crash",
                "crypto crash",
                "bitcoin crash",
                "doj charges",
                "fbi seizes",
                "bankrupt",
                "insolvency",
                "emergency shutdown",
                "trading halted",
            ]
            cls._instance.rss_url = "https://cointelegraph.com/rss"
            cls._instance._kill_switch_fired = False  # cooldown: bir kez tetiklendi mi?
        return cls._instance

    async def check_news(self):
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(self.rss_url) as response:
                    if response.status == 200:
                        content = await response.text()
                        root = ET.fromstring(content)
                        
                        now = datetime.now(timezone.utc)
                        for item in root.findall('.//item'):
                            title_elem = item.find('title')
                            if title_elem is None or not title_elem.text:
                                continue
                            
                            # Haber tarihini kontrol et (eski haberleri atla)
                            pub_date_elem = item.find('pubDate')
                            if pub_date_elem is not None and pub_date_elem.text:
                                try:
                                    pub_date = parsedate_to_datetime(pub_date_elem.text)
                                    # 1 saatten (3600 sn) eski haberleri değerlendirmeye alma
                                    if (now - pub_date).total_seconds() > 3600:
                                        continue
                                except Exception as date_err:
                                    logger.debug(f"[NEWS AI] Haber tarihi ayristirma hatasi: {date_err}")
                            
                            title = title_elem.text.lower()
                            
                            for kw in self.panic_keywords:
                                if kw in title:
                                    if self._kill_switch_fired:
                                        logger.warning(f"[NEWS AI] Kill switch already active, skipping repeat: '{kw}' in '{title}'")
                                        return
                                    logger.critical(f"[NEWS AI] PANIC KEYWORD DETECTED: '{kw}' in '{title}'")
                                    self._kill_switch_fired = True
                                    await event_bus.publish(Event(
                                        type=EventType.KILL_SWITCH_ACTIVATED,
                                        payload={"reason": f"News AI Panic: {kw}", "title": title}
                                    ))
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
