"""
scalp_bot_v3.py — AX Scalp Engine Modernize Edilmiş Sürüm
=========================================================
Yenilikler:
  - Asenkron Market Scanner (Hız)
  - Advanced Trend Engine (Mean Reversion & Volume Profile)
  - Advanced Risk Engine (Korelasyon Koruması)
  - PostgreSQL Hazırlığı
"""
import asyncio
import logging
import time
from binance.client import Client
from config import BINANCE_API_KEY, BINANCE_API_SECRET, SCAN_INTERVAL, DB_PATH
from core.async_market_scanner import AsyncMarketScanner
from core.advanced_trend_engine import AdvancedTrendEngine
from core.trigger_engine import TriggerEngine
from core.advanced_risk_engine import AdvancedRiskEngine
from database import init_db, get_paper_balance, get_open_trades

logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)s | %(message)s")
logger = logging.getLogger("scalp_bot_v3")

async def main_loop():
    logger.info("=== AX Scalp Engine v3.0 (Modernized) Başlatılıyor ===")
    
    init_db()
    client = Client(BINANCE_API_KEY, BINANCE_API_SECRET)
    
    scanner = AsyncMarketScanner(db_path=DB_PATH)
    trend = AdvancedTrendEngine(client)
    trigger = TriggerEngine(client)
    risk = AdvancedRiskEngine(client, db_path=DB_PATH)
    
    while True:
        try:
            # 1. Asenkron Tarama
            candidates = await scanner.scan()
            if not candidates:
                await asyncio.sleep(SCAN_INTERVAL)
                continue
                
            open_trades = get_open_trades()
            balance = get_paper_balance()
            
            for coin in candidates[:10]: # En iyi 10 adayı işle
                symbol = coin["symbol"]
                
                # 2. Gelişmiş Trend Analizi
                trend_res = trend.analyze(symbol)
                if trend_res["direction"] == "NO TRADE": continue
                
                # 3. Tetikleyici Kontrolü
                trigger_res = trigger.analyze(symbol, trend_res["direction"], trend_res.get("btc_trend", "NEUTRAL"))
                if trigger_res["quality"] == "D": continue
                
                # 4. Gelişmiş Risk ve Korelasyon Kontrolü
                risk_res = risk.calculate(
                    symbol, trend_res["direction"], trigger_res["entry"], 
                    trigger_res["quality"], balance, open_trades
                )
                
                if risk_res["valid"]:
                    logger.info(f"🚀 SİNYAL ONAYLANDI: {symbol} {trend_res['direction']} | Kalite: {trigger_res['quality']} | Skor: {trend_res['score']}")
                    # Burada trade açma mantığı (execution_engine) çağrılacak
                
            await asyncio.sleep(SCAN_INTERVAL)
            
        except Exception as e:
            logger.error(f"Ana döngü hatası: {e}")
            await asyncio.sleep(10)

if __name__ == "__main__":
    asyncio.run(main_loop())
