"""
scalp_bot_v3.py — AX Scalp Engine v5.0 (LIVE-READY)
====================================================
Ana tarama döngüsü. execution_engine üzerinden trade açar.
Hardcoded token/API key YOKTUR.
"""
import asyncio
import logging
import time
import os
import uuid
from binance.client import Client
from config import (
    BINANCE_API_KEY, BINANCE_API_SECRET, SCAN_INTERVAL, DB_PATH,
    ALLOWED_QUALITIES, ADX_MIN_THRESHOLD, COIN_UNIVERSE,
    TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID,
)
from core.async_market_scanner import AsyncMarketScanner
from core.advanced_trend_engine import AdvancedTrendEngine
from core.trigger_engine import TriggerEngine
from core.advanced_risk_engine import AdvancedRiskEngine
from core.ai_decision_engine import AIDecisionEngine
from database import (
    init_db, get_paper_balance, get_open_trades,
    save_signal_candidate, save_paper_trade,
)
from core.data_layer import SignalData
from telegram_delivery import deliver_signal, send_message
from execution_engine import open_trade

logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)s | %(message)s")
logger = logging.getLogger("scalp_bot_v3")


async def main_loop():
    logger.info("=== AX Scalp Engine v5.0 (LIVE-READY) Başlatılıyor ===")
    init_db()

    # Binance Client — bağlantı retry
    client = None
    max_retries = 5
    for attempt in range(1, max_retries + 1):
        try:
            use_testnet = os.getenv("BINANCE_TESTNET", "").lower() in ("true", "1", "yes")
            if use_testnet:
                client = Client(
                    BINANCE_API_KEY, BINANCE_API_SECRET,
                    testnet=True
                )
                logger.info("Binance TESTNET bağlantısı kuruldu.")
            else:
                client = Client(BINANCE_API_KEY, BINANCE_API_SECRET)
                logger.info("Binance LIVE bağlantısı kuruldu.")
            break
        except Exception as e:
            logger.warning(f"Binance bağlantı denemesi {attempt}/{max_retries} başarısız: {e}")
            if attempt < max_retries:
                wait = min(30, 5 * attempt)
                logger.info(f"  {wait}s sonra tekrar denenecek...")
                await asyncio.sleep(wait)
            else:
                logger.error("Binance API'ye bağlanılamadı! VPN gerekli olabilir.")
                send_message("❌ <b>AX Engine:</b> Binance API bağlantısı kurulamadı!\nVPN aktif mi kontrol edin.")
                return

    scanner = AsyncMarketScanner(db_path=DB_PATH)
    trend = AdvancedTrendEngine(client)
    trigger = TriggerEngine(client)
    risk = AdvancedRiskEngine(client, db_path=DB_PATH)
    ai_engine = AIDecisionEngine(db_path=DB_PATH)

    send_message("👑 <b>AX Engine v5.0 Başlatıldı!</b>\nLive-Ready mode aktif.")

    while True:
        try:
            candidates = await scanner.scan()
            if not candidates:
                await asyncio.sleep(SCAN_INTERVAL)
                continue

            balance = get_paper_balance()
            open_trades = get_open_trades()

            for coin in candidates[:30]:
                symbol = coin["symbol"]

                # Zaten açık trade varsa atla
                if any(t["symbol"] == symbol for t in open_trades):
                    continue

                trend_res = trend.analyze(symbol)
                if trend_res["direction"] == "NO TRADE":
                    continue

                trigger_res = trigger.analyze(symbol, trend_res["direction"])
                if trigger_res.get("quality", "D") == "D":
                    continue

                # Risk kontrolü
                risk_res = risk.calculate(
                    symbol, trend_res["direction"],
                    trigger_res["entry"], trigger_res["quality"],
                    balance, open_trades
                )

                # Sinyal oluştur
                sig_data = {
                    "id": str(uuid.uuid4())[:8],
                    "symbol": symbol,
                    "direction": trend_res["direction"],
                    "entry": trigger_res["entry"],
                    "sl": risk_res.get("sl", trigger_res["entry"] * 0.98),
                    "tp1": risk_res.get("tp1", trigger_res["entry"] * 1.02),
                    "tp2": risk_res.get("tp2", trigger_res["entry"] * 1.04),
                    "tp3": risk_res.get("tp2", trigger_res["entry"] * 1.04) * 1.01,
                    "setup_quality": trigger_res["quality"],
                    "final_score": trigger_res.get("score", 0),
                    "confidence": 0.8,
                    "leverage": risk_res.get("leverage", 10),
                    "risk_usd": risk_res.get("risk_usd", 0),
                    "market_regime": trend_res.get("regime"),
                    "reason": f"Score: {trigger_res.get('score', 0):.1f}",
                }

                # AI karar
                decision, reason = ai_engine.decide(sig_data)

                # Sinyal kaydet (her karar için)
                save_signal_candidate({
                    **sig_data,
                    "uuid": sig_data["id"],
                    "decision": decision,
                    "reason": reason,
                })

                # Paper result kaydet (ghost learning için)
                save_paper_trade(sig_data, tracked_from=decision)

                if decision == "ALLOW":
                    if risk_res.get("valid", False):
                        logger.info(f"🚀 TRADE SİNYALİ: {symbol} {trend_res['direction']}")
                        trade_id = open_trade(client, sig_data)
                        if trade_id:
                            open_trades = get_open_trades()  # Güncelle
                    else:
                        logger.info(f"⚠️ Risk red: {symbol} - {risk_res.get('reason')}")
                elif decision == "WATCH":
                    logger.info(f"👀 WATCH: {symbol} - {reason}")
                else:
                    logger.debug(f"🚫 VETO: {symbol} - {reason}")

            await asyncio.sleep(SCAN_INTERVAL)
        except Exception as e:
            logger.error(f"Ana döngü hatası: {e}")
            await asyncio.sleep(10)


if __name__ == "__main__":
    asyncio.run(main_loop())
