import asyncio
import logging
import time
import uuid
import requests
import pandas as pd
from binance.client import Client
from config import (
    BINANCE_API_KEY, BINANCE_API_SECRET, DB_PATH, SCAN_INTERVAL,
    TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID, ENABLE_LIVE_TRADING
)
from core.async_market_scanner import AsyncMarketScanner
from core.advanced_trend_engine import AdvancedTrendEngine
from core.trigger_engine import TriggerEngine
from core.advanced_risk_engine import AdvancedRiskEngine
from core.ai_decision_engine import AIDecisionEngine
from database import init_db, get_paper_balance, get_open_trades, save_scalp_signal, save_paper_trade
from core.data_layer import SignalData
from telegram_delivery import deliver_signal, send_trade_open
import execution_engine

logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)s | %(message)s")
logger = logging.getLogger("scalp_bot_v3")

# Duplicate mesaj engeli
_sent_signals = set()

def send_direct_message(text):
    url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
    try:
        requests.post(url, json={"chat_id": TELEGRAM_CHAT_ID, "text": text, "parse_mode": "HTML"}, timeout=10)
    except Exception:
        pass

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
        except:
            return 50.0

    def calculate_adaptive_targets(self, symbol, entry, direction):
        try:
            klines = self.client.futures_klines(symbol=symbol, interval="1h", limit=20)
            df = pd.DataFrame(klines, columns=['t','o','h','l','c','v','ct','q','n','tb','tq','i'])
            df['h'], df['l'], df['c'] = df['h'].astype(float), df['l'].astype(float), df['c'].astype(float)
            atr = (df['h'] - df['l']).mean()
            sl_dist = atr * 1.5
            tp_dist = atr * 2.5
            sl = entry - sl_dist if direction == "LONG" else entry + sl_dist
            tp1 = entry + tp_dist if direction == "LONG" else entry - tp_dist
            tp2 = entry + tp_dist * 1.5 if direction == "LONG" else entry - tp_dist * 1.5
            tp3 = entry + tp_dist * 2.0 if direction == "LONG" else entry - tp_dist * 2.0
            return sl, tp1, tp2, tp3, atr
        except:
            sl = entry * 0.98 if direction == "LONG" else entry * 1.02
            tp1 = entry * 1.04 if direction == "LONG" else entry * 0.96
            tp2 = entry * 1.06 if direction == "LONG" else entry * 0.94
            tp3 = entry * 1.08 if direction == "LONG" else entry * 0.92
            return sl, tp1, tp2, tp3, 0.0

async def main_loop():
    logger.info("=== AX Scalp Engine v3.9 (ULTIMATE ELITE) Baslatiliyor ===")
    init_db()
    client = Client(BINANCE_API_KEY, BINANCE_API_SECRET)

    scanner = AsyncMarketScanner(db_path=DB_PATH)
    trend = AdvancedTrendEngine(client)
    trigger = TriggerEngine(client)
    risk = AdvancedRiskEngine(client, db_path=DB_PATH)
    ai_engine = AIDecisionEngine(db_path=DB_PATH)
    elite = UltimateEliteEngine(client)

    send_direct_message("👑 <b>AX ULTIMATE ELITE Surumu Baslatildi!</b>\nSentiment, Adaptive SL/TP ve AI Post-Mortem aktif.")

    while True:
        try:
            sentiment = elite.get_sentiment()
            logger.info(f"🌍 Piyasa Duyarliligi: %{sentiment}")

            candidates = await scanner.scan()
            if not candidates:
                await asyncio.sleep(SCAN_INTERVAL)
                continue

            for coin in candidates[:20]:
                symbol = coin["symbol"]
                trend_res = trend.analyze(symbol)
                if trend_res["direction"] == "NO TRADE":
                    continue

                if sentiment < 20 and trend_res["direction"] == "LONG":
                    continue

                trigger_res = trigger.analyze(symbol, trend_res["direction"])
                entry = trigger_res["entry"]
                sl, tp1, tp2, tp3, atr = elite.calculate_adaptive_targets(symbol, entry, trend_res["direction"])
                runner_target = tp3  # Runner hedef = TP3

                sig = SignalData(
                    id=str(uuid.uuid4())[:8],
                    symbol=symbol,
                    timestamp=time.time(),
                    direction=trend_res["direction"],
                    entry_zone=entry,
                    stop_loss=sl,
                    tp1=tp1,
                    tp2=tp2,
                    tp3=tp3,
                    setup_quality=trigger_res["quality"],
                    coin_score=coin["tradeability_score"],
                    trend_score=trend_res["score"],
                    trigger_score=trigger_res["score"],
                    risk_score=8,
                    ml_score=50,
                    rr=2.0,
                    risk_percent=1.0,
                    position_size=100,
                    notional_size=100,
                    leverage_suggestion=10,
                    confidence=0.8,
                    reason=f"Sentiment: %{sentiment} | Adaptive Targets"
                )

                ai_res = ai_engine.evaluate(sig)
                decision = ai_res["decision"]

                # Sinyal DB'ye kaydet
                sig_dict = sig.to_dict()
                sig_dict["decision"] = decision
                save_paper_trade(sig_dict, tracked_from=decision)

                if decision == "ALLOW":
                    # Duplicate engeli
                    sig_key = f"{symbol}_{round(entry, 6)}_{trend_res['direction']}"
                    if sig_key in _sent_signals:
                        logger.info(f"[Bot] Duplicate sinyal atildi: {symbol}")
                        continue
                    _sent_signals.add(sig_key)
                    # 100 kayittan fazla olunca eski entryleri temizle
                    if len(_sent_signals) > 100:
                        _sent_signals.clear()

                    logger.info(f"🚀 ALLOW: {symbol} - execution_engine.open_trade cagrilıyor")
                    save_scalp_signal(sig_dict)
                    deliver_signal(sig)

                    # execution_engine.open_trade ile paper trade ac (manuel SQL yok)
                    signal_for_exec = {
                        "symbol": symbol,
                        "direction": trend_res["direction"],
                        "entry": entry,
                        "sl": sl,
                        "tp1": tp1,
                        "tp2": tp2,
                        "runner_target": runner_target,
                        "tp3": tp3,
                        "atr": atr,
                        "confidence": 0.8,
                        "score": ai_res.get("final_score", 0),
                        "setup_quality": trigger_res["quality"],
                        "candidate_id": sig.id,
                    }
                    trade_id = execution_engine.open_trade(client, signal_for_exec, ai_res)
                    if trade_id:
                        send_trade_open({"symbol": symbol, "direction": trend_res["direction"], "entry": entry})
                        logger.info(f"[Bot] PAPER TRADE ACILDI #{trade_id} {symbol}")
                    else:
                        logger.warning(f"[Bot] open_trade basarisiz: {symbol}")

                elif decision == "WATCH":
                    # WATCH: sadece sinyal gonder, trade acma
                    logger.info(f"👀 WATCH: {symbol} - sinyal gonderildi, trade acilmadi")
                    save_scalp_signal(sig_dict)
                    deliver_signal(sig)

                elif decision == "VETO":
                    # REJECT: sadece logla
                    logger.info(f"❌ VETO/REJECT: {symbol} - reason: {ai_res.get('reason', '')}")

            await asyncio.sleep(SCAN_INTERVAL)
        except Exception as e:
            logger.error(f"Hata: {e}")
            await asyncio.sleep(10)

if __name__ == "__main__":
    asyncio.run(main_loop())
