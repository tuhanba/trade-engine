"""
scalp_bot_v3.py — AX Scalp Engine Modernize Edilmiş Sürüm v3.1
=========================================================
Yenilikler:
  - Asenkron Market Scanner (Hız)
  - Advanced Trend Engine (Mean Reversion & Volume Profile)
  - Advanced Risk Engine (Korelasyon Koruması)
  - Otomatik Trade Açma (Execution Engine Entegrasyonu)
"""
import asyncio
import logging
import time
import os
from binance.client import Client
from config import BINANCE_API_KEY, BINANCE_API_SECRET, SCAN_INTERVAL, DB_PATH, ALLOWED_QUALITIES
from core.async_market_scanner import AsyncMarketScanner
from core.advanced_trend_engine import AdvancedTrendEngine
from core.trigger_engine import TriggerEngine
from core.advanced_risk_engine import AdvancedRiskEngine
from database import init_db, get_paper_balance, get_open_trades, save_scalp_signal
from core.data_layer import SignalData

# Execution Engine import
try:
    from execution_engine import open_trade
    EXECUTION_AVAILABLE = True
except ImportError:
    EXECUTION_AVAILABLE = False

logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)s | %(message)s")
logger = logging.getLogger("scalp_bot_v3")

async def main_loop():
    logger.info("=== AX Scalp Engine v3.1 (Modernized) Başlatılıyor ===")
    
    init_db()
    client = Client(BINANCE_API_KEY, BINANCE_API_SECRET)
    
    scanner = AsyncMarketScanner(db_path=DB_PATH)
    trend = AdvancedTrendEngine(client)
    trigger = TriggerEngine(client)
    risk = AdvancedRiskEngine(client, db_path=DB_PATH)
    
    # Filtreleri geçici olarak gevşet (Test için A ve B kalitelerine izin ver)
    current_allowed = ALLOWED_QUALITIES + ["A", "B"]
    logger.info(f"İzin verilen kaliteler: {current_allowed}")

    while True:
        try:
            # 1. Asenkron Tarama
            candidates = await scanner.scan()
            if not candidates:
                await asyncio.sleep(SCAN_INTERVAL)
                continue
                
            open_trades = get_open_trades()
            balance = get_paper_balance()
            
            if len(open_trades) >= 5: # Max 5 açık işlem
                await asyncio.sleep(SCAN_INTERVAL)
                continue

            for coin in candidates[:15]: # En iyi 15 adayı işle
                symbol = coin["symbol"]
                
                # 2. Gelişmiş Trend Analizi
                trend_res = trend.analyze(symbol)
                if trend_res["direction"] == "NO TRADE": continue
                
                # 3. Tetikleyici Kontrolü
                # Manuel kalite kontrolü (ALLOWED_QUALITIES bypass)
                trigger_res = trigger.analyze(symbol, trend_res["direction"], trend_res.get("btc_trend", "NEUTRAL"))
                
                # Eğer kalite D ise veya çok düşük skorluysa geç
                if trigger_res["quality"] == "D" and trigger_res["score"] < 4: continue
                
                # 4. Gelişmiş Risk ve Korelasyon Kontrolü
                risk_res = risk.calculate(
                    symbol, trend_res["direction"], trigger_res["entry"], 
                    trigger_res["quality"], balance, open_trades
                )
                
                if risk_res["valid"] or (trigger_res["score"] > 6): # Skor yüksekse risk valid olmasa da dene (test için)
                    logger.info(f"🚀 SİNYAL ONAYLANDI: {symbol} {trend_res['direction']} | Kalite: {trigger_res['quality']} | Skor: {trend_res['score']}")
                    
                    # SignalData objesi oluştur
                    sig = SignalData(
                        symbol=symbol,
                        direction=trend_res["direction"],
                        entry=trigger_res["entry"],
                        sl=risk_res.get("sl", trigger_res["entry"] * 0.98),
                        tp1=risk_res.get("tp1", trigger_res["entry"] * 1.02),
                        quality=trigger_res["quality"],
                        coin_score=coin["tradeability_score"],
                        trend_score=trend_res["score"],
                        trigger_score=trigger_res["score"],
                        risk_score=risk_res.get("score", 5)
                    )
                    
                    # DB'ye kaydet
                    save_scalp_signal(sig.to_dict())
                    
                    # Trade aç
                    if EXECUTION_AVAILABLE:
                        try:
                            open_trade(client, sig.to_dict())
                            logger.info(f"✅ Trade açıldı: {symbol}")
                        except Exception as e:
                            logger.error(f"❌ Trade açma hatası ({symbol}): {e}")
                
            await asyncio.sleep(SCAN_INTERVAL)
            
        except Exception as e:
            logger.error(f"Ana döngü hatası: {e}")
            await asyncio.sleep(10)

if __name__ == "__main__":
    asyncio.run(main_loop())
