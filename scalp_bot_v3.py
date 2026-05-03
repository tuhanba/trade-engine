"""
scalp_bot_v3.py — AX Scalp Engine Modernize Edilmiş Sürüm v3.5 (ELITE)
=========================================================
Yenilikler:
  - Asenkron Market Scanner (Hız)
  - Advanced Trend Engine (Mean Reversion & Volume Profile)
  - Advanced Risk Engine (Korelasyon Koruması)
  - AI Decision Engine Entegrasyonu (Final Karar Katmanı)
  - Otomatik Trade Açma (Execution Engine Entegrasyonu)
  - Telegram Bildirimleri (Sabitlenmiş Token & Chat ID)
  - Dashboard Entegrasyonu (Data Layer & DB Kaydı)
  - Backtest Filtre Restorasyonu (92 Coin Özel Filtreleri)
  - Volatility Guard (Ani Piyasa Hareketlerine Karşı Koruma)
  - Ghost Trading (Açılmayan trade'lerin veri takibi)
"""
import asyncio
import logging
import time
import os
import uuid
import requests
from binance.client import Client
from config import (
    BINANCE_API_KEY, BINANCE_API_SECRET, SCAN_INTERVAL, DB_PATH, 
    ALLOWED_QUALITIES, ADX_MIN_THRESHOLD, BAD_HOURS_UTC, COIN_UNIVERSE
)
from core.async_market_scanner import AsyncMarketScanner
from core.advanced_trend_engine import AdvancedTrendEngine
from core.trigger_engine import TriggerEngine
from core.advanced_risk_engine import AdvancedRiskEngine
from core.ai_decision_engine import AIDecisionEngine
from database import init_db, get_paper_balance, get_open_trades, save_scalp_signal, save_ai_log, save_scanned_coin
from core.data_layer import SignalData, data_layer
from telegram_delivery import deliver_signal, send_trade_open

# Sabitlenmiş Telegram Bilgileri
TG_TOKEN = "8404489471:AAEU3uk-i_IWj4EcHXlf4Zt8-PkpIPAAc54"
TG_CHAT_ID = "958182551"

# Execution Engine import
try:
    from execution_engine import open_trade
    EXECUTION_AVAILABLE = True
except ImportError:
    EXECUTION_AVAILABLE = False

logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)s | %(message)s")
logger = logging.getLogger("scalp_bot_v3")

def send_direct_message(text):
    url = f"https://api.telegram.org/bot{TG_TOKEN}/sendMessage"
    try:
        requests.post(url, json={"chat_id": TG_CHAT_ID, "text": text, "parse_mode": "HTML"}, timeout=10)
    except Exception as e:
        logger.error(f"Telegram direct message error: {e}")

def check_volatility_guard(client):
    """Piyasa genelindeki volatiliteyi kontrol eder."""
    try:
        btc = client.futures_ticker(symbol="BTCUSDT")
        change = abs(float(btc['priceChangePercent']))
        if change > 5.0: # BTC %5'ten fazla hareket ettiyse riskli
            return False, f"BTC Volatility High: {change}%"
        return True, "Normal"
    except:
        return True, "Normal"

async def main_loop():
    logger.info("=== AX Scalp Engine v3.5 (ELITE) Başlatılıyor ===")
    
    init_db()
    client = Client(BINANCE_API_KEY, BINANCE_API_SECRET)
    
    scanner = AsyncMarketScanner(db_path=DB_PATH)
    trend = AdvancedTrendEngine(client)
    trigger = TriggerEngine(client)
    risk = AdvancedRiskEngine(client, db_path=DB_PATH)
    ai_engine = AIDecisionEngine(db_path=DB_PATH)
    
    # 10/10 Filtre Restorasyonu
    logger.info(f"Backtest Filtreleri Aktif: {len(COIN_UNIVERSE)} Coin, ADX > {ADX_MIN_THRESHOLD}")
    
    send_direct_message("💎 <b>AX Scalp Engine v3.5 ELITE Sürüm Başlatıldı!</b>\n10/10 Backtest filtreleri ve Volatility Guard aktif.")

    while True:
        try:
            # Volatility Guard Kontrolü
            is_safe, reason = check_volatility_guard(client)
            if not is_safe:
                logger.warning(f"⚠️ Volatility Guard Devrede: {reason}")
                await asyncio.sleep(SCAN_INTERVAL)
                continue

            # 1. Asenkron Tarama
            candidates = await scanner.scan()
            if not candidates:
                await asyncio.sleep(SCAN_INTERVAL)
                continue
                
            open_trades = get_open_trades()
            balance = get_paper_balance()
            
            for coin in candidates[:20]: # Daha geniş tarama
                symbol = coin["symbol"]
                
                # 2. Gelişmiş Trend Analizi
                trend_res = trend.analyze(symbol)
                if trend_res["direction"] == "NO TRADE": continue
                
                # 3. Tetikleyici Kontrolü
                trigger_res = trigger.analyze(symbol, trend_res["direction"], trend_res.get("btc_trend", "NEUTRAL"))
                
                # 4. Gelişmiş Risk ve Korelasyon Kontrolü
                risk_res = risk.calculate(
                    symbol, trend_res["direction"], trigger_res["entry"], 
                    trigger_res["quality"], balance, open_trades
                )
                
                # SignalData objesi oluştur
                sig = SignalData(
                    id=str(uuid.uuid4())[:8],
                    symbol=symbol,
                    timestamp=time.time(),
                    direction=trend_res["direction"],
                    entry_zone=trigger_res["entry"],
                    stop_loss=risk_res.get("sl", trigger_res["entry"] * 0.98),
                    tp1=risk_res.get("tp1", trigger_res["entry"] * 1.02),
                    tp2=risk_res.get("tp2", trigger_res["entry"] * 1.04),
                    tp3=risk_res.get("tp3", trigger_res["entry"] * 1.06),
                    setup_quality=trigger_res["quality"],
                    coin_score=coin["tradeability_score"],
                    trend_score=trend_res["score"],
                    trigger_score=trigger_res["score"],
                    risk_score=risk_res.get("score", 5),
                    ml_score=trigger_res.get("ml_score", 50),
                    rr=risk_res.get("rr", 1.5),
                    risk_percent=risk_res.get("risk_pct", 1.0),
                    position_size=risk_res.get("position_size", 0),
                    notional_size=risk_res.get("notional", 0),
                    leverage_suggestion=risk_res.get("leverage", 10),
                    confidence=trigger_res.get("ml_score", 50) / 100.0,
                    reason=f"Trend: {trend_res['direction']}, Score: {trend_res['score']}"
                )
                
                # 5. AI Decision Engine Değerlendirmesi
                ai_res = ai_engine.evaluate(sig)
                
                # Ghost Trading: Açılmayan her şeyi kaydet (Veri biriktirme)
                save_scanned_coin(symbol, coin["tradeability_score"], ai_res["decision"], ai_res.get("reason", "low_score"))

                # Sinyal Onay Mantığı
                if ai_res["decision"] == "ALLOW":
                    logger.info(f"🚀 SİNYAL ONAYLANDI: {symbol} {trend_res['direction']} | Kalite: {trigger_res['quality']}")
                    
                    # Dashboard ve DB Kaydı
                    data_layer.add_signal(sig)
                    save_scalp_signal(sig.to_dict())
                    deliver_signal(sig)
                    
                    # Trade aç
                    if EXECUTION_AVAILABLE and len(open_trades) < 5:
                        try:
                            trade_info = open_trade(client, sig.to_dict())
                            if trade_info:
                                send_trade_open(trade_info)
                                logger.info(f"✅ Trade açıldı: {symbol}")
                        except Exception as e:
                            logger.error(f"❌ Trade açma hatası ({symbol}): {e}")
                
                elif ai_res["decision"] == "WATCH":
                    # Sadece Telegram'a bilgi ver veya Dashboard'da izle
                    data_layer.add_signal(sig)
                    save_scalp_signal(sig.to_dict())
                    logger.info(f"👀 İZLEME LİSTESİ: {symbol} {trend_res['direction']}")

            await asyncio.sleep(SCAN_INTERVAL)
            
        except Exception as e:
            logger.error(f"Ana döngü hatası: {e}")
            await asyncio.sleep(10)

if __name__ == "__main__":
    asyncio.run(main_loop())
