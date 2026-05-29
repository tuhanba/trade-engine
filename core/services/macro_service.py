import logging
import asyncio
import aiohttp
from typing import Dict, Any

logger = logging.getLogger("ax.macro")

class MacroService:
    """
    Dış dünyadaki makroekonomik verileri ve genel piyasa duyarlılığını (Sentiment) takip eder.
    Fear & Greed Index, Bitcoin Volatilitesi gibi verileri sağlar.
    """
    
    _instance = None
    
    def __new__(cls):
        if cls._instance is None:
            cls._instance = super(MacroService, cls).__new__(cls)
            cls._instance._cache = {
                "fear_greed_value": 50,
                "fear_greed_classification": "Neutral",
                "last_update": 0
            }
            cls._instance._is_running = False
        return cls._instance

    async def fetch_fear_and_greed(self):
        """Alternative.me API'den Fear & Greed verisini çeker."""
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get("https://api.alternative.me/fng/") as response:
                    if response.status == 200:
                        data = await response.json()
                        if data and "data" in data and len(data["data"]) > 0:
                            fng = data["data"][0]
                            self._cache["fear_greed_value"] = int(fng.get("value", 50))
                            self._cache["fear_greed_classification"] = fng.get("value_classification", "Neutral")
                            self._cache["last_update"] = asyncio.get_event_loop().time()
                            logger.info(f"[Macro] Fear & Greed: {self._cache['fear_greed_value']} ({self._cache['fear_greed_classification']})")
        except Exception as e:
            logger.error(f"[Macro] Fear & Greed API hatası: {e}")

    async def start_background_task(self):
        """Arka planda makro verileri periyodik olarak günceller (Örn: 1 saatte bir)"""
        if self._is_running:
            return
        self._is_running = True
        logger.info("[Macro] MacroService background task başlatıldı.")
        
        while self._is_running:
            await self.fetch_fear_and_greed()
            await asyncio.sleep(3600)  # 1 saatte bir güncelle

    def stop(self):
        self._is_running = False

    def get_market_sentiment(self) -> Dict[str, Any]:
        """Geçerli piyasa duyarlılığını döndürür."""
        return {
            "fng_value": self._cache.get("fear_greed_value", 50),
            "fng_class": self._cache.get("fear_greed_classification", "Neutral")
        }

macro_service = MacroService()
