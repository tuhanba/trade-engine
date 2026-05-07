"""
scalp_bot_v3.py — AX Scalp Engine v5.1 — PAPER ENGINE / LIVE-BLOCKED + FALLBACK
================================================================
Ana tarama döngüsü. Binance public market data bağlantısı, yoksa CoinGecko fallback.
Adaptive scan interval, watchdog, graceful shutdown.
Hardcoded token/API key YOKTUR.
"""
import asyncio
import logging
import signal
import time
import os
import uuid
import sys
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
from core.fallback_data_provider import FallbackDataProvider, test_binance_connectivity
from core.watchdog import SystemWatchdog
from database import (
    init_db, get_paper_balance, get_open_trades,
    save_signal_candidate, save_paper_trade,
)
from core.data_layer import SignalData
from telegram_delivery import deliver_signal, send_message
from execution_engine import open_trade, monitor_trades
from core.paper_tracker import process_pending_paper_results

logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)s | %(message)s")
logger = logging.getLogger("scalp_bot_v3")

# ── Graceful Shutdown ──
_shutdown_event = asyncio.Event() if sys.platform != "win32" else None
_shutdown_flag = False


def _signal_handler(sig, frame):
    global _shutdown_flag
    logger.info(f"[Bot] Sinyal alındı ({sig}) — graceful shutdown...")
    _shutdown_flag = True
    if _shutdown_event:
        _shutdown_event.set()


# ── Adaptive Scan ──
def get_adaptive_interval(base_interval: int, hour_utc: int, open_trades: int) -> int:
    """
    Saat ve açık trade sayısına göre tarama aralığını ayarla.
    - Yoğun saatler (08-20 UTC): daha sık
    - Sakin saatler (00-07 UTC): daha seyrek
    - Açık trade varsa: daha sık (yönetim için)
    """
    if open_trades > 0:
        return max(15, base_interval // 2)  # Açık trade varsa 2x hızlı

    if 8 <= hour_utc <= 20:
        return base_interval  # Normal
    elif 3 <= hour_utc <= 7:
        return int(base_interval * 2)  # Gece: 2x yavaş
    else:
        return int(base_interval * 1.5)  # Geçiş: 1.5x yavaş


async def main_loop():
    global _shutdown_flag
    logger.info("=== AX Scalp Engine v5.1 (PAPER ENGINE + FALLBACK) Başlatılıyor ===")
    init_db()

    # Signal handlers
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            signal.signal(sig, _signal_handler)
        except (OSError, ValueError):
            pass

    # Watchdog başlat
    watchdog = SystemWatchdog(db_path=DB_PATH, check_interval=120)
    watchdog.start()

    # Fallback provider
    fallback = FallbackDataProvider()
    use_fallback = False

    # Binance Client — bağlantı retry + proxy desteği
    client = None
    max_retries = 5
    proxy = os.getenv("HTTPS_PROXY") or os.getenv("https_proxy")
    binance_tld = os.getenv("BINANCE_TLD", "com")
    use_testnet = os.getenv("BINANCE_TESTNET", "").lower() in ("true", "1", "yes")

    req_params = {}
    if proxy:
        req_params["proxies"] = {"https": proxy, "http": proxy}
        logger.info(f"Proxy aktif: {proxy[:30]}...")

    for attempt in range(1, max_retries + 1):
        try:
            client = Client(
                BINANCE_API_KEY, BINANCE_API_SECRET,
                testnet=use_testnet,
                tld=binance_tld,
                requests_params=req_params if req_params else None,
            )
            mode_str = "TESTNET PUBLIC DATA" if use_testnet else f"PUBLIC MARKET DATA (.{binance_tld})"
            logger.info(f"Binance public market data bağlantısı kuruldu: {mode_str}")
            break
        except Exception as e:
            logger.warning(f"Binance bağlantı denemesi {attempt}/{max_retries} başarısız: {e}")
            if attempt < max_retries:
                wait = min(30, 5 * attempt)
                logger.info(f"  {wait}s sonra tekrar denenecek...")
                await asyncio.sleep(wait)
            else:
                logger.warning("Binance API erişilemiyor — CoinGecko FALLBACK modu aktif.")
                use_fallback = True
                if fallback.is_available():
                    logger.info("CoinGecko erişilebilir — paper trading devam edecek.")
                    send_message(
                        "⚠️ <b>AX Engine:</b> Binance API erişilemiyor!\n"
                        "📊 CoinGecko fallback modu aktif — paper trading devam ediyor."
                    )
                else:
                    logger.error("CoinGecko da erişilemiyor — çıkılıyor.")
                    send_message("❌ <b>AX Engine:</b> Hiçbir veri kaynağına erişilemiyor!")
                    watchdog.stop()
                    return

    # Engine bileşenleri — Binance varsa normal, yoksa None
    scanner = AsyncMarketScanner(db_path=DB_PATH) if not use_fallback else None
    trend = AdvancedTrendEngine(client) if client else None
    trigger = TriggerEngine(client) if client else None
    risk = AdvancedRiskEngine(client, db_path=DB_PATH)
    ai_engine = AIDecisionEngine(db_path=DB_PATH)

    startup_msg = (
        "👑 <b>AX Engine v5.1 Başlatıldı!</b>\n"
        f"📊 Mod: {'CoinGecko Fallback' if use_fallback else 'Binance Public Data'}\n"
        f"⚙️ Paper trading aktif, real order blocked\n"
        f"🔄 Scan: {SCAN_INTERVAL}s (adaptive)"
    )
    send_message(startup_msg)

    scan_count = 0
    consecutive_errors = 0

    while not _shutdown_flag:
        try:
            scan_count += 1
            from datetime import datetime, timezone as tz
            now_utc = datetime.now(tz.utc)
            hour_utc = now_utc.hour

            # Tarama — Binance veya CoinGecko
            if use_fallback:
                from config import MIN_VOLUME_USD
                candidates = fallback.get_market_candidates(min_volume=MIN_VOLUME_USD)
            else:
                try:
                    candidates = await scanner.scan()
                except Exception as scan_err:
                    logger.warning(f"Binance scan hatası, fallback deneniyor: {scan_err}")
                    candidates = fallback.get_market_candidates()

            if not candidates:
                open_trades = get_open_trades()
                interval = get_adaptive_interval(SCAN_INTERVAL, hour_utc, len(open_trades))
                logger.debug(f"Scan #{scan_count}: 0 aday — {interval}s bekleniyor")
                await asyncio.sleep(interval)
                continue

            balance = get_paper_balance()
            open_trades = get_open_trades()

            for coin in candidates[:30]:
                if _shutdown_flag:
                    break

                symbol = coin["symbol"]

                # Zaten açık trade varsa atla
                if any(t["symbol"] == symbol for t in open_trades):
                    continue

                # Fallback modda trend/trigger analizi yapılamaz — basit sinyal üret
                if use_fallback or trigger is None:
                    # CoinGecko'dan gelen price_change ve tradeability_score ile basit karar
                    if coin.get("status") != "Eligible":
                        continue
                    if coin.get("tradeability_score", 0) < 5.0:
                        continue

                    entry = coin.get("price", 0)
                    if entry <= 0:
                        continue

                    # Basit yön: 24h change > 0 → LONG, < 0 → SHORT
                    direction = "LONG" if coin.get("price_change", 0) > 0 else "SHORT"
                    quality = "B"
                    score = coin.get("tradeability_score", 5.0)
                else:
                    trend_res = trend.analyze(symbol)
                    if trend_res["direction"] == "NO TRADE":
                        continue

                    trigger_res = trigger.analyze(symbol, trend_res["direction"])
                    if trigger_res.get("quality", "D") == "D":
                        continue

                    entry = trigger_res["entry"]
                    direction = trend_res["direction"]
                    quality = trigger_res["quality"]
                    score = trigger_res.get("score", 0)

                # Risk kontrolü
                atr_pct = trigger_res.get("atr_pct") if not use_fallback and 'trigger_res' in locals() else None
                risk_res = risk.calculate(symbol, direction, entry, quality, balance, open_trades, atr_pct=atr_pct)

                # SignalData Tek Schema Oluşturma
                from core.data_layer import SignalData
                
                sig_obj = SignalData(
                    symbol=symbol,
                    direction=direction,
                    entry_zone=entry,
                    stop_loss=risk_res.get("sl", entry * (0.98 if direction == "LONG" else 1.02)),
                    tp1=risk_res.get("tp1", entry * (1.02 if direction == "LONG" else 0.98)),
                    tp2=risk_res.get("tp2", entry * (1.04 if direction == "LONG" else 0.96)),
                    tp3=risk_res.get("tp2", entry * (1.04 if direction == "LONG" else 0.96)) * (1.01 if direction == "LONG" else 0.99),
                    setup_quality=quality,
                    final_score=score,
                    confidence=0.8,
                    leverage_suggestion=risk_res.get("leverage", 10),
                    risk_percent=risk_res.get("risk_pct", 1.0),
                    reason=f"Score: {score:.1f}" + (" [CG]" if use_fallback else "")
                )
                
                if not sig_obj.is_valid():
                    from database import update_system_state
                    update_system_state("last_error", f"Invalid Signal Schema: {symbol}")
                    # Log invalid candidate
                    sig_obj.status = "invalid_candidate"
                    sig_obj.reject_reason = "Eksik Schema Verisi"
                    save_signal_candidate({**sig_obj.to_dict(), "decision": "VETO", "reason": sig_obj.reject_reason})
                    continue

                # Sistem Geriye Dönük Uyumluluk için Dict kullanımı
                sig_data = sig_obj.to_dict()
                # Ek legacy alanlar
                sig_data["id"] = sig_obj.id[:8]
                sig_data["entry"] = sig_obj.entry_zone
                sig_data["sl"] = sig_obj.stop_loss
                sig_data["leverage"] = sig_obj.leverage_suggestion
                sig_data["risk_usd"] = risk_res.get("risk_usd", 0)
                sig_data["market_regime"] = "fallback" if use_fallback else "live"

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
                        if client and not use_fallback:
                            logger.info(f"🚀 TRADE SİNYALİ: {symbol} {direction}")
                            trade_id = open_trade(client, sig_data)
                            if trade_id:
                                open_trades = get_open_trades()
                        else:
                            logger.info(f"📊 FALLBACK SİNYAL: {symbol} {direction} "
                                       f"(paper-only, gerçek trade açılamaz)")
                    else:
                        logger.info(f"⚠️ Risk red: {symbol} - {risk_res.get('reason')}")
                elif decision == "WATCH":
                    logger.debug(f"👀 WATCH: {symbol} - {reason}")
                else:
                    logger.debug(f"🚫 VETO: {symbol} - {reason}")

            # Monitor Trades
            if client and not use_fallback:
                try:
                    closed_list = monitor_trades(client)
                    if closed_list:
                        logger.info(f"Kapanan işlemler: {closed_list}")
                except Exception as monitor_err:
                    logger.error(f"Trade monitor hatası: {monitor_err}")
                
            # AI Ghost Tracking Process
            try:
                if scan_count % 3 == 0:
                    processed_ghosts = process_pending_paper_results(client, limit=10)
                    if processed_ghosts > 0:
                        logger.info(f"👻 AI Ghost Tracking: {processed_ghosts} paper trade sonuçlandırıldı ve öğrenildi.")
                        try:
                            from database import update_system_state
                            update_system_state("last_paper_result_process_at", datetime.now(tz.utc).isoformat())
                        except: pass
            except Exception as ghost_err:
                logger.error(f"AI Ghost Tracker hatası: {ghost_err}")

        # Adaptive interval
        interval = get_adaptive_interval(SCAN_INTERVAL, hour_utc, len(open_trades))
        consecutive_errors = 0
        
        # ── SYSTEM STATE GÜNCELLEMESİ (HEARTBEAT) ──
        try:
            from database import update_system_state
            update_system_state("bot_heartbeat_at", datetime.now(tz.utc).isoformat())
            update_system_state("last_scan_time", datetime.now(tz.utc).isoformat())
            update_system_state("last_scan_status", "OK")
            update_system_state("last_scan_symbol_count", str(len(candidates) if candidates else 0))
            if client and not use_fallback:
                update_system_state("last_trade_monitor_at", datetime.now(tz.utc).isoformat())
        except Exception as e:
            logger.error(f"Heartbeat yazılamadı: {e}")
            
        logger.info(f"Scan #{scan_count}: {len(candidates) if candidates else 0} aday | "
                       f"açık: {len(open_trades)} | "
                       f"sonraki: {interval}s | "
                       f"{'fallback' if use_fallback else 'binance'}")

        await asyncio.sleep(interval)

        except Exception as e:
            consecutive_errors += 1
            logger.error(f"Ana döngü hatası ({consecutive_errors}): {e}")
            if consecutive_errors >= 10:
                logger.critical("10 ardışık hata — 5 dakika bekleniyor!")
                send_message("⚠️ <b>AX Engine:</b> 10 ardışık hata! 5dk bekleniyor...")
                await asyncio.sleep(300)
                consecutive_errors = 0
            else:
                await asyncio.sleep(min(30, 10 * consecutive_errors))

    # Graceful shutdown
    watchdog.stop()
    logger.info("=== AX Engine düzgün şekilde kapatıldı ===")
    send_message("🛑 <b>AX Engine</b> kapatıldı.")


if __name__ == "__main__":
    asyncio.run(main_loop())
