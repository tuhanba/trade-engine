"""
scalp_bot_v3.py — AX Scalp Engine Modernize Edilmiş Sürüm v3.6 (ULTIMATE)
=========================================================
Yenilikler:
  - Asenkron Market Scanner (Hız)
  - Advanced Trend Engine (Mean Reversion & Volume Profile)
  - Advanced Risk Engine (Korelasyon Koruması)
  - AI Decision Engine Entegrasyonu (Final Karar Katmanı)
  - Otomatik Trade Açma (Execution Engine Entegrasyonu)
  - Telegram Bildirimleri (Sabitlenmiş Token & Chat ID)
  - Dashboard Canlı Akış (SocketIO Entegrasyonu)
  - Ghost Trading (Açılmayan trade'lerin veri takibi)
  - Filtre Optimizasyonu (Daha fazla veri toplama için esnetilmiş eşikler)
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
from database import init_db, get_paper_balance, get_open_trades, save_scalp_signal, save_ai_log, save_scanned_coin, save_paper_trade
from core.data_layer import SignalData, data_layer
from telegram_delivery import deliver_signal, send_trade_open

# Sabitlenmiş Telegram Bilgileri
TG_TOKEN = "8404489471:AAEU3uk-i_IWj4EcHXlf4Zt8-PkpIPAAc54"
TG_CHAT_ID = "958182551"

# Dashboard SocketIO Entegrasyonu
try:
    from app import socketio
    DASHBOARD_SOCKET = socketio
except ImportError:
    DASHBOARD_SOCKET = None

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

def broadcast_to_dashboard(event, data):
    """Dashboard'a canlı veri gönderir."""
    if DASHBOARD_SOCKET:
        try:
            DASHBOARD_SOCKET.emit(event, data, namespace='/')
        except Exception as e:
            logger.debug(f"SocketIO emit error: {e}")

async def main_loop():
    logger.info("=== AX Scalp Engine v3.6 (ULTIMATE) Başlatılıyor ===")
    
    init_db()
    client = Client(BINANCE_API_KEY, BINANCE_API_SECRET)
    
    scanner = AsyncMarketScanner(db_path=DB_PATH)
    trend = AdvancedTrendEngine(client)
    trigger = TriggerEngine(client)
    risk = AdvancedRiskEngine(client, db_path=DB_PATH)
    ai_engine = AIDecisionEngine(db_path=DB_PATH)
    
    send_direct_message("👑 <b>AX Scalp Engine v3.6 ULTIMATE Sürüm Başlatıldı!</b>\nCanlı Dashboard akışı ve Ghost Trading aktif.")

    while True:
        try:
            # 1. Asenkron Tarama
            candidates = await scanner.scan()
            if not candidates:
                await asyncio.sleep(SCAN_INTERVAL)
                continue
                
            # Dashboard'a tarama sonuçlarını gönder
            broadcast_to_dashboard('scanner_update', {'count': len(candidates), 'timestamp': time.time()})
            
            open_trades = get_open_trades()
            balance = get_paper_balance()
            
            for coin in candidates[:25]: # Daha fazla veri için tarama genişletildi
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
                
                # Ghost Trading: Girilmeyen her şeyi "Paper Trade" olarak kaydet (Veri biriktirme)
                if ai_res["decision"] != "ALLOW":
                    save_paper_trade(sig.to_dict(), tracked_from=ai_res["decision"])
                    broadcast_to_dashboard('ghost_trade', {'symbol': symbol, 'decision': ai_res["decision"]})

                # Sinyal Onay Mantığı
                if ai_res["decision"] == "ALLOW":
                    logger.info(f"🚀 SİNYAL ONAYLANDI: {symbol} {trend_res['direction']}")
                    
                    # Dashboard Canlı Bildirim
                    broadcast_to_dashboard('new_signal', sig.to_dict())
                    
                    # DB ve Telegram
                    save_scalp_signal(sig.to_dict())
                    deliver_signal(sig)
                    
                    # Trade aç
                    if EXECUTION_AVAILABLE and len(open_trades) < 5:
                        try:
                            trade_info = open_trade(client, sig.to_dict())
                            if trade_info:
                                send_trade_open(trade_info)
                                broadcast_to_dashboard('trade_opened', trade_info)
                                logger.info(f"✅ Trade açıldı: {symbol}")
                        except Exception as e:
                            logger.error(f"❌ Trade açma hatası ({symbol}): {e}")
                
                elif ai_res["decision"] == "WATCH":
                    # İzleme listesi bildirimi
                    broadcast_to_dashboard('watchlist_update', sig.to_dict())
                    logger.info(f"👀 İZLEME LİSTESİ: {symbol} {trend_res['direction']}")

            await asyncio.sleep(SCAN_INTERVAL)
            
        except Exception as e:
            logger.error(f"Ana döngü hatası: {e}")
            await asyncio.sleep(10)

if __name__ == "__main__":
    asyncio.run(main_loop())
