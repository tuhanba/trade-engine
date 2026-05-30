import asyncio
from core.async_market_scanner import AsyncMarketScanner

async def test():
    scanner = AsyncMarketScanner()
    res = await scanner.scan()
    print("Found", len(res), "candidates.")
    if len(res) > 0:
        print(res[0])

asyncio.run(test())
