import asyncio
from core.async_market_scanner import AsyncMarketScanner

async def run_scan_test():
    scanner = AsyncMarketScanner()
    res = await scanner.scan()
    print("Found", len(res), "candidates.")
    if len(res) > 0:
        print(res[0])

asyncio.run(run_scan_test())
