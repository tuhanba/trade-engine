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
    EXECUTION_MODE, LIVE_CONFIRM, MAX_LEVERAGE, MIN_RR,
)
from database import (
    init_db, init_paper_account, get_paper_balance,
    get_open_trades, set_state, get_state,
    save_scalp_signal, archive_old_scalp_signals,
    get_daily_signal_count,
    save_candidate_signal, save_signal_event, update_candidate_status, save_paper_result,
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
                    signal_id = data_layer.create_signal(symbol).id
                    save_signal_event(signal_id, "SCANNED", symbol=symbol, reason="scanner_pass")
                    trend_result = trend.analyze(symbol)
                    if trend_result["direction"] == "NO TRADE":
                        save_signal_event(signal_id, "REJECTED", symbol=symbol, reject_reason="weak_trend", reason="trend_engine_no_trade")
                        continue
                    save_signal_event(signal_id, "TREND_CHECKED", symbol=symbol, reason="trend_pass")

                    # ── ADIM 3: TRIGGER ENGINE ─────────────────────────────
                    trigger_result = trigger.analyze(symbol, trend_result["direction"], trend_result.get("btc_trend", "NEUTRAL"))
                    if trigger_result["quality"] == "D":
                        save_signal_event(signal_id, "REJECTED", symbol=symbol, reject_reason="weak_trigger", reason="trigger_quality_d")
                        continue
                    save_signal_event(signal_id, "TRIGGER_CHECKED", symbol=symbol, reason="trigger_pass")

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
                        candidate_id = save_candidate_signal({
                            "signal_id": sig.id,
                            "symbol": symbol,
                            "direction": sig.direction,
                            "trend_score": sig.trend_score,
                            "trigger_score": sig.trigger_score,
                            "final_score": max(DATA_THRESHOLD, 50),
                            "quality": "C",
                            "reason": "watchlist_only_candidate",
                            "execution_status": "candidate",
                            "lifecycle_stage": "APPROVED_FOR_WATCHLIST",
                        })
                        save_signal_event(sig.id, "APPROVED_FOR_WATCHLIST", symbol=symbol, reason="quality_c_watchlist")
                        if True:
                            save_paper_result({
                                "signal_id": sig.id,
                                "candidate_id": candidate_id,
                                "symbol": symbol,
                                "direction": sig.direction,
                                "tracked_from": "watchlist",
                            })
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
                        save_candidate_signal({
                            "signal_id": signal_id,
                            "symbol": symbol,
                            "direction": trend_result["direction"],
                            "trend_score": trend_result["score"],
                            "trigger_score": trigger_result["score"],
                            "risk_score": risk_result.get("score", 0),
                            "quality": trigger_result["quality"],
                            "reject_reason": risk_result.get("risk_reject_reason", "risk_guard_failed"),
                            "risk_reject_reason": risk_result.get("risk_reject_reason", "risk_guard_failed"),
                            "lifecycle_stage": "REJECTED",
                            "execution_status": "rejected",
                        })
                        save_signal_event(signal_id, "REJECTED", symbol=symbol, reject_reason=risk_result.get("risk_reject_reason", "risk_guard_failed"), reason="risk_engine_reject")
                        continue
                    save_signal_event(signal_id, "RISK_CHECKED", symbol=symbol, reason="risk_pass")

                    # ── ADIM 5: DATA LAYER — SİNYAL OLUŞTUR ───────────────
                    sig = data_layer.get_signal(signal_id)
                    sig.direction = trend_result["direction"]
                    sig.coin_score = coin_info["tradeability_score"]
                    sig.trend_score = trend_result["score"]
                    sig.trigger_score = trigger_result["score"]
                    sig.risk_score = risk_result["score"]
                    sig.setup_quality = trigger_result["quality"]
                    sig.ml_score = trigger_result.get("ml_score", 50)
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
                    save_signal_event(sig.id, "AI_CHECKED", symbol=symbol, reason=decision["reason"])

                    candidate_payload = {
                        "signal_id": sig.id,
                        "symbol": sig.symbol,
                        "direction": sig.direction,
                        "trend_score": sig.trend_score,
                        "trigger_score": sig.trigger_score,
                        "risk_score": sig.risk_score,
                        "ai_score": decision.get("ai_score", sig.final_score),
                        "final_score": sig.final_score,
                        "quality": sig.setup_quality,
                        "reason": sig.reason,
                        "lifecycle_stage": "AI_CHECKED",
                        "entry": sig.entry_zone,
                        "stop": sig.stop_loss,
                        "tp1": sig.tp1,
                        "tp2": sig.tp2,
                        "tp3": sig.tp3,
                        "rr": sig.rr,
                        "atr": risk_result.get("atr", 0),
                        "stop_distance_percent": risk_result.get("stop_distance_percent", 0),
                        "estimated_fee": risk_result.get("estimated_fee", 0),
                        "estimated_slippage": risk_result.get("estimated_slippage", 0),
                        "net_rr": risk_result.get("net_rr", sig.rr),
                        "position_size": sig.position_size,
                        "notional": sig.notional_size,
                        "leverage_suggestion": sig.leverage_suggestion,
                        "risk_amount": risk_result.get("risk_amount", sig.max_loss),
                        "max_loss": sig.max_loss,
                        "execution_status": "candidate",
                    }
                    candidate_id = save_candidate_signal(candidate_payload)
                    sig.candidate_id = candidate_id

                    if sig.final_score < DATA_THRESHOLD:
                        sig.status = "vetoed"
                        sig.reject_reason = "low_confidence"
                        sig.telegram_status = "skip"
                        update_candidate_status(candidate_id, reject_reason="low_confidence", lifecycle_stage="REJECTED", execution_status="rejected")
                        save_signal_event(sig.id, "REJECTED", symbol=symbol, reject_reason="low_confidence", reason="below_data_threshold")
                        save_scalp_signal(sig.to_dict())
                        continue

                    if decision["decision"] == "VETO":
                        sig.status = "vetoed"
                        sig.ai_veto_reason = decision["reason"]
                        sig.reject_reason = "ai_veto"
                        sig.telegram_status = "skip"
                        update_candidate_status(candidate_id, ai_veto_reason=decision["reason"], reject_reason="ai_veto", lifecycle_stage="REJECTED", execution_status="rejected")
                        save_signal_event(sig.id, "REJECTED", symbol=symbol, reject_reason="ai_veto", reason=decision["reason"])
                        save_scalp_signal(sig.to_dict())
                        logger.info(
                            f"[VETO] {symbol} {sig.direction} | {decision['reason']} | "
                            f"score={sig.final_score}"
                        )
                        save_paper_result({
                            "signal_id": sig.id,
                            "candidate_id": candidate_id,
                            "symbol": symbol,
                            "direction": sig.direction,
                            "tracked_from": "candidate",
                        })
                        continue

                    sig.status = "approved"
                    update_candidate_status(candidate_id, lifecycle_stage="APPROVED_FOR_WATCHLIST")
                    save_signal_event(sig.id, "APPROVED_FOR_WATCHLIST", symbol=symbol, reason="watchlist_threshold_pass")

                    # ── ADIM 7: DATA LAYER'A KAYDET ────────────────────────
                    save_scalp_signal(sig.to_dict())

                    # ── ADIM 8: TELEGRAM DELIVERY ──────────────────────────
                    if sig.final_score >= TELEGRAM_THRESHOLD:
                        sig.telegram_status = "queued"
                        update_candidate_status(candidate_id, lifecycle_stage="APPROVED_FOR_TELEGRAM")
                        save_signal_event(sig.id, "APPROVED_FOR_TELEGRAM", symbol=symbol, reason="telegram_threshold_pass")
                        deliver_signal(sig)
                    else:
                        sig.telegram_status = "skip"

                    # ── ADIM 9: TRADE AÇMA (execute modunda) ──────────────
                    if AX_MODE == "execute" and EXECUTION_AVAILABLE and sig.final_score >= TRADE_THRESHOLD:
                        if sig.rr < MIN_RR:
                            update_candidate_status(candidate_id, reject_reason="bad_rr", lifecycle_stage="REJECTED", execution_status="rejected")
                            save_signal_event(sig.id, "REJECTED", symbol=symbol, reject_reason="bad_rr", reason="trade_guard_min_rr")
                            continue
                        if sig.leverage_suggestion > MAX_LEVERAGE:
                            update_candidate_status(candidate_id, reject_reason="risk_guard_failed", lifecycle_stage="REJECTED", execution_status="rejected")
                            save_signal_event(sig.id, "REJECTED", symbol=symbol, reject_reason="risk_guard_failed", reason="trade_guard_max_leverage")
                            continue
                        if EXECUTION_MODE == "live" and not LIVE_CONFIRM:
                            update_candidate_status(candidate_id, reject_reason="risk_guard_failed", lifecycle_stage="REJECTED", execution_status="rejected")
                            save_signal_event(sig.id, "REJECTED", symbol=symbol, reject_reason="risk_guard_failed", reason="live_confirm_required")
                            continue
                        update_candidate_status(candidate_id, lifecycle_stage="APPROVED_FOR_TRADE")
                        save_signal_event(sig.id, "APPROVED_FOR_TRADE", symbol=symbol, reason="trade_threshold_pass")
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
                            "candidate_id": candidate_id,
                        }
                        ax_result = {
                            "decision": "ALLOW",
                            "score": sig.final_score,
                            "confidence": sig.confidence,
                            "veto_reason": None,
                        }
                        trade_id = open_trade(client, legacy_signal, ax_result)
                        if trade_id:
                            save_signal_event(sig.id, "OPENED", symbol=symbol, reason=f"trade_id={trade_id}")
                            update_candidate_status(candidate_id, lifecycle_stage="OPENED", execution_status="opened")
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
