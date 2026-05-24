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
    DB_PATH,
    DAILY_MAX_LOSS_PCT,
    DATA_THRESHOLD, WATCHLIST_THRESHOLD, TELEGRAM_THRESHOLD, TRADE_THRESHOLD,
    LIVE_CONFIRM, MAX_LEVERAGE, MIN_RR, EXECUTABLE_QUALITIES,
    MAX_CORRELATED_TRADES,
    SCAN_INCLUDE_WATCH, WATCHLIST_MIN_SCAN_SCORE, MAX_COINS_PER_SCAN_LOOP,
    MAX_PORTFOLIO_EXPOSURE_PCT,
    PAPER_TRACK_REJECTED_CANDIDATES,
    PAPER_TRACK_WATCHLIST,
    PAPER_TRACK_TELEGRAM_GAPS,
    PAPER_TRACK_HORIZON_HOURS,
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
from websocket_events import event_manager

try:
    from core.ghost_learning import maybe_ghost_log as _ghost_log
except Exception:
    def _ghost_log(signal, reason): pass  # type: ignore[misc]

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
# SCAN + RİSK YARDIMCILARI
# ─────────────────────────────────────────────────────────────────────────────


def build_eligible_scan_subset(candidates: list) -> list:
    eligible = []
    for c in candidates:
        status = c.get("status")
        ts = float(c.get("tradeability_score") or 0)
        if status == "Eligible":
            eligible.append(c)
        elif SCAN_INCLUDE_WATCH and status == "Watch" and ts >= WATCHLIST_MIN_SCAN_SCORE:
            eligible.append(c)
    eligible.sort(key=lambda x: float(x.get("tradeability_score") or 0), reverse=True)
    return eligible[:MAX_COINS_PER_SCAN_LOOP]


def correlation_blocks(open_trades: list, direction: str) -> bool:
    if not direction or direction == "NO TRADE":
        return False
    # DB has 'side', not 'direction'
    n = sum(1 for t in open_trades
            if (t.get("direction") or t.get("side", "")) == direction)
    return n >= MAX_CORRELATED_TRADES


def exposure_blocks(open_trades: list, extra_notional: float, balance: float) -> bool:
    # DB has 'quantity'/'notional', not 'qty'/'entry'
    expo = sum(
        float(t.get("notional") or
              (t.get("quantity") or t.get("qty") or 0) * (t.get("entry_price") or t.get("entry") or 0))
        for t in open_trades
    )
    cap = balance * MAX_PORTFOLIO_EXPOSURE_PCT / 100.0
    return expo + max(0.0, extra_notional or 0) > cap + 1e-8


def _paper_horizon_minutes() -> float:
    return PAPER_TRACK_HORIZON_HOURS * 60.0


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
    # ── Duplicate process koruması ─────────────────────────────────────
    import fcntl as _fcntl
    _lock_file = open("/tmp/aurvex_bot.lock", "w")
    try:
        _fcntl.flock(_lock_file, _fcntl.LOCK_EX | _fcntl.LOCK_NB)
    except BlockingIOError:
        logger.error("HATA: scalp_bot zaten çalışıyor! Çift process engellendi.")
        import sys; sys.exit(1)
    # ─────────────────────────────────────────────────────────────────
    logger.info("=== AX Scalp Engine v2.0 başlatılıyor ===")

    # DB ve hesap başlat
    init_db()
    init_paper_account()

    # Binance client — paper modda API key opsiyonel
    try:
        client = Client(
            BINANCE_API_KEY or "",
            BINANCE_API_SECRET or "",
        )
        client.ping()
        logger.info("✅ Binance bağlantısı OK")
    except Exception as _be:
        logger.warning(
            f"⚠️ Binance bağlantısı başarısız: {_be} — "
            f"Public endpoint'ler kullanılacak (paper mode)"
        )
        client = Client("", "")
    if AI_BRAIN_AVAILABLE:
        ai_set_client(client, send_message)

    # Modülleri başlat
    scanner  = MarketScanner(client, db_path=DB_PATH)
    trend    = TrendEngine(client)
    trigger  = TriggerEngine(client)
    risk     = RiskEngine(client, db_path=DB_PATH)
    ai_engine = AIDecisionEngine(db_path=DB_PATH)

    # Ghost learning — bağımsız thread (ana bot döngüsünden bağımsız)
    _ghost_stop = threading.Event()

    def _ghost_worker(binance_client, stop_event):
        logger.info("[Ghost] Worker thread başladı")
        while not stop_event.is_set():
            try:
                from core.ghost_learning import process_pending_results
                process_pending_results(binance_client)
            except Exception as _ge:
                logger.warning(f"[Ghost] Worker hata: {_ge}")
            stop_event.wait(timeout=120)   # 2 dakikada bir çalış
        logger.info("[Ghost] Worker thread durdu")

    _ghost_thread = threading.Thread(
        target=_ghost_worker,
        args=(client, _ghost_stop),
        daemon=True,
        name="ghost-worker",
    )
    _ghost_thread.start()

    # Telegram Manager (komut dinleyici)
    tg_manager = None
    if TG_MANAGER_AVAILABLE:
        try:
            tg_manager = TelegramManager(send_message)
            tg_manager.start()
        except Exception as e:
            logger.warning(f"TelegramManager başlatılamadı: {e}")

    _last_ai_adapt    = 0   # AI Brain periyodik adaptasyon (30 dakikada bir)
    _last_nightly_day = ""  # Nightly optimizer son çalışma günü (YYYY-MM-DD)
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

            # Günlük kayıp limiti — circuit breaker hard stop
            try:
                from database import get_conn
                today_str = datetime.now(timezone.utc).strftime("%Y-%m-%d")
                with get_conn() as _conn:
                    _row = _conn.execute(
                        "SELECT COALESCE(SUM(net_pnl),0) FROM trades WHERE DATE(close_time)=? AND status='closed'",
                        (today_str,)
                    ).fetchone()
                today_loss = float(_row[0] or 0.0)
                balance = get_paper_balance()
                max_loss_abs = balance * (DAILY_MAX_LOSS_PCT / 100.0)
                if today_loss < -max_loss_abs:
                    logger.warning(
                        f"[CIRCUIT BREAKER] Günlük kayıp limiti aşıldı: {today_loss:.2f}$ "
                        f"(limit: -{max_loss_abs:.2f}$). Scan durduruldu — 5dk bekleniyor."
                    )
                    time.sleep(300)
                    continue
            except Exception as _e:
                logger.warning(f"Günlük kayıp kontrolü hatası: {_e}")

            # Günlük sinyal sayısı — hard limit kontrolü
            daily_counts = get_daily_signal_count()
            _daily_total = daily_counts.get("total", 0) if isinstance(daily_counts, dict) else int(daily_counts)
            from config import DAILY_SIGNAL_LIMIT
            if _daily_total >= DAILY_SIGNAL_LIMIT:
                logger.info(f"[LIMIT] Günlük sinyal limiti doldu ({_daily_total}/{DAILY_SIGNAL_LIMIT}), scan atlandı")
                time.sleep(60)
                continue
            if _daily_total % 10 == 0 and _daily_total > 0:
                logger.debug(f"Günlük sinyal sayısı: {_daily_total}/{DAILY_SIGNAL_LIMIT}")

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

            try:
                from core.paper_tracker import process_pending_paper_results

                done_p = process_pending_paper_results(client, limit=30)
                if done_p:
                    logger.info(f"[paper_tracker] finalize {done_p} satır tamamlandı")
                    # Ghost sonuçlarına göre TRADE_THRESHOLD'u adaptif güncelle
                    try:
                        ai_engine._update_threshold_from_ghost()
                    except Exception as _tge:
                        logger.debug(f"[AI] threshold update atlandı: {_tge}")
            except Exception as _pte:
                logger.debug(f"[paper_tracker] atlandı: {_pte}")

            # Diagnostics sayacı
            try:
                from core.signal_diagnostics import record_scan, log_hourly
                record_scan()
                log_hourly()
            except Exception:
                pass

            # ── ADIM 1: MARKET SCANNER ─────────────────────────────────────
            candidates = scanner.scan()
            if not candidates:
                logger.debug("Market scanner: aday bulunamadı")
                time.sleep(SCAN_INTERVAL)
                continue

            eligible = build_eligible_scan_subset(candidates)
            logger.info(
                f"Scanner: {len(eligible)} coin işlenecek "
                f"(Eligible + Watch≥{WATCHLIST_MIN_SCAN_SCORE if SCAN_INCLUDE_WATCH else 'OFF'})"
            )

            # Eski sinyalleri arşivle
            archive_old_scalp_signals(hours=24)

            for coin_info in eligible:
                symbol = coin_info["symbol"]

                if symbol in open_symbols:
                    continue

                try:
                    # ── ADIM 2: TREND ENGINE ───────────────────────────────
                    signal_id = data_layer.create_signal(symbol).id
                    save_signal_event(signal_id, "SCANNED", symbol=symbol, reason="scanner_pass")
                    trend_result = trend.analyze(symbol)
                    if trend_result["direction"] == "NO TRADE":
                        save_signal_event(signal_id, "REJECTED", symbol=symbol, reject_reason="weak_trend", reason="trend_engine_no_trade")
                        if event_manager: event_manager.broadcast_signal_rejected(symbol, trend_result["direction"], "weak_trend")
                        continue
                    save_signal_event(signal_id, "TREND_CHECKED", symbol=symbol, reason="trend_pass")

                    # ── ADIM 3: TRIGGER ENGINE ─────────────────────────────
                    trigger_result = trigger.analyze(
                        symbol,
                        trend_result["direction"],
                        trend_result.get("btc_trend", "NEUTRAL"),
                        trend_confluence=trend_result.get("confluence_raw", 1),
                    )
                    if trigger_result["quality"] == "D":
                        save_signal_event(signal_id, "REJECTED", symbol=symbol, reject_reason="weak_trigger", reason="trigger_quality_d")
                        if event_manager: event_manager.broadcast_signal_rejected(symbol, trend_result["direction"], "weak_trigger")
                        continue
                    save_signal_event(signal_id, "TRIGGER_CHECKED", symbol=symbol, reason="trigger_pass")

                    # C kalitesi sadece dashboard'da izlenir
                    if trigger_result["quality"] == "C":
                        # Watchlist katmanı — tek signal kaynağı kullan (çift orphan yok)
                        sig = data_layer.get_signal(signal_id)
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
                            "final_score": round(float(trigger_result.get("score", 0) * 10), 1),
                            "quality": "C",
                            "reason": "watchlist_only_candidate",
                            "execution_status": "candidate",
                            "lifecycle_stage": "APPROVED_FOR_WATCHLIST",
                            "entry": trigger_result["entry"],
                        })
                        save_signal_event(sig.id, "APPROVED_FOR_WATCHLIST", symbol=symbol, reason="quality_c_watchlist")
                        if PAPER_TRACK_WATCHLIST:
                            prv_c = risk.preview_for_paper(symbol, trend_result["direction"], trigger_result["entry"], balance)
                            if prv_c.get("valid"):
                                save_paper_result({
                                    "signal_id": sig.id,
                                    "candidate_id": candidate_id,
                                    "symbol": symbol,
                                    "direction": sig.direction,
                                    "tracked_from": "watchlist",
                                    "final_score_snap": sig.coin_score,
                                    "horizon_minutes": _paper_horizon_minutes(),
                                    "preview_entry": trigger_result["entry"],
                                    "preview_sl": prv_c["sl"],
                                    "preview_tp1": prv_c["tp1"],
                                    "preview_tp2": prv_c["tp2"],
                                    "preview_tp3": prv_c["tp3"],
                                    "leverage_hint": int(prv_c.get("leverage") or 10),
                                })
                        continue

                    # ── ADIM 3.5: COIN PERSONALITY — Per-coin parametreler ──
                    try:
                        from core.coin_personality import CoinPersonalityEngine
                        _cp = CoinPersonalityEngine(db_path=DB_PATH)
                        _personality = _cp.analyze_personality(symbol)
                        _adaptive = _cp.get_adaptive_params(symbol)
                        # Coin personality'yi signal event'e kaydet
                        _pname = _personality.get("personality", "unknown")
                        _traits = _personality.get("traits", [])
                        if _pname != "unknown":
                            save_signal_event(
                                signal_id, "COIN_PERSONALITY",
                                symbol=symbol,
                                reason=f"{_pname}|traits={','.join(_traits)}"
                            )
                            logger.debug(
                                f"[CoinPersonality] {symbol}: {_pname} "
                                f"sl_mult={_adaptive['sl_atr_mult']:.1f} "
                                f"risk={_adaptive['risk_pct']:.1f}%"
                            )
                        # Trigger score'una personality boost ekle
                        if _pname == "The Runner" and trigger_result["quality"] in ("A", "A+", "S"):
                            # Runner coinlerde strong trend sinyali = bonus
                            trigger_result = dict(trigger_result)
                            trigger_result["score"] = min(10.0, trigger_result["score"] + 0.5)
                        elif _pname == "The Faker" and trigger_result["quality"] == "S":
                            # Faker coinlerde S sinyali olsa bile dikkatli ol
                            trigger_result = dict(trigger_result)
                            trigger_result["score"] = max(0.0, trigger_result["score"] - 0.3)
                    except Exception as _cp_err:
                        logger.debug(f"[CoinPersonality] skip: {_cp_err}")
                    # ────────────────────────────────────────────────────────

                    # ── ADIM 4: RISK ENGINE ────────────────────────────────
                    balance = get_paper_balance()
                    risk_result = risk.calculate(
                        symbol, trend_result["direction"],
                        trigger_result["entry"],
                        trigger_result["quality"],
                        balance
                    )
                    if not risk_result.get("valid"):
                        rk_id = save_candidate_signal({
                            "signal_id": signal_id,
                            "symbol": symbol,
                            "direction": trend_result["direction"],
                            "trend_score": trend_result["score"],
                            "trigger_score": trigger_result["score"],
                            "risk_score": risk_result.get("score", 0),
                            "final_score": round(float(trigger_result.get("score", 0) * 10), 1),
                            "quality": trigger_result["quality"],
                            "reject_reason": risk_result.get("risk_reject_reason", "risk_guard_failed"),
                            "risk_reject_reason": risk_result.get("risk_reject_reason", "risk_guard_failed"),
                            "lifecycle_stage": "REJECTED",
                            "execution_status": "rejected",
                            "entry": trigger_result["entry"],
                        })
                        save_signal_event(signal_id, "REJECTED", symbol=symbol, reject_reason=risk_result.get("risk_reject_reason", "risk_guard_failed"), reason="risk_engine_reject")
                        if event_manager: event_manager.broadcast_signal_rejected(symbol, trend_result["direction"], risk_result.get("risk_reject_reason", "risk_guard_failed"))
                        if PAPER_TRACK_REJECTED_CANDIDATES:
                            prv = risk.preview_for_paper(symbol, trend_result["direction"], trigger_result["entry"], balance)
                            if prv.get("valid"):
                                save_paper_result({
                                    "signal_id": signal_id,
                                    "candidate_id": rk_id,
                                    "symbol": symbol,
                                    "direction": trend_result["direction"],
                                    "tracked_from": "candidate",
                                    "reject_reason_snap": risk_result.get("risk_reject_reason", "risk_guard_failed"),
                                    "horizon_minutes": _paper_horizon_minutes(),
                                    "preview_entry": trigger_result["entry"],
                                    "preview_sl": prv["sl"],
                                    "preview_tp1": prv["tp1"],
                                    "preview_tp2": prv["tp2"],
                                    "preview_tp3": prv["tp3"],
                                    "leverage_hint": int(prv.get("leverage") or 10),
                                })
                                _ghost_log({
                                    "symbol": symbol, "direction": trend_result["direction"],
                                    "entry": trigger_result["entry"],
                                    "sl": prv["sl"], "tp1": prv["tp1"],
                                    "confidence": trigger_result["score"] / 10,
                                    "quality": trigger_result["quality"],
                                }, reason=risk_result.get("risk_reject_reason", "risk_guard_failed"))
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
                    sig.confluence_score = trigger_result.get("confluence_total", 2)
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
                    if event_manager: event_manager.broadcast_signal_generated(sig.symbol, sig.direction, sig.setup_quality, sig.final_score)

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
                        if event_manager: event_manager.broadcast_signal_rejected(symbol, sig.direction, "low_confidence")
                        save_scalp_signal(sig.to_dict())
                        if PAPER_TRACK_REJECTED_CANDIDATES:
                            save_paper_result({
                                "signal_id": sig.id,
                                "candidate_id": candidate_id,
                                "symbol": symbol,
                                "direction": sig.direction,
                                "tracked_from": "candidate",
                                "reject_reason_snap": "below_data_threshold",
                                "final_score_snap": sig.final_score,
                                "horizon_minutes": _paper_horizon_minutes(),
                                "preview_entry": sig.entry_zone,
                                "preview_sl": sig.stop_loss,
                                "preview_tp1": sig.tp1,
                                "preview_tp2": sig.tp2,
                                "preview_tp3": sig.tp3,
                                "leverage_hint": int(sig.leverage_suggestion or 10),
                            })
                            _ghost_log({
                                "symbol": symbol, "direction": sig.direction,
                                "entry": sig.entry_zone,
                                "sl": sig.stop_loss, "tp1": sig.tp1,
                                "confidence": sig.final_score / 100,
                                "quality": sig.setup_quality,
                            }, reason="below_data_threshold")
                        continue

                    if decision["decision"] == "VETO":
                        sig.status = "vetoed"
                        sig.ai_veto_reason = decision["reason"]
                        sig.reject_reason = "ai_veto"
                        sig.telegram_status = "skip"
                        update_candidate_status(candidate_id, ai_veto_reason=decision["reason"], reject_reason="ai_veto", lifecycle_stage="REJECTED", execution_status="rejected")
                        save_signal_event(sig.id, "REJECTED", symbol=symbol, reject_reason="ai_veto", reason=decision["reason"])
                        if event_manager: event_manager.broadcast_signal_rejected(symbol, sig.direction, "ai_veto")
                        save_scalp_signal(sig.to_dict())
                        logger.info(
                            f"[VETO] {symbol} {sig.direction} | {decision['reason']} | "
                            f"score={sig.final_score}"
                        )
                        if PAPER_TRACK_REJECTED_CANDIDATES:
                            save_paper_result({
                                "signal_id": sig.id,
                                "candidate_id": candidate_id,
                                "symbol": symbol,
                                "direction": sig.direction,
                                "tracked_from": "candidate",
                                "reject_reason_snap": sig.ai_veto_reason or decision.get("reason"),
                                "final_score_snap": sig.final_score,
                                "horizon_minutes": _paper_horizon_minutes(),
                                "preview_entry": sig.entry_zone,
                                "preview_sl": sig.stop_loss,
                                "preview_tp1": sig.tp1,
                                "preview_tp2": sig.tp2,
                                "preview_tp3": sig.tp3,
                                "leverage_hint": int(sig.leverage_suggestion or 10),
                            })
                        continue

                    sig.status = "approved"
                    update_candidate_status(candidate_id, lifecycle_stage="APPROVED_FOR_WATCHLIST")
                    save_signal_event(sig.id, "APPROVED_FOR_WATCHLIST", symbol=symbol, reason="watchlist_threshold_pass")

                    # ── ADIM 7: DATA LAYER'A KAYDET ────────────────────────
                    save_scalp_signal(sig.to_dict())

                    # ── ADIM 8: TELEGRAM DELIVERY ──────────────────────────
                    telegram_sent_ok = False
                    if sig.final_score >= TELEGRAM_THRESHOLD:
                        sig.telegram_status = "queued"
                        update_candidate_status(candidate_id, lifecycle_stage="APPROVED_FOR_TELEGRAM")
                        save_signal_event(sig.id, "APPROVED_FOR_TELEGRAM", symbol=symbol, reason="telegram_threshold_pass")
                        telegram_sent_ok = bool(deliver_signal(sig))
                        if telegram_sent_ok:
                            sig.telegram_status = "sent"
                    else:
                        sig.telegram_status = "skip"

                    if (
                        PAPER_TRACK_TELEGRAM_GAPS
                        and telegram_sent_ok
                        and sig.final_score < TRADE_THRESHOLD
                    ):
                        save_paper_result({
                            "signal_id": sig.id,
                            "candidate_id": candidate_id,
                            "symbol": symbol,
                            "direction": sig.direction,
                            "tracked_from": "telegram_gap",
                            "final_score_snap": sig.final_score,
                            "horizon_minutes": _paper_horizon_minutes(),
                            "preview_entry": sig.entry_zone,
                            "preview_sl": sig.stop_loss,
                            "preview_tp1": sig.tp1,
                            "preview_tp2": sig.tp2,
                            "preview_tp3": sig.tp3,
                            "leverage_hint": int(sig.leverage_suggestion or 10),
                        })

                    save_scalp_signal(sig.to_dict())

                    # ── ADIM 9: TRADE AÇMA (execute modunda) ──────────────
                    if (AX_MODE == "execute" and EXECUTION_AVAILABLE
                            and sig.final_score >= TRADE_THRESHOLD
                            and sig.setup_quality in EXECUTABLE_QUALITIES):  # B kalite execute edilmez
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
                        bal_trade = get_paper_balance()
                        if correlation_blocks(open_trades_now, sig.direction):
                            update_candidate_status(
                                candidate_id,
                                reject_reason="correlation_risk",
                                lifecycle_stage="REJECTED",
                                execution_status="rejected",
                            )
                            save_signal_event(
                                sig.id,
                                "REJECTED",
                                symbol=symbol,
                                reject_reason="correlation_risk",
                                reason="max_correlated_positions",
                            )
                            logger.info(f"[risk] Korrelasyon filtresi: {symbol} {sig.direction}")
                            continue
                        if exposure_blocks(open_trades_now, float(sig.notional_size or 0), bal_trade):
                            update_candidate_status(
                                candidate_id,
                                reject_reason="max_portfolio_exposure",
                                lifecycle_stage="REJECTED",
                                execution_status="rejected",
                            )
                            save_signal_event(
                                sig.id,
                                "REJECTED",
                                symbol=symbol,
                                reject_reason="max_portfolio_exposure",
                                reason=f"cap_{MAX_PORTFOLIO_EXPOSURE_PCT}pct",
                            )
                            logger.info(f"[risk] Exposure limiti: {symbol} notional≈{sig.notional_size}")
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
                            "leverage": int(sig.leverage_suggestion or 10),
                            # ML training features — live_tracker.record_close() okur
                            "adx":              trigger_result.get("adx", 0),
                            "rv":               trigger_result.get("rv", 0),
                            "rsi5":             trigger_result.get("rsi5", 50),
                            "rsi1":             trigger_result.get("rsi1", 50),
                            "btc_trend":        trend_result.get("btc_trend", "NEUTRAL"),
                            "bb_width_pct":     trend_result.get("bb_width", 0),
                            "bb_width_chg":     trend_result.get("bb_width_chg", 0),
                            "momentum_3c":      trigger_result.get("momentum_3c", 0),
                            "funding_favorable": 1,
                            "ml_score":         trigger_result.get("ml_score", 50),
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

            try:
                set_state("heartbeat", datetime.now(timezone.utc).isoformat())
                set_state("status", "running")
            except Exception:
                pass
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

            # ── Nightly Per-Coin Optimizer (03:00 UTC) ──────────────────────
            if AI_BRAIN_AVAILABLE:
                _utc_now = datetime.now(timezone.utc)
                _today   = _utc_now.strftime("%Y-%m-%d")
                if _utc_now.hour == 3 and _today != _last_nightly_day:
                    _last_nightly_day = _today
                    def _run_nightly():
                        try:
                            from ai_brain import nightly_optimize_coins
                            nightly_optimize_coins(tg_fn=send_message)
                        except Exception as _ne:
                            logger.warning(f"[NightlyOpt] Hata: {_ne}")
                    threading.Thread(target=_run_nightly, daemon=True).start()
                    logger.info("[NightlyOpt] Per-coin optimizer baslatildi (%s).", _today)

        except KeyboardInterrupt:
            logger.info("Bot durduruldu (KeyboardInterrupt).")
            send_message("⛔ AX Scalp Engine durduruldu.")
            break
        except Exception as e:
            logger.error(f"Ana döngü hatası: {e}", exc_info=True)
            try:
                set_state("status", "error")
                set_state("last_error", str(e)[:500])
            except Exception:
                pass
            time.sleep(10)

# ─────────────────────────────────────────────────────────────────────────────
# GİRİŞ NOKTASI
# ─────────────────────────────────────────────────────────────────────────────
if __name__ == "__main__":
    main()
