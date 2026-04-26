"""
scalp_bot.py — AX Ana Döngü
============================
10 adımlı ana loop:
  1  scan         — market_scan: aktif coinleri filtrele
  2  signal       — signal_engine: teknik analiz + seviyeler
  3  db_save      — DB'ye signal_candidate kaydet
  4  ax_score     — ai_brain.evaluate_signal: ALLOW/VETO/WATCH
  5  decision     — VETO veya WATCH ise atla
  6  risk_check   — ek risk koruması (bakiye, circuit_breaker)
  7  execution    — execution_engine.open_trade: paper trade aç
  8  track        — execution_engine.monitor_trades: SL/TP takip
  9  close        — execution_engine kendi içinde kapatır
  10 post         — ai_brain.post_trade_analysis + coin_library güncelle
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
    HEARTBEAT_INTERVAL,
    EXEC_MIN_VOLUME_M, EXEC_CONFIDENCE_MIN, EXEC_SCORE_MIN,
    EXEC_MIN_RR, EXEC_MIN_EXPECTED_MFE_R,
    DEBUG_SIGNAL_MODE,
)
from database import (
    init_db, init_paper_account, get_paper_balance,
    save_signal_candidate, update_signal_decision,
    get_open_trades, get_trades, get_stats,
    set_state, get_state,
    save_daily_summary, save_coin_market_memory,
    set_coin_cooldown,
    save_pipeline_stats, save_candidate_outcome,
    get_pending_outcomes, update_candidate_outcome,
)
from coin_library import init_coin_library, update_coin_stats, get_coin_params
from market_scan import scan, get_current_session, get_market_regime
from signal_engine import generate_signal
from ai_brain import evaluate_signal, analyze_and_adapt, post_trade_analysis, set_client as ai_set_client
from execution_engine import open_trade, monitor_trades as exec_monitor

try:
    from telegram_manager import TelegramManager
    TG_AVAILABLE = True
except ImportError:
    TG_AVAILABLE = False

try:
    from n8n_bridge import register_bot, notify_trade_open, notify_trade_close, notify_alert
    N8N_AVAILABLE = True
except ImportError:
    N8N_AVAILABLE = False
    def register_bot(**_): pass
    def notify_trade_open(*_, **__): pass
    def notify_trade_close(*_, **__): pass
    def notify_alert(*_, **__): pass


# ─────────────────────────────────────────────────────────────────────────────
# LOGGING
# ─────────────────────────────────────────────────────────────────────────────

os.makedirs(LOG_DIR, exist_ok=True)

logging.basicConfig(
    level=logging.DEBUG if DEBUG_SIGNAL_MODE else logging.INFO,
    format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
    handlers=[
        logging.StreamHandler(),
        logging.handlers.RotatingFileHandler(
            os.path.join(LOG_DIR, "ax_bot.log"),
            maxBytes=LOG_MAX_MB * 1024 * 1024,
            backupCount=LOG_MAX_DAYS,
            encoding="utf-8",
        ),
    ]
)
logger = logging.getLogger("scalp_bot")


# ─────────────────────────────────────────────────────────────────────────────
# GLOBAL DURUM
# ─────────────────────────────────────────────────────────────────────────────

consecutive_losses: int = 0
circuit_breaker_until: datetime | None = None
closed_trade_counter: int = 0
_ai_brain_counter: int = 0
AI_BRAIN_EVERY = 10   # Her N kapanışta bir AI brain raporu

# Pipeline stats — her scan döngüsünde sıfırlanır
_pipeline: dict = {
    "scanned_symbols": 0,
    "passed_market_filter": 0,
    "candidates_created": 0,
    "ax_allow": 0,
    "ax_veto": 0,
    "ax_watch": 0,
    "risk_rejected": 0,
    "paper_trades_opened": 0,
    "session": None,
    "market_regime": None,
    "last_veto_reason": None,
    "last_allow_symbol": None,
}

# Son scan zamanı (Telegram /scan için)
_last_scan_summary: dict = {}
_last_scan_time: str = ""


# ─────────────────────────────────────────────────────────────────────────────
# CIRCUIT BREAKER
# ─────────────────────────────────────────────────────────────────────────────

def is_circuit_breaker_active() -> tuple:
    global circuit_breaker_until
    if circuit_breaker_until and datetime.now(timezone.utc) < circuit_breaker_until:
        remaining = int((circuit_breaker_until - datetime.now(timezone.utc)).total_seconds() / 60)
        return True, remaining
    return False, 0

def trigger_circuit_breaker(tg_manager=None):
    global circuit_breaker_until, consecutive_losses
    circuit_breaker_until = datetime.now(timezone.utc) + timedelta(minutes=CIRCUIT_BREAKER_MINUTES)
    set_state("circuit_breaker_until", circuit_breaker_until.isoformat())
    logger.warning(f"⛔ DEVRE KESİCİ — {CIRCUIT_BREAKER_MINUTES}dk")
    if tg_manager:
        tg_manager.send(
            f"⛔ <b>Devre Kesici Aktif</b>\n"
            f"{CIRCUIT_BREAKER_LOSSES} ardışık kayıp.\n"
            f"Bot {CIRCUIT_BREAKER_MINUTES} dk duraklatıldı.\n"
            f"Devam: <code>{circuit_breaker_until.strftime('%H:%M UTC')}</code>"
        )
    notify_alert("circuit_breaker",
                 f"{CIRCUIT_BREAKER_LOSSES} ardışık kayıp — {CIRCUIT_BREAKER_MINUTES}dk duraklama")


# ─────────────────────────────────────────────────────────────────────────────
# TELEGRAM GÖNDERİM
# ─────────────────────────────────────────────────────────────────────────────

def tg_send(tg_manager, msg: str):
    if tg_manager:
        tg_manager.send(msg)


# ─────────────────────────────────────────────────────────────────────────────
# TRADE SONRASI İŞLEMLER
# ─────────────────────────────────────────────────────────────────────────────

def on_trade_closed(client, tg_manager, trade: dict):
    """Trade kapandığında: istatistik güncelle, AI brain tetikle."""
    global consecutive_losses, closed_trade_counter, _ai_brain_counter

    symbol    = trade["symbol"]
    direction = trade["direction"]
    net_pnl   = trade.get("net_pnl", 0) or 0
    result    = "WIN" if net_pnl > 0 else "LOSS"
    hold_min  = trade.get("hold_minutes", 0) or 0
    r_mult    = trade.get("r_multiple", 0) or 0
    session   = get_current_session()

    # Ardışık kayıp takibi
    if result == "LOSS":
        consecutive_losses += 1
    else:
        consecutive_losses = 0

    closed_trade_counter += 1
    _ai_brain_counter    += 1

    # Coin library güncelle
    update_coin_stats(
        symbol, result, net_pnl,
        r_multiple=r_mult, session=session, direction=direction
    )

    # Piyasa hafızası
    regime = get_market_regime(client)
    save_coin_market_memory(symbol, session, regime, direction, result, r_mult)

    # Cooldown: ardışık kayıpta coin'i geçici olarak beklet
    if result == "LOSS":
        coin_p = get_coin_params(symbol)
        if coin_p.get("loss_count", 0) % 3 == 0:  # Her 3 kayıpta cooldown
            set_coin_cooldown(symbol, minutes=120, reason="consecutive_loss")

    # Circuit breaker
    if consecutive_losses >= CIRCUIT_BREAKER_LOSSES:
        trigger_circuit_breaker(tg_manager)
        consecutive_losses = 0

    # Postmortem (arka planda)
    threading.Thread(
        target=post_trade_analysis,
        args=(trade["id"],),
        kwargs={"client_ref": client, "tg_fn": lambda m: tg_send(tg_manager, m)},
        daemon=True
    ).start()

    # AI Brain raporu — her N kapanışta
    if _ai_brain_counter >= AI_BRAIN_EVERY:
        _ai_brain_counter = 0
        threading.Thread(target=_run_ai_brain, args=(tg_manager,), daemon=True).start()

    # n8n bildirimi
    notify_trade_close(
        symbol=symbol, direction=direction, result=result,
        net_pnl=net_pnl, exit_price=trade.get("close_price", 0),
        hold_minutes=hold_min, balance=get_paper_balance(),
    )


def _run_ai_brain(tg_manager):
    try:
        insight = analyze_and_adapt()
        if insight and tg_manager:
            tg_manager.send(f"🧠 <b>AI Brain Raporu</b>\n\n{str(insight)[:800]}")
    except Exception as e:
        logger.error(f"AI Brain hata: {e}")


# ─────────────────────────────────────────────────────────────────────────────
# GÜNLÜK ÖZET
# ─────────────────────────────────────────────────────────────────────────────

_last_daily_summary = None

def _check_pending_outcomes(client):
    """Veto/Watch edilen sinyallerin piyasa sonucunu kontrol et (pseudo-outcome)."""
    try:
        pending = get_pending_outcomes(max_age_hours=3)
        if not pending:
            return
        for rec in pending[:10]:  # Her döngüde max 10 kontrol
            try:
                symbol    = rec["symbol"]
                direction = rec["direction"]
                entry     = rec["entry_price"] or 0
                if not entry:
                    continue

                # Güncel fiyatı al
                ticker = client.futures_ticker(symbol=symbol)
                price  = float(ticker.get("lastPrice", 0))
                if not price:
                    continue

                now_ts = datetime.now(timezone.utc)
                created = datetime.fromisoformat(rec["created_at"].replace("Z", "+00:00"))
                age_min = (now_ts - created).total_seconds() / 60

                fields: dict = {}

                if 28 <= age_min <= 35 and rec.get("price_30m") is None:
                    fields["price_30m"] = price
                    if direction == "LONG":
                        fields["mfe_30m"] = round((price - entry) / entry * 100, 3)
                        fields["mae_30m"] = 0
                    else:
                        fields["mfe_30m"] = round((entry - price) / entry * 100, 3)
                        fields["mae_30m"] = 0

                elif 58 <= age_min <= 65 and rec.get("price_60m") is None:
                    fields["price_60m"] = price
                    if direction == "LONG":
                        fields["mfe_60m"] = round((price - entry) / entry * 100, 3)
                        fields["mae_60m"] = 0
                    else:
                        fields["mfe_60m"] = round((entry - price) / entry * 100, 3)
                        fields["mae_60m"] = 0

                elif age_min >= 118 and not rec.get("checked"):
                    fields["price_120m"] = price
                    # Kazanç tespiti — %0.5 üstü hareket "kazanacaktı"
                    if direction == "LONG":
                        ret = (price - entry) / entry * 100
                    else:
                        ret = (entry - price) / entry * 100
                    fields["would_have_won"] = 1 if ret > 0.5 else 0
                    fields["checked"] = 1

                if fields:
                    update_candidate_outcome(rec["id"], fields)
            except Exception:
                pass
    except Exception as e:
        logger.debug(f"[Outcome] kontrol hatası: {e}")


def maybe_save_daily_summary():
    global _last_daily_summary
    today = datetime.now(timezone.utc).date().isoformat()
    if _last_daily_summary == today:
        return
    _last_daily_summary = today
    stats = get_stats(hours=24)
    save_daily_summary({
        "date":        today,
        "trade_count": stats["total"],
        "win_count":   stats["wins"],
        "loss_count":  stats["losses"],
        "win_rate":    stats["win_rate"],
        "net_pnl":     stats["total_pnl"],
        "avg_r":       stats["avg_r"],
        "max_drawdown":stats["max_drawdown"],
        "balance_eod": get_paper_balance(),
    })


# ─────────────────────────────────────────────────────────────────────────────
# ANA LOOP
# ─────────────────────────────────────────────────────────────────────────────

def main():
    global consecutive_losses, circuit_breaker_until

    # ── Başlatma ─────────────────────────────────────────────────────────────
    logger.info(f"AX Bot başlıyor | AX_MODE={AX_MODE} | EXECUTION={EXECUTION_MODE}")

    init_db()
    for _attempt in range(10):
        try:
            init_paper_account()
            break
        except Exception as _e:
            logger.warning(f"init_paper_account bekleniyor ({_attempt+1}/10): {_e}")
            time.sleep(3)
    init_coin_library()

    client = Client(BINANCE_API_KEY, BINANCE_API_SECRET)
    ai_set_client(client)

    # Önceki circuit breaker durumunu yükle
    cb_str = get_state("circuit_breaker_until")
    if cb_str:
        try:
            circuit_breaker_until = datetime.fromisoformat(cb_str)
        except Exception:
            pass

    # Telegram
    tg_manager = None
    if TG_AVAILABLE:
        tg_manager = TelegramManager(client)
        tg_manager.paper_mode = PAPER_MODE
        tg_manager.start(
            get_balance_fn=get_paper_balance,
            get_open_trades_fn=get_open_trades,
            run_ai_brain_fn=lambda: _run_ai_brain(tg_manager),
            get_circuit_breaker_fn=is_circuit_breaker_active,
        )

    # n8n
    if N8N_AVAILABLE:
        register_bot(
            tg_manager=tg_manager,
            get_balance_fn=get_paper_balance,
            get_open_trades_fn=get_open_trades,
            run_ai_brain_fn=lambda: _run_ai_brain(tg_manager),
        )

    # Startup mesajı
    bal = get_paper_balance()
    logger.info(f"Bakiye: ${bal:.2f} | {len(COIN_UNIVERSE)} coin taranacak")
    if tg_manager:
        tg_manager.send(
            f"🟢 <b>AX Bot AKTİF</b>\n"
            f"⏰ {datetime.now(timezone.utc).strftime('%H:%M:%S UTC')}\n\n"
            f"💰 Bakiye: ${bal:.2f}\n"
            f"📊 {len(COIN_UNIVERSE)} coin | AX_MODE={AX_MODE}\n"
            f"⛔ CB: {CIRCUIT_BREAKER_LOSSES} kayıp = {CIRCUIT_BREAKER_MINUTES}dk"
        )

    last_scan    = 0
    last_hb      = time.time()
    prev_open_ids = {t["id"] for t in get_open_trades()}

    while True:
        try:
            now = time.time()

            # ── ADIM 8+9: MONITOR — her döngüde ─────────────────────────────
            newly_closed = exec_monitor(client)
            for trade_id in newly_closed:
                from database import get_trade
                t = get_trade(trade_id)
                if t:
                    on_trade_closed(client, tg_manager, t)
                    pnl_str = f"{t.get('net_pnl', 0):+.3f}$"
                    reason  = t.get("close_reason", "?").upper()
                    tg_send(tg_manager,
                        f"{'✅' if (t.get('net_pnl') or 0) > 0 else '❌'} "
                        f"<b>{t['symbol']} {t['direction']} KAPANDI</b>\n"
                        f"Neden: {reason} | PnL: {pnl_str}\n"
                        f"Bakiye: ${get_paper_balance():.2f}"
                    )

            # ── HEARTBEAT (varsayılan 6 saatte 1) ────────────────────────────
            if now - last_hb >= HEARTBEAT_INTERVAL:
                last_hb = now
                bal = get_paper_balance()
                open_c = len(get_open_trades())
                tg_send(tg_manager,
                    f"💓 <b>BOT AKTİF</b> | "
                    f"{datetime.now(timezone.utc).strftime('%H:%M')} UTC\n"
                    f"Bakiye: ${bal:.2f} | Açık: {open_c} trade"
                )
                maybe_save_daily_summary()

            # ── SCAN döngüsü ──────────────────────────────────────────────────
            if now - last_scan < SCAN_INTERVAL:
                time.sleep(1)
                continue
            last_scan = now

            # ── Pause / Circuit Breaker ───────────────────────────────────────
            if tg_manager and tg_manager.is_paused:
                time.sleep(5)
                continue

            cb_active, cb_remaining = is_circuit_breaker_active()
            if cb_active:
                logger.info(f"Devre kesici — {cb_remaining}dk kaldı")
                time.sleep(10)
                continue

            # Finish modu: açık trade yoksa bitir
            if tg_manager and tg_manager.is_finish_mode:
                if not get_open_trades():
                    tg_manager.notify_finish_complete(get_paper_balance())
                    logger.info("Finish modu tamamlandı.")
                    break
                time.sleep(10)
                continue

            # ── ADIM 1: SCAN ─────────────────────────────────────────────────
            open_trades_now = get_open_trades()
            open_symbols    = {t["symbol"] for t in open_trades_now}

            if len(open_trades_now) >= MAX_OPEN_TRADES:
                time.sleep(5)
                continue

            session = get_current_session()
            regime  = get_market_regime(client)

            candidates = scan(client, open_symbols=open_symbols)

            # Pipeline sayacını sıfırla
            _pipeline.update({
                "scanned_symbols":    len(COIN_UNIVERSE),
                "passed_market_filter": len(candidates),
                "candidates_created": 0,
                "ax_allow": 0, "ax_veto": 0, "ax_watch": 0,
                "risk_rejected": 0, "paper_trades_opened": 0,
                "session": session, "market_regime": regime,
                "last_veto_reason": None, "last_allow_symbol": None,
            })

            if not candidates:
                logger.info(
                    f"[Pipeline] scan={len(COIN_UNIVERSE)} geçen=0 "
                    f"candidate=0 (piyasa hareketsiz veya filtre çok sıkı)"
                )
                save_pipeline_stats(_pipeline)
                time.sleep(5)
                continue

            # ── Pending outcome kontrol (pseudo-outcome tracking) ─────────────
            _check_pending_outcomes(client)

            # ── ADIM 2-7: SİNYAL → KARAR → AÇILIŞ ──────────────────────────
            for coin_info in candidates:
                if len(get_open_trades()) >= MAX_OPEN_TRADES:
                    break

                symbol = coin_info["symbol"]

                # ADIM 2: Sinyal üret
                signal = generate_signal(client, symbol, coin_info)
                if not signal["direction"]:
                    continue

                _pipeline["candidates_created"] += 1

                # ADIM 3: DB'ye kaydet
                candidate_id = save_signal_candidate({
                    **signal,
                    "session":        session,
                    "market_regime":  regime,
                    "ax_mode":        AX_MODE,
                    "execution_mode": EXECUTION_MODE,
                })
                signal["candidate_id"] = candidate_id

                # ADIM 4: AX karar
                ax = evaluate_signal(
                    signal=signal,
                    open_trades=get_open_trades(),
                    balance=get_paper_balance(),
                    consecutive_losses=consecutive_losses,
                    circuit_breaker_active=cb_active,
                    session=session,
                    market_regime=regime,
                )

                # ADIM 5: Karar
                decision = ax["decision"]
                update_signal_decision(
                    candidate_id, decision,
                    ax["score"], ax["confidence"],
                    ax.get("veto_reason"),
                )

                if decision == "VETO":
                    _pipeline["ax_veto"] += 1
                    _pipeline["last_veto_reason"] = ax.get("veto_reason", "")
                    logger.info(
                        f"[VETO] {symbol} {signal['direction']} "
                        f"| {ax.get('veto_reason','')} | score={ax['score']:.0f}"
                    )
                    # Pseudo-outcome: veto edilen sinyali izlemeye al
                    save_candidate_outcome(
                        candidate_id, symbol, signal["direction"],
                        "VETO", signal["entry"]
                    )
                    continue

                elif decision == "WATCH":
                    _pipeline["ax_watch"] += 1
                    logger.info(
                        f"[WATCH] {symbol} {signal['direction']} "
                        f"| score={ax['score']:.0f}"
                    )
                    save_candidate_outcome(
                        candidate_id, symbol, signal["direction"],
                        "WATCH", signal["entry"]
                    )
                    continue

                # ALLOW
                _pipeline["ax_allow"] += 1
                _pipeline["last_allow_symbol"] = symbol

                # ADIM 6: Execution eşiği — ALLOW ama execution kriterleri
                if AX_MODE != "execute":
                    _pipeline["risk_rejected"] += 1
                    logger.info(f"[OBSERVE] {symbol} — AX_MODE=observe, açılmıyor")
                    continue

                # Execution kalite eşikleri (candidate'dan daha sıkı)
                if (ax["score"] < EXEC_SCORE_MIN
                        or ax["confidence"] < EXEC_CONFIDENCE_MIN
                        or signal.get("rr", 0) < EXEC_MIN_RR
                        or signal.get("expected_mfe_r", 0) < EXEC_MIN_EXPECTED_MFE_R
                        or coin_info.get("volume", 0) < EXEC_MIN_VOLUME_M):
                    _pipeline["risk_rejected"] += 1
                    logger.info(
                        f"[EXEC_FILTER] {symbol} — execution eşiği karşılanmadı "
                        f"(score={ax['score']:.0f} conf={ax['confidence']:.2f} "
                        f"rr={signal.get('rr',0):.2f} vol={coin_info.get('volume',0):.1f}M)"
                    )
                    continue

                # ADIM 7: Aç
                trade_id = open_trade(client, signal, ax)
                if not trade_id:
                    continue

                _pipeline["paper_trades_opened"] += 1
                update_signal_decision(candidate_id, "ALLOW",
                                       ax["score"], ax["confidence"],
                                       linked_trade_id=trade_id)

                # Telegram bildirimi
                tg_send(tg_manager,
                    f"🎯 <b>{symbol} {signal['direction']} AÇILDI</b>\n"
                    f"Giriş: {signal['entry']:.5f}\n"
                    f"SL: {signal['sl']:.5f} | TP1: {signal['tp1']:.5f}\n"
                    f"RR: {signal['rr']:.2f} | Skor: {ax['score']:.0f}\n"
                    f"Bakiye: ${get_paper_balance():.2f}"
                )

                notify_trade_open(
                    symbol=symbol, direction=signal["direction"],
                    entry=signal["entry"], sl=signal["sl"], tp=signal["tp1"],
                    leverage=1, ml_score=ax["score"],
                    rr=signal["rr"], balance=get_paper_balance(),
                )

                logger.info(
                    f"✅ AÇILDI {symbol} {signal['direction']} "
                    f"score={ax['score']:.0f} rr={signal['rr']:.2f}"
                )

            # ── Pipeline logla ────────────────────────────────────────────────
            logger.info(
                f"[Pipeline] scan={_pipeline['scanned_symbols']} "
                f"geçen={_pipeline['passed_market_filter']} "
                f"candidate={_pipeline['candidates_created']} "
                f"allow={_pipeline['ax_allow']} veto={_pipeline['ax_veto']} "
                f"watch={_pipeline['ax_watch']} "
                f"exec_filter={_pipeline['risk_rejected']} "
                f"trade={_pipeline['paper_trades_opened']}"
            )
            save_pipeline_stats(_pipeline)
            _last_scan_summary.update(_pipeline)
            _last_scan_time = datetime.now(timezone.utc).isoformat()

        except KeyboardInterrupt:
            logger.info("Bot durduruldu (KeyboardInterrupt).")
            break
        except Exception as e:
            logger.error(f"Ana döngü hatası: {e}", exc_info=True)
            time.sleep(10)


# ─────────────────────────────────────────────────────────────────────────────
# GİRİŞ NOKTASI
# ─────────────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    main()
