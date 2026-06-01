"""
core/services/sentiment_scraper.py — Twitter/News Sentiment Scraper Agent
=======================================================================

Periodically checks crypto news RSS feeds, evaluates sentiment dynamically,
and updates system state to adjust AI SentimentAgent votes in real-time.
"""

import logging
import asyncio
import aiohttp
import xml.etree.ElementTree as ET
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
import config
from database import update_system_state, get_system_state

logger = logging.getLogger("ax.sentiment_scraper")

class SentimentScraper:
    _instance = None

    def __new__(cls):
        if cls._instance is None:
            cls._instance = super(SentimentScraper, cls).__new__(cls)
            cls._instance._is_running = False
            cls._instance.sources = [
                "https://cointelegraph.com/rss",
                "https://cryptoglobemedia.s3.amazonaws.com/feed.xml"  # standard fallback RSS
            ]
            # Harmonious keyword list for macro sentiment
            cls._instance.bullish_keywords = [
                "bullish", "rally", "surge", "breakout", "institutional", "gains", 
                "ath", "accumulation", "adoption", "growth", "upward", "rise", 
                "upgrade", "halving", "approves", "approval", "highs", "recovery", "support"
            ]
            cls._instance.bearish_keywords = [
                "bearish", "selloff", "crash", "hack", "lawsuit", "sec charge", 
                "dump", "liquidation", "outflow", "drop", "plunge", "panic", 
                "regulatory", "bankruptcy", "insolvency", "fears", "cracks", "hackers", "stolen"
            ]
        return cls._instance

    async def scrape_sentiment(self) -> str:
        """Scrape RSS feeds and calculate macro sentiment."""
        logger.info("[Sentiment Scraper] Starting RSS headlines scan...")
        bull_count = 0
        bear_count = 0
        now = datetime.now(timezone.utc)

        async with aiohttp.ClientSession() as session:
            for url in self.sources:
                try:
                    async with session.get(url, timeout=10) as response:
                        if response.status != 200:
                            continue
                        content = await response.text()
                        root = ET.fromstring(content)

                        for item in root.findall('.//item'):
                            title_elem = item.find('title')
                            if title_elem is None or not title_elem.text:
                                continue

                            # Skip older news (older than 24 hours) to keep sentiment fresh
                            pub_date_elem = item.find('pubDate')
                            if pub_date_elem is not None and pub_date_elem.text:
                                try:
                                    pub_date = parsedate_to_datetime(pub_date_elem.text)
                                    if (now - pub_date).total_seconds() > 86400:
                                        continue
                                except Exception:
                                    pass

                            title_text = title_elem.text.lower()
                            
                            # Count keyword occurrences
                            for kw in self.bullish_keywords:
                                if kw in title_text:
                                    bull_count += 1
                            for kw in self.bearish_keywords:
                                if kw in title_text:
                                    bear_count += 1
                except Exception as e:
                    logger.debug(f"[Sentiment Scraper] Failed to fetch {url}: {e}")

        # Sentiment Classification
        sentiment = "neutral"
        if bull_count > 0 and bear_count == 0:
            sentiment = "bullish"
        elif bear_count > 0 and bull_count == 0:
            sentiment = "bearish"
        elif bull_count >= bear_count * 1.5:
            sentiment = "bullish"
        elif bear_count >= bull_count * 1.5:
            sentiment = "bearish"

        logger.info(f"[Sentiment Scraper] Completed scan. Bullish hits: {bull_count}, Bearish hits: {bear_count} → Macro Sentiment: {sentiment.upper()}")
        
        # Save to DB
        try:
            update_system_state("sentiment_scraper_macro", sentiment)
            # Emit WebSocket log event for the console stream
            try:
                from websocket_events import event_manager
                if event_manager:
                    event_manager._publish_event("sentiment_scraper_progress", {
                        "bullish_hits": bull_count,
                        "bearish_hits": bear_count,
                        "sentiment": sentiment,
                        "timestamp": datetime.now(timezone.utc).isoformat()
                    })
            except Exception:
                pass
        except Exception as db_err:
            logger.error(f"[Sentiment Scraper] DB save error: {db_err}")

        return sentiment

    async def start_background_task(self):
        """Run periodic sentiment checks every 30 minutes."""
        if self._is_running:
            return
        self._is_running = True
        logger.info("[Sentiment Scraper] Background scraper task started.")
        
        while self._is_running:
            try:
                await self.scrape_sentiment()
            except Exception as e:
                logger.error(f"[Sentiment Scraper] Scraper error: {e}")
            await asyncio.sleep(1800)  # Check every 30 minutes

    def stop(self):
        self._is_running = False

sentiment_scraper = SentimentScraper()
