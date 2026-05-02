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
    DAILY_MAX_LOSS_PCT,
    DATA_THRESHOLD, WATCHLIST_THRESHOLD, TELEGRAM_THRESHOLD, TRADE_THRESHOLD,
    LIVE_CONFIRM,
)
from database import (
    init_db, init_paper_account, get_paper_balance,
    get_open_trades, set_state, get_state,
    save_scalp_signal, archive_old_scalp_signals,
    get_daily_signal_count,
    log_signal_event, log_trade_event,
    is_telegram_sent, mark_telegram_sent,
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

    # Bot restart sonrası açık sinyalleri geri yükle
    data_layer.restore_from_db()

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
    _last_ai_adapt = 0  # AI Brain periyodik adaptasyon (30 dakikada bir)
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

            # Günlük kayıp limiti kontrolü (DAILY_MAX_LOSS_PCT)
            try:
                from database import get_conn
                today_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")
                with get_conn() as _conn:
                    _row = _conn.execute(
                        "SELECT SUM(net_pnl) FROM trades WHERE DATE(close_time)=? AND close_time IS NOT NULL",
                        (today_str,)
                    ).fetchone()
                today_loss = _row[0] or 0.0
                balance = get_paper_balance()
                max_loss_abs = balance * (DAILY_MAX_LOSS_PCT / 100.0)
                if today_loss < -max_loss_abs:
                    logger.warning(
                        f"⛔ Günlük kayıp limiti aşıldı: {today_loss:.2f}$ "
                        f"(limit: -{max_loss_abs:.2f}$) — yeni trade alınmıyor."
                    )
                    send_message(
                        f"⛔ Günlük kayıp limiti aşıldı!\n"
                        f"Bugün: {today_loss:.2f}$ | Limit: -{max_loss_abs:.2f}$\n"
                        f"Yeni sinyal alımı durduruldu."
                    )
                    time.sleep(300)  # 5 dakika bekle, tekrar kontrol et
                    continue
            except Exception as _e:
                logger.warning(f"Günlük kayıp kontrolü hatası: {_e}")

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
                    exec_monitor(client)

            # Açık trade limiti
            open_trades_now = get_open_trades()
            if len(open_trades_now) >= MAX_OPEN_TRADES:
                time.sleep(5)
                continue

            open_symbols = {t["symbol"] for t in open_trades_now}

            # ── ADIM 1: MARKET SCANNER ─────────────────────────────────────
            candidates = scanner.scan(save_to_db=True)
            if not candidates:
                logger.debug("Market scanner: aday bulunamadı")
                time.sleep(SCAN_INTERVAL)
                continue

            eligible = [c for c in candidates if c["scanner_status"] == "ELIGIBLE"]
            logger.info(f"Scanner: {len(candidates)} tarandı, {len(eligible)} ELIGIBLE")

            archive_old_scalp_signals(hours=24)

            for coin_info in eligible[:50]:
                symbol = coin_info["symbol"]

                if symbol in open_symbols:
                    continue

                daily_counts = get_daily_signal_count()
                if daily_counts["total"] >= MAX_DAILY_SIGNALS:
                    break

                try:
                    # ── SCANNED stage ──────────────────────────────────────
                    sig = data_layer.create_signal(symbol)
                    sig.coin_score  = coin_info.get("tradeability_score", 0)
                    data_layer.update_stage(sig, "SCANNED")

                    # ── ADIM 2: TREND ENGINE ───────────────────────────────
                    trend_result = trend.analyze(symbol)
                    data_layer.update_stage(sig, "TREND_CHECKED",
                                            {"trend_score": trend_result.get("score", 0)})
                    if trend_result["direction"] == "NO TRADE":
                        data_layer.reject(sig, "weak_trend",
                                          trend_result.get("reason", "no_direction"))
                        continue

                    sig.direction   = trend_result["direction"]
                    sig.trend_score = trend_result.get("score", 0)

                    # ── ADIM 3: TRIGGER ENGINE ─────────────────────────────
                    trigger_result = trigger.analyze(
                        symbol, trend_result["direction"],
                        trend_result.get("btc_trend", "NEUTRAL")
                    )
                    data_layer.update_stage(sig, "TRIGGER_CHECKED",
                                            {"trigger_score": trigger_result.get("score", 0)})
                    if trigger_result["quality"] in ("D", ""):
                        data_layer.reject(sig, "weak_trigger", trigger_result.get("reason"))
                        continue

                    sig.trigger_score = trigger_result.get("score", 0)
                    sig.entry_zone    = trigger_result.get("entry", 0)
                    sig.setup_quality = trigger_result["quality"]

                    # ── ADIM 4: RISK ENGINE ────────────────────────────────
                    balance = get_paper_balance()
                    risk_result = risk.calculate(
                        symbol, trend_result["direction"],
                        trigger_result.get("entry", 0),
                        trigger_result["quality"],
                        balance
                    )
                    data_layer.update_stage(sig, "RISK_CHECKED",
                                            {"risk_score": risk_result.get("score", 0)})
                    if not risk_result.get("valid"):
                        data_layer.reject(sig, risk_result.get("risk_reject_reason", "risk_guard_failed"))
                        continue

                    sig.risk_score          = risk_result.get("score", 0)
                    sig.stop_loss           = risk_result["sl"]
                    sig.tp1                 = risk_result["tp1"]
                    sig.tp2                 = risk_result["tp2"]
                    sig.tp3                 = risk_result["tp3"]
                    sig.rr                  = risk_result["rr"]
                    sig.net_rr              = risk_result.get("net_rr", 0)
                    sig.risk_percent        = risk_result["risk_pct"]
                    sig.risk_amount         = risk_result.get("risk_amount", 0)
                    sig.position_size       = risk_result["position_size"]
                    sig.notional_size       = risk_result["notional"]
                    sig.leverage_suggestion = risk_result["leverage"]
                    sig.max_loss            = risk_result["max_loss"]
                    sig.estimated_fee       = risk_result.get("estimated_fee", 0)
                    sig.estimated_slippage  = risk_result.get("estimated_slippage", 0)

                    # ── ADIM 5: AI DECISION ENGINE ─────────────────────────
                    decision = ai_engine.evaluate(sig)
                    sig.ai_score    = decision.get("ai_score", 0)
                    sig.final_score = decision["final_score"]
                    sig.confidence  = decision["confidence"]
                    sig.reason      = decision["reason"]
                    data_layer.update_stage(sig, "AI_CHECKED",
                                            {"ai_score": sig.ai_score,
                                             "final_score": sig.final_score})

                    if decision["decision"] != "ALLOW":
                        data_layer.reject(sig, "ai_veto", decision["reason"])
                        logger.info(
                            f"[AI VETO] {symbol} {sig.direction} | {decision['reason']} | "
                            f"score={sig.final_score:.1f}"
                        )
                        continue

                    # ── ADIM 6: Eşik değerlendirme ─────────────────────────
                    data_layer.evaluate_thresholds(sig)

                    if not sig.passes_data_threshold():
                        data_layer.reject(sig, "low_confidence",
                                          f"score={sig.final_score:.1f}<{DATA_THRESHOLD}")
                        continue

                    if sig.approved_for_watchlist:
                        data_layer.approve_watchlist(sig)
                        logger.info(
                            f"[WATCHLIST] {symbol} {sig.direction} "
                            f"score={sig.final_score:.1f} quality={sig.setup_quality}"
                        )

                    # ── ADIM 7: Telegram ───────────────────────────────────
                    if sig.approved_for_telegram:
                        if not is_telegram_sent(sig.id):
                            data_layer.approve_telegram(sig)
                            deliver_signal(sig)
                            mark_telegram_sent(sig.id, sig.symbol, sig.direction or "", sig.setup_quality)
                        else:
                            logger.debug(f"Telegram duplicate engellendi (DB): {symbol}")

                    # ── ADIM 8: DB'ye persist ─────────────────────────────
                    data_layer.persist(sig)

                    logger.info(
                        f"[{sig.setup_quality}] {symbol} {sig.direction} "
                        f"Entry={sig.entry_zone:.6f} SL={sig.stop_loss:.6f} "
                        f"RR={sig.rr:.2f} netRR={sig.net_rr:.2f} "
                        f"Score={sig.final_score:.1f} "
                        f"[tg={'✓' if sig.approved_for_telegram else '—'} "
                        f"trade={'✓' if sig.approved_for_trade else '—'}]"
                    )

                    # ── ADIM 9: TRADE AÇMA ─────────────────────────────────
                    if (AX_MODE == "execute" and sig.approved_for_trade
                            and EXECUTION_AVAILABLE):
                        # Live mode guard
                        if EXECUTION_MODE == "live" and not LIVE_CONFIRM:
                            logger.warning(
                                f"LIVE MODE: LIVE_CONFIRM=false — {symbol} işlem açılmadı!"
                            )
                        else:
                            legacy_signal = {
                                "symbol": sig.symbol, "direction": sig.direction,
                                "entry": sig.entry_zone, "sl": sig.stop_loss,
                                "tp1": sig.tp1, "tp2": sig.tp2,
                                "runner_target": sig.tp3, "rr": sig.rr,
                                "score": sig.final_score, "confidence": sig.confidence,
                            }
                            ax_result = {
                                "decision": "ALLOW", "score": sig.final_score,
                                "confidence": sig.confidence, "veto_reason": None,
                            }
                            trade_id = open_trade(client, legacy_signal, ax_result)
                            if trade_id:
                                data_layer.mark_opened(sig, trade_id)
                                log_trade_event(trade_id, "OPENED",
                                                price=sig.entry_zone,
                                                detail=f"score={sig.final_score:.1f}")
                                logger.info(
                                    f"✅ TRADE AÇILDI #{trade_id} {symbol} "
                                    f"{sig.direction} score={sig.final_score:.1f}"
                                )

                except Exception as e:
                    logger.error(f"Coin işleme hatası {symbol}: {e}", exc_info=True)
                    if 'sig' in locals():
                        data_layer.mark_error(sig, str(e))
                    continue

            time.sleep(SCAN_INTERVAL)
            # ── AI Brain Periyodik Adaptasyon (30 dakikada bir) ─────────────
            _now_ts = time.time()
            if AI_BRAIN_AVAILABLE and (_now_ts - _last_ai_adapt) >= 1800:
                try:
                    from ai_brain import analyze_and_adapt
                    threading.Thread(target=analyze_and_adapt, daemon=True).start()
                    _last_ai_adapt = _now_ts
                    logger.info("[AI Brain] Periyodik adaptasyon baslatildi.")
                except Exception as _ae:
                    logger.warning(f"AI Brain adaptasyon hatasi: {_ae}")

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
