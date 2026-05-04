import asyncio
import logging
import time
import uuid
import requests
import pandas as pd
from binance.client import Client
from config import BINANCE_API_KEY, BINANCE_API_SECRET, DB_PATH, SCAN_INTERVAL, TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID
from core.async_market_scanner import AsyncMarketScanner
from core.advanced_trend_engine import AdvancedTrendEngine
from core.trigger_engine import TriggerEngine
from core.advanced_risk_engine import AdvancedRiskEngine
from core.ai_decision_engine import AIDecisionEngine
from core.elite_monitor import EliteMonitor
from database import init_db, get_paper_balance, get_open_trades, save_scalp_signal, save_paper_trade, get_conn
from core.data_layer import SignalData
from telegram_delivery import deliver_signal, send_trade_open

logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)s | %(message)s")
logger = logging.getLogger("scalp_bot_v3")

def send_direct_message(text):
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    try:
        requests.post(url, json={"chat_id": TELEGRAM_CHAT_ID, "text": text, "parse_mode": "HTML"}, timeout=10)
    except Exception: pass

class UltimateEliteEngine:
    def __init__(self, client):
        self.client = client
    def get_sentiment(self):
        try:
            tickers = self.client.get_ticker()
            up = len([t for t in tickers if float(t['priceChangePercent']) > 0])
            total = len(tickers)
            sentiment_score = (up / total) * 100
            return round(sentiment_score, 2)
        except: return 50.0
    def calculate_adaptive_targets(self, symbol, entry, direction):
        try:
            klines = self.client.futures_klines(symbol=symbol, interval="1h", limit=20)
            df = pd.DataFrame(klines, columns=['t','o','h','l','c','v','ct','q','n','tb','tq','i'])
            df['h'], df['l'], df['c'] = df['h'].astype(float), df['l'].astype(float), df['c'].astype(float)
            atr = (df['h'] - df['l']).mean()
            sl_dist = atr * 1.5
            tp_dist = atr * 2.5
            sl = entry - sl_dist if direction == "LONG" else entry + sl_dist
            tp = entry + tp_dist if direction == "LONG" else entry - tp_dist
            return sl, tp
        except:
            return entry * 0.98, entry * 1.04

async def main_loop():
    logger.info("=== AX Scalp Engine v3.9 (ULTIMATE ELITE) Başlatılıyor ===")
    init_db()
    client = Client(BINANCE_API_KEY, BINANCE_API_SECRET)
    
    scanner = AsyncMarketScanner(db_path=DB_PATH)
    trend = AdvancedTrendEngine(client)
    trigger = TriggerEngine(client)
    risk = AdvancedRiskEngine(client, db_path=DB_PATH)
    ai_engine = AIDecisionEngine(db_path=DB_PATH)
    elite = UltimateEliteEngine(client)
    
    send_direct_message("👑 <b>AX ULTIMATE ELITE Sürüm Başlatıldı!</b>\nSentiment, Adaptive SL/TP ve AI Post-Mortem aktif.")
    while True:
        try:
            sentiment = elite.get_sentiment()
            logger.info(f"🌍 Piyasa Duyarlılığı: %{sentiment}")
            
            candidates = await scanner.scan()
            if not candidates:
                await asyncio.sleep(SCAN_INTERVAL)
                continue
            
            for coin in candidates[:20]:
                symbol = coin["symbol"]
                trend_res = trend.analyze(symbol)
                if trend_res["direction"] == "NO TRADE": continue
                
                if sentiment < 20 and trend_res["direction"] == "LONG": continue
                
                trigger_res = trigger.analyze(symbol, trend_res["direction"])
                sl, tp = elite.calculate_adaptive_targets(symbol, trigger_res["entry"], trend_res["direction"])
                
                sig = SignalData(
                    id=str(uuid.uuid4())[:8], symbol=symbol, timestamp=time.time(),
                    direction=trend_res["direction"], entry_zone=trigger_res["entry"],
                    stop_loss=sl, tp1=tp, tp2=tp*1.02, tp3=tp*1.04,
                    setup_quality=trigger_res["quality"], coin_score=coin["tradeability_score"],
                    trend_score=trend_res["score"], trigger_score=trigger_res["score"],
                    risk_score=8, ml_score=50, rr=2.0, risk_percent=1.0,
                    position_size=100, notional_size=100, leverage_suggestion=10,
                    confidence=0.8, reason=f"Sentiment: %{sentiment} | Adaptive Targets"
                )
                
                ai_res = ai_engine.evaluate(sig)
                save_paper_trade(sig.to_dict(), tracked_from=ai_res["decision"])
                
                if ai_res["decision"] in ["ALLOW", "WATCH"]:
                    logger.info(f"🚀 ELITE SİNYAL: {symbol} ({ai_res['decision']})")
                    save_scalp_signal(sig.to_dict())
                    deliver_signal(sig)
                    
                    # Trade açma mantığı (Paper)
                    if ai_res["decision"] == "ALLOW":
                        with get_conn() as conn:
                            conn.execute("""
                                INSERT INTO trades (symbol, direction, entry, sl, tp1, status, environment, open_time)
                                VALUES (?, ?, ?, ?, ?, 'open', 'paper', datetime('now'))
                            """, (symbol, trend_res["direction"], trigger_res["entry"], sl, tp))
            
            await asyncio.sleep(SCAN_INTERVAL)
        except Exception as e:
            logger.error(f"Hata: {e}")
            await asyncio.sleep(10)

if __name__ == "__main__":
    asyncio.run(main_loop())
