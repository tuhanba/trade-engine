"""
scalp_bot_v3.py — AX Scalp Bot v5.0 FINAL
==========================================
3 paralel dongu:
  1. main_loop       : Coin tarama + sinyal + trade acma (60 sn)
  2. monitor_loop    : Acik trade'leri izle TP/SL/Trail (15 sn)
  3. ghost_loop      : WATCH/VETO sinyallerini simule et (5 dk)
"""
import asyncio
import logging
import time
from datetime import datetime, timezone

from config import (
    SCAN_INTERVAL, MAX_OPEN_TRADES, TRADE_THRESHOLD,
    WATCHLIST_THRESHOLD, TELEGRAM_THRESHOLD,
    BINANCE_API_KEY, BINANCE_API_SECRET, DB_PATH,
    AI_MAX_DAILY_SIGNALS, AI_GHOST_HORIZON_MINUTES,
    MIN_VOLUME_USD, TOP_COINS_SCAN
)
from database import (
    init_db, get_open_trades, get_paper_balance,
    save_paper_result, get_conn
)
import execution_engine as eng
import telegram_delivery as tg

# Core moduller
from core.ai_decision_engine import AIDecisionEngine
from core.trigger_engine      import TriggerEngine
from core.paper_tracker       import PaperTracker
from core.data_layer          import DataLayer

# Logging ayarla
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%H:%M:%S"
)
logger = logging.getLogger("scalp_bot")

# ─────────────────────────────────────────────────────────────────────────────
# GLOBAL NESNELER
# ─────────────────────────────────────────────────────────────────────────────
_client     = None
_ai         = None
_trigger    = None
_tracker    = None
_data_layer = None

def _init_modules():
    """Tum modulleri baslat."""
    global _client, _ai, _trigger, _tracker, _data_layer
    try:
        from binance.client import Client
        _client = Client(BINANCE_API_KEY, BINANCE_API_SECRET)
        logger.info("[Init] Binance client baglandi")
    except Exception as e:
        logger.warning(f"[Init] Binance client hatasi (paper modda devam): {e}")
        _client = None

    _ai      = AIDecisionEngine(db_path=DB_PATH)
    _trigger = TriggerEngine(_client)
    _tracker = PaperTracker(db_path=DB_PATH)
    _data_layer = DataLayer(_client)
    logger.info("[Init] Tum moduller hazir")


def _get_scan_symbols() -> list:
    """Taranacak coin listesini al (hacim bazli)."""
    import requests
    try:
        r = requests.get(
            "https://fapi.binance.com/fapi/v1/ticker/24hr",
            timeout=10
        )
        if r.status_code != 200:
            return []
        tickers = r.json()
        usdt = [
            t for t in tickers
            if t["symbol"].endswith("USDT")
            and not any(x in t["symbol"] for x in ["_", "BUSD"])
            and float(t.get("quoteVolume", 0)) >= MIN_VOLUME_USD
        ]
        # Hacme gore sirala, en iyi TOP_COINS_SCAN al
        usdt.sort(key=lambda x: float(x.get("quoteVolume", 0)), reverse=True)
        symbols = [t["symbol"] for t in usdt[:TOP_COINS_SCAN]]
        logger.info(f"[Scan] {len(symbols)} coin taranacak")
        return symbols
    except Exception as e:
        logger.error(f"[Scan] Symbol listesi hatasi: {e}")
        return []


# ─────────────────────────────────────────────────────────────────────────────
# ANA TARAMA DONGUSU
# ─────────────────────────────────────────────────────────────────────────────

async def main_loop():
    """Coin tara, sinyal uret, trade ac."""
    logger.info("[Bot] Ana tarama dongusu basladi")
    scan_count = 0

    while True:
        try:
            scan_count += 1
            loop_start = time.time()

            # Acik trade sayisi kontrolu
            open_trades = get_open_trades()
            open_count  = len(open_trades) if open_trades else 0
            if open_count >= MAX_OPEN_TRADES:
                logger.info(f"[Bot] MAX_OPEN_TRADES={MAX_OPEN_TRADES} doldu, tarama atlanıyor")
                await asyncio.sleep(SCAN_INTERVAL)
                continue

            # Coin listesi al
            symbols = _get_scan_symbols()
            if not symbols:
                await asyncio.sleep(30)
                continue

            # Her coini tara
            allowed_this_scan = 0
            for symbol in symbols:
                try:
                    await _process_symbol(symbol, open_count)
                    # Acik trade sayisini guncelle
                    open_count = len(get_open_trades() or [])
                    if open_count >= MAX_OPEN_TRADES:
                        break
                except Exception as e:
                    logger.error(f"[Bot] {symbol} isleme hatasi: {e}")
                await asyncio.sleep(0.3)  # Rate limit

            elapsed = time.time() - loop_start
            logger.info(f"[Bot] Tarama #{scan_count} tamamlandi ({elapsed:.1f}s) | Acik: {open_count}")

        except Exception as e:
            logger.error(f"[Bot] main_loop hatasi: {e}", exc_info=True)

        await asyncio.sleep(SCAN_INTERVAL)


async def _process_symbol(symbol: str, current_open: int):
    """Tek bir coini analiz et ve karar ver."""
    try:
        # Zaten bu coinde acik trade var mi?
        open_trades = get_open_trades() or []
        if any(t.get("symbol") == symbol for t in open_trades):
            return

        # Veri cek
        sig = _data_layer.get_signal_for_symbol(symbol) if _data_layer else None
        if sig is None:
            return

        # Trigger analizi
        for direction in ["LONG", "SHORT"]:
            try:
                trigger_res = _trigger.analyze(symbol, direction)
                if not trigger_res or trigger_res.get("quality") == "D":
                    continue

                # Signal objesini zenginlestir
                sig.direction     = direction
                sig.setup_quality = trigger_res.get("quality", "B")
                sig.score         = trigger_res.get("score", 5.0)
                sig.entry_zone    = trigger_res.get("entry", 0) or sig.entry_zone
                # SL: trigger_engine'den gelen entry + ATR bazlı hesapla
                entry_price = float(trigger_res.get("entry", 0) or 0)
                if entry_price > 0:
                    # ATR proxy: entry'nin %2'si (scalp için makul varsayılan)
                    # Gerçek ATR trigger_res'te yoksa bu fallback kullanılır
                    atr_pct = 0.02
                    if direction == "LONG":
                        sig.stop_loss = round(entry_price * (1 - atr_pct), 8)
                    else:
                        sig.stop_loss = round(entry_price * (1 + atr_pct), 8)
                else:
                    sig.stop_loss = getattr(sig, "stop_loss", 0) or 0

                # trigger_res alanlarini AI score alanlarına map et
                raw_score = trigger_res.get("score", 5.0)  # 0-10 arasi

                # trigger_score: 0-10 → 0-100
                sig.trigger_score = raw_score * 10.0

                # trend_score: BTC trend + momentum + MACD + saat
                btc_ok  = 1 if trigger_res.get("btc_trend") in ["UP", "NEUTRAL"] else 0
                mom     = trigger_res.get("momentum_3c", 0) or 0
                macd_h  = trigger_res.get("macd_hist", 0) or 0
                good_hr = 1 if trigger_res.get("good_hour", True) else 0
                sig.trend_score = min(100.0, max(0.0,
                    btc_ok * 30
                    + min(abs(float(mom)) * 200, 30)
                    + min(abs(float(macd_h)) * 5000, 20)
                    + good_hr * 20
                ))

                # risk_score: RSI + funding + ADX
                rsi5    = float(trigger_res.get("rsi5", 50) or 50)
                funding = float(trigger_res.get("funding", 0) or 0)
                adx     = float(trigger_res.get("adx", 20) or 20)
                rsi_ok  = 1.0 if 30 <= rsi5 <= 70 else (0.5 if 25 <= rsi5 <= 75 else 0.0)
                fund_ok = 1.0 if abs(funding) < 0.05 else (0.5 if abs(funding) < 0.1 else 0.0)
                adx_ok  = min(adx / 30.0, 1.0)
                sig.risk_score = min(100.0, rsi_ok * 40 + fund_ok * 30 + adx_ok * 30)

                # coin_score: AI coin profilinden al (ilk taramalarda 50 default)
                sig.coin_score = getattr(sig, "coin_score", 50) or 50

                # ml_score: trigger score 0-10 → 0-100
                sig.ml_score = raw_score * 10.0

                # confidence: kalite bazli
                _q_conf = {"S": 0.95, "A+": 0.90, "A": 0.85, "B": 0.75, "C": 0.65}
                sig.confidence = _q_conf.get(sig.setup_quality, 0.75)

                # AI karar
                ai_res = _ai.evaluate(sig)
                decision    = ai_res.get("decision", "VETO")
                final_score = ai_res.get("final_score", 0)

                if decision == "ALLOW":
                    # Dinamik kaldirach
                    leverage = _ai.decide_leverage(sig, ai_res)
                    sig.leverage = leverage

                    # Telegram sinyali gonder
                    try:
                        tg.deliver_signal(sig)
                    except Exception:
                        pass

                    # Trade ac
                    result = eng.open_trade(sig, _client, _ai)
                    if result.get("ok"):
                        logger.info(
                            f"[Bot] ALLOW: {symbol} {direction} {leverage}x "
                            f"score={final_score:.1f} quality={sig.setup_quality}"
                        )
                    else:
                        logger.warning(f"[Bot] Trade acilamadi: {result.get('error')}")

                elif decision == "WATCH":
                    logger.info(f"[Bot] WATCH: {symbol} {direction} score={final_score:.1f}")
                    # Ghost-tracker icin kaydet
                    try:
                        _save_ghost(sig, "WATCH", final_score)
                    except Exception:
                        pass
                    # Telegram sinyali (sadece WATCH icin de gonder)
                    try:
                        tg.deliver_signal(sig)
                    except Exception:
                        pass

                else:  # VETO
                    logger.debug(f"[Bot] VETO: {symbol} {direction} score={final_score:.1f} reason={ai_res.get('reason','')}")
                    try:
                        _save_ghost(sig, "VETO", final_score)
                    except Exception:
                        pass

            except Exception as e:
                logger.error(f"[Bot] {symbol} {direction} analiz hatasi: {e}")

    except Exception as e:
        logger.error(f"[Bot] _process_symbol {symbol} hatasi: {e}")


def _save_ghost(sig, decision: str, score: float):
    """WATCH/VETO sinyalini ghost-tracker icin DB'ye kaydet."""
    try:
        entry = float(getattr(sig, "entry_zone", 0) or 0)
        sl    = float(getattr(sig, "stop_loss",  0) or 0)
        tp1   = float(getattr(sig, "tp1", 0) or 0)
        tp2   = float(getattr(sig, "tp2", 0) or 0)
        tp3   = float(getattr(sig, "tp3", 0) or 0)
        if not entry:
            return
        save_paper_result({
            "symbol":          sig.symbol,
            "direction":       getattr(sig, "direction", "LONG"),
            "tracked_from":    decision,
            "preview_entry":   entry,
            "preview_sl":      sl,
            "preview_tp1":     tp1,
            "preview_tp2":     tp2,
            "preview_tp3":     tp3,
            "entry":           entry,
            "sl":              sl,
            "tp1":             tp1,
            "tp2":             tp2,
            "tp3":             tp3,
            "score":           score,
            "setup_quality":   getattr(sig, "setup_quality", "B"),
            "horizon_minutes": AI_GHOST_HORIZON_MINUTES,
            "status":          "pending",
        })
    except Exception as e:
        logger.warning(f"[Ghost] save_paper_result hatasi {sig.symbol}: {e}")


# ─────────────────────────────────────────────────────────────────────────────
# MONITOR DONGUSU
# ─────────────────────────────────────────────────────────────────────────────

async def monitor_loop():
    """Acik trade'leri 15 sn'de bir kontrol et."""
    logger.info("[Monitor] AI Execution monitor dongusu basladi")
    await asyncio.sleep(10)  # Bot baslarken biraz bekle

    while True:
        try:
            eng.monitor_open_trades(_client, _ai)
        except Exception as e:
            logger.error(f"[Monitor] monitor_loop hatasi: {e}", exc_info=True)
        await asyncio.sleep(15)


# ─────────────────────────────────────────────────────────────────────────────
# GHOST-TRACKER DONGUSU
# ─────────────────────────────────────────────────────────────────────────────

async def ghost_loop():
    """WATCH/VETO sinyallerini 5 dk'da bir simule et."""
    logger.info("[Ghost] Ghost-tracker dongusu basladi - WATCH/VETO sinyalleri izleniyor")
    await asyncio.sleep(60)  # Ilk 1 dk bekle

    while True:
        try:
            if _tracker:
                count = _tracker.process_pending()
                if count and count > 0:
                    logger.info(f"[Ghost] {count} WATCH/VETO sinyali simule edildi ve AI'a ogretildi")
        except Exception as e:
            logger.error(f"[Ghost] ghost_loop hatasi: {e}", exc_info=True)
        await asyncio.sleep(300)  # 5 dk


# ─────────────────────────────────────────────────────────────────────────────
# MAIN
# ─────────────────────────────────────────────────────────────────────────────

async def main():
    """Tum donguleri paralel calistir."""
    init_db()
    _init_modules()

    logger.info("=" * 50)
    logger.info("  AX SCALP BOT v5.0 FINAL BASLIYOR")
    logger.info("=" * 50)

    # 3 donguyu paralel calistir
    await asyncio.gather(
        main_loop(),
        monitor_loop(),
        ghost_loop(),
        return_exceptions=True
    )


if __name__ == "__main__":
    asyncio.run(main())
