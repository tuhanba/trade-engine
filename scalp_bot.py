"""
scalp_bot.py — AX Scalp Engine Ana Döngü v2.0
===============================================
Mimari:
  Market Scanner → Trend Engine → Trigger Engine → Risk Engine
  → AI Decision Engine → Data Layer → Dashboard + Telegram

Veri akışı:
  Sadece Data Layer'dan validate edilmiş veri Dashboard ve Telegram'a gider.
  Ham veri hiçbir zaman direkt gönderilmez.
"""
import os
import time
import logging
import logging.handlers
import threading
from datetime import datetime, timezone, timedelta
from binance.client import Client

from config import (
    BINANCE_API_KEY, BINANCE_API_SECRET,
    SCAN_INTERVAL, MAX_OPEN_TRADES,
    CIRCUIT_BREAKER_LOSSES, CIRCUIT_BREAKER_MINUTES,
    PAPER_MODE, AX_MODE, EXECUTION_MODE, COIN_UNIVERSE,
    LOG_DIR, LOG_MAX_DAYS, LOG_MAX_MB,
    MAX_DAILY_SIGNALS, DB_PATH,
)
from database import (
    init_db, init_paper_account, get_paper_balance,
    get_open_trades, set_state, get_state,
    save_scalp_signal, archive_old_scalp_signals,
    get_daily_signal_count,
)
from core.data_layer import data_layer, SignalData
from core.market_scanner import MarketScanner
from core.trend_engine import TrendEngine
from core.trigger_engine import TriggerEngine
from core.risk_engine import RiskEngine
from core.ai_decision_engine import AIDecisionEngine
from telegram_delivery import deliver_signal, send_message

# Geriye dönük uyumluluk için eski modüller
try:
    from ai_brain import post_trade_analysis, set_client as ai_set_client
    AI_BRAIN_AVAILABLE = True
except ImportError:
    AI_BRAIN_AVAILABLE = False

try:
    from execution_engine import open_trade, monitor_trades as exec_monitor
    EXECUTION_AVAILABLE = True
except ImportError:
    EXECUTION_AVAILABLE = False

try:
    from telegram_manager import TelegramManager
    TG_MANAGER_AVAILABLE = True
except ImportError:
    TG_MANAGER_AVAILABLE = False

# ─────────────────────────────────────────────────────────────────────────────
# LOGGING
# ─────────────────────────────────────────────────────────────────────────────
os.makedirs(LOG_DIR, exist_ok=True)
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
    handlers=[
        logging.StreamHandler(),
        logging.handlers.RotatingFileHandler(
            os.path.join(LOG_DIR, "ax_bot.log"),
            maxBytes=LOG_MAX_MB * 1024 * 1024,
            backupCount=LOG_MAX_DAYS,
            encoding="utf-8",
        ),
    ],
)
logger = logging.getLogger("scalp_bot")

# ─────────────────────────────────────────────────────────────────────────────
# DEVRE KESİCİ
# ─────────────────────────────────────────────────────────────────────────────
def is_circuit_breaker_active() -> tuple:
    """(aktif_mi, kalan_dakika)"""
    cb_until = get_state("circuit_breaker_until")
    if not cb_until:
        return False, 0
    try:
        until = datetime.fromisoformat(cb_until)
        now   = datetime.now(timezone.utc)
        if until.tzinfo is None:
            until = until.replace(tzinfo=timezone.utc)
        if now < until:
            remaining = int((until - now).total_seconds() / 60)
            return True, remaining
    except Exception:
        pass
    return False, 0

def activate_circuit_breaker():
    until = (datetime.now(timezone.utc) + timedelta(minutes=CIRCUIT_BREAKER_MINUTES)).isoformat()
    set_state("circuit_breaker_until", until)
    logger.warning(f"Devre kesici aktif — {CIRCUIT_BREAKER_MINUTES}dk")
    send_message(f"⛔ Devre kesici aktif — {CIRCUIT_BREAKER_MINUTES} dakika bekleniyor.")

# ─────────────────────────────────────────────────────────────────────────────
# ANA DÖNGÜ
# ─────────────────────────────────────────────────────────────────────────────
def main():
    logger.info("=== AX Scalp Engine v2.0 başlatılıyor ===")

    # DB ve hesap başlat
    init_db()
    init_paper_account()

    # Binance client
    client = Client(BINANCE_API_KEY, BINANCE_API_SECRET)
    if AI_BRAIN_AVAILABLE:
        ai_set_client(client, send_message)

    # Modülleri başlat
    scanner  = MarketScanner(client, db_path=DB_PATH)
    trend    = TrendEngine(client)
    trigger  = TriggerEngine(client)
    risk     = RiskEngine(client)
    ai_engine = AIDecisionEngine(db_path=DB_PATH)

    # Telegram Manager (komut dinleyici)
    tg_manager = None
    if TG_MANAGER_AVAILABLE:
        try:
            tg_manager = TelegramManager(send_message)
            tg_manager.start()
        except Exception as e:
            logger.warning(f"TelegramManager başlatılamadı: {e}")

    consecutive_losses = 0
    send_message(
        f"🚀 <b>AX Scalp Engine v2.0 başlatıldı</b>\n"
        f"Mod: {AX_MODE.upper()} | {EXECUTION_MODE.upper()}\n"
        f"Bakiye: ${get_paper_balance():.2f}"
    )

    while True:
        try:
            # Pause kontrolü
            if tg_manager and hasattr(tg_manager, 'is_paused') and tg_manager.is_paused:
                time.sleep(5)
                continue

            # Devre kesici
            cb_active, cb_remaining = is_circuit_breaker_active()
            if cb_active:
                logger.info(f"Devre kesici — {cb_remaining}dk kaldı")
                time.sleep(10)
                continue

            # Finish modu
            if tg_manager and hasattr(tg_manager, 'is_finish_mode') and tg_manager.is_finish_mode:
                if not get_open_trades():
                    send_message(f"✅ Finish modu tamamlandı. Bakiye: ${get_paper_balance():.2f}")
                    logger.info("Finish modu tamamlandı.")
                    break
                time.sleep(10)
                continue

            # Günlük sinyal limiti kontrolü
            daily_counts = get_daily_signal_count()
            if daily_counts["total"] >= MAX_DAILY_SIGNALS:
                logger.info(f"Günlük sinyal limiti doldu ({MAX_DAILY_SIGNALS})")
                time.sleep(60)
                continue

            # Açık trade takibi
            if EXECUTION_AVAILABLE:
                open_trades_now = get_open_trades()
                if open_trades_now:
                    exec_monitor(client, open_trades_now)

            # Açık trade limiti
            open_trades_now = get_open_trades()
            if len(open_trades_now) >= MAX_OPEN_TRADES:
                time.sleep(5)
                continue

            open_symbols = {t["symbol"] for t in open_trades_now}

            # ── ADIM 1: MARKET SCANNER ─────────────────────────────────────
            candidates = scanner.scan()
            if not candidates:
                logger.debug("Market scanner: aday bulunamadı")
                time.sleep(SCAN_INTERVAL)
                continue

            # Sadece Eligible olanları işle
            eligible = [c for c in candidates if c["status"] == "Eligible"]
            logger.info(f"Scanner: {len(eligible)} eligible coin bulundu")

            # Eski sinyalleri arşivle
            archive_old_scalp_signals(hours=24)

            for coin_info in eligible[:50]:  # Max 50 coin işle
                symbol = coin_info["symbol"]

                if symbol in open_symbols:
                    continue

                # Günlük limit tekrar kontrol
                daily_counts = get_daily_signal_count()
                if daily_counts["total"] >= MAX_DAILY_SIGNALS:
                    break

                try:
                    # ── ADIM 2: TREND ENGINE ───────────────────────────────
                    trend_result = trend.analyze(symbol)
                    if trend_result["direction"] == "NO TRADE":
                        continue

                    # ── ADIM 3: TRIGGER ENGINE ─────────────────────────────
                    trigger_result = trigger.analyze(symbol, trend_result["direction"])
                    if trigger_result["quality"] == "D":
                        continue

                    # C kalitesi sadece dashboard'da izlenir
                    if trigger_result["quality"] == "C":
                        # Sadece kaydet, Telegram'a gönderme
                        sig = data_layer.create_signal(symbol)
                        sig.direction = trend_result["direction"]
                        sig.trend_score = trend_result["score"]
                        sig.trigger_score = trigger_result["score"]
                        sig.coin_score = coin_info["tradeability_score"]
                        sig.entry_zone = trigger_result["entry"]
                        sig.setup_quality = "C"
                        sig.status = "watch"
                        sig.dashboard_status = "active"
                        sig.telegram_status = "skip"
                        save_scalp_signal(sig.to_dict())
                        continue

                    # ── ADIM 4: RISK ENGINE ────────────────────────────────
                    balance = get_paper_balance()
                    risk_result = risk.calculate(
                        symbol, trend_result["direction"],
                        trigger_result["entry"],
                        trigger_result["quality"],
                        balance
                    )
                    if not risk_result.get("valid"):
                        continue

                    # ── ADIM 5: DATA LAYER — SİNYAL OLUŞTUR ───────────────
                    sig = data_layer.create_signal(symbol)
                    sig.direction = trend_result["direction"]
                    sig.coin_score = coin_info["tradeability_score"]
                    sig.trend_score = trend_result["score"]
                    sig.trigger_score = trigger_result["score"]
                    sig.risk_score = risk_result["score"]
                    sig.setup_quality = trigger_result["quality"]
                    sig.entry_zone = trigger_result["entry"]
                    sig.stop_loss = risk_result["sl"]
                    sig.tp1 = risk_result["tp1"]
                    sig.tp2 = risk_result["tp2"]
                    sig.tp3 = risk_result["tp3"]
                    sig.rr = risk_result["rr"]
                    sig.risk_percent = risk_result["risk_pct"]
                    sig.position_size = risk_result["position_size"]
                    sig.notional_size = risk_result["notional"]
                    sig.leverage_suggestion = risk_result["leverage"]
                    sig.max_loss = risk_result["max_loss"]
                    sig.status = "ready"
                    sig.dashboard_status = "active"

                    # ── ADIM 6: AI DECISION ENGINE ─────────────────────────
                    decision = ai_engine.evaluate(sig)
                    sig.final_score = decision["final_score"]
                    sig.confidence = decision["confidence"]
                    sig.reason = decision["reason"]

                    if decision["decision"] != "ALLOW":
                        sig.status = "vetoed"
                        sig.telegram_status = "skip"
                        save_scalp_signal(sig.to_dict())
                        logger.info(
                            f"[VETO] {symbol} {sig.direction} | {decision['reason']} | "
                            f"score={sig.final_score}"
                        )
                        continue

                    sig.status = "approved"

                    # ── ADIM 7: DATA LAYER'A KAYDET ────────────────────────
                    save_scalp_signal(sig.to_dict())

                    # ── ADIM 8: TELEGRAM DELIVERY ──────────────────────────
                    deliver_signal(sig)

                    # ── ADIM 9: TRADE AÇMA (execute modunda) ──────────────
                    if AX_MODE == "execute" and EXECUTION_AVAILABLE:
                        # Eski signal formatına dönüştür (geriye uyumluluk)
                        legacy_signal = {
                            "symbol": sig.symbol,
                            "direction": sig.direction,
                            "entry": sig.entry_zone,
                            "sl": sig.stop_loss,
                            "tp1": sig.tp1,
                            "tp2": sig.tp2,
                            "runner_target": sig.tp3,
                            "rr": sig.rr,
                            "score": sig.final_score,
                            "confidence": sig.confidence,
                        }
                        ax_result = {
                            "decision": "ALLOW",
                            "score": sig.final_score,
                            "confidence": sig.confidence,
                            "veto_reason": None,
                        }
                        trade_id = open_trade(client, legacy_signal, ax_result)
                        if trade_id:
                            logger.info(
                                f"✅ TRADE AÇILDI #{trade_id} {symbol} {sig.direction} "
                                f"score={sig.final_score:.1f} rr={sig.rr:.2f}"
                            )

                    logger.info(
                        f"✅ SİNYAL [{sig.setup_quality}] {symbol} {sig.direction} "
                        f"Entry={sig.entry_zone:.6f} SL={sig.stop_loss:.6f} "
                        f"RR={sig.rr:.2f} Score={sig.final_score:.1f}"
                    )

                    # AI Öğrenme: Trade kapandığında çağrılır (execution_engine callback)
                    # Örnek: ai_engine.learn_from_trade(symbol, "WIN", pnl, sig.setup_quality)

                except Exception as e:
                    logger.error(f"Coin işleme hatası {symbol}: {e}", exc_info=True)
                    continue

            time.sleep(SCAN_INTERVAL)

        except KeyboardInterrupt:
            logger.info("Bot durduruldu (KeyboardInterrupt).")
            send_message("⛔ AX Scalp Engine durduruldu.")
            break
        except Exception as e:
            logger.error(f"Ana döngü hatası: {e}", exc_info=True)
            time.sleep(10)

# ─────────────────────────────────────────────────────────────────────────────
# GİRİŞ NOKTASI
# ─────────────────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    main()
