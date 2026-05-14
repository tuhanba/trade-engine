"""
scalp_bot_v3.py – Ana bot runner.

Paper mode default. Ctrl+C ile graceful shutdown.
Telegram/Binance eksikse crash olmaz.
"""

from __future__ import annotations

import logging
import signal
import sys
import time
import traceback
from datetime import datetime, timezone

import config
import database
from core.market_data import get_public_tickers, get_current_price
from core.coin_library import build_symbol_universe, rank_symbols_by_activity
from core.signal_engine import generate_signal
from core.ai_decision_engine import classify_signal
from core.risk_engine import should_open_trade
from core.paper_tracker import register_candidate, update_candidate_outcome
from core.data_layer import SignalDecision
from execution_engine import ExecutionEngine
from telegram_delivery import TelegramDelivery

# ── Logging ─────────────────────────────────────────────────────────

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s – %(message)s",
    handlers=[
        logging.StreamHandler(sys.stdout),
    ],
)
logger = logging.getLogger("ax.bot")

# ── Graceful shutdown ───────────────────────────────────────────────
_running = True


def _shutdown_handler(signum, frame):
    global _running
    logger.info("Shutdown sinyali alındı – döngü durduruluyor…")
    _running = False


signal.signal(signal.SIGINT, _shutdown_handler)
signal.signal(signal.SIGTERM, _shutdown_handler)


# ── Safety check ────────────────────────────────────────────────────

def safety_check() -> bool:
    """Güvenlik kontrollerini yapar. Sorun varsa False döner."""
    summary = config.safety_summary()
    logger.info("Güvenlik durumu: %s", summary)

    if summary["live_allowed"]:
        logger.error("LIVE TRADING AKTİF – bu bot sadece paper mode!")
        return False

    if summary["private_api_allowed"]:
        logger.error("PRIVATE API AKTİF – sadece public data kullanılmalı!")
        return False

    logger.info(
        "✓ Paper mode aktif  |  DRY_RUN=%s  |  MODE=%s",
        config.DRY_RUN, config.EXECUTION_MODE,
    )
    return True


# ── Ana döngü ──────────────────────────────────────────────────────

def run_scan_loop():
    """Tek bir scan döngüsü çalıştırır."""
    engine = ExecutionEngine()

    # 1. Market data çek
    tickers = get_public_tickers()
    if not tickers:
        logger.warning("Ticker verisi alınamadı – döngü atlanıyor")
        return

    # 2. Symbol universe oluştur (filtre dahil)
    universe = build_symbol_universe(
        tickers,
        min_volume_usdt=config.MIN_VOLUME_USDT,
        min_move_pct=config.MIN_MOVE_PCT,
    )
    universe = rank_symbols_by_activity(universe)

    # İlk N sembol
    top_symbols = universe[:20]

    logger.info("Taranacak sembol sayısı: %d", len(top_symbols))

    # 3. Açık trade'leri güncelle
    engine.update_open_trades()

    # 4. Mevcut açık trade'ler
    open_trades = database.get_open_trades()

    # 5. Bakiye
    stats = database.get_dashboard_stats()
    balance = stats.get("balance", 1000.0)

    # 6. Her sembol için sinyal üret ve değerlendir
    for ticker in top_symbols:
        if not _running:
            break

        symbol = ticker.get("symbol", "")
        if not symbol:
            continue

        # Market context
        market_ctx = {
            "last_price": float(ticker.get("lastPrice", 0)),
            "price_change_pct": float(ticker.get("priceChangePercent", 0)),
            "volume_usdt": float(ticker.get("quoteVolume", 0)),
            "high_24h": float(ticker.get("highPrice", 0)),
            "low_24h": float(ticker.get("lowPrice", 0)),
        }

        # Sinyal üret
        sig = generate_signal(symbol, market_ctx)
        if sig is None:
            continue

        # AI karar
        ai_result = classify_signal(sig)

        # AI VETO ise kaydet ve geç
        if ai_result.decision == SignalDecision.VETO.value:
            register_candidate(sig, ai_result.decision, ai_result.reason)
            continue

        # Risk filtresi
        can_open, decision, reason = should_open_trade(
            sig, open_trades, balance,
        )

        if can_open:
            trade_id = engine.process_signal(sig)
            if trade_id:
                # Açık trade listesini güncelle
                open_trades = database.get_open_trades()
        else:
            # Açılmayan sinyal → candidate olarak kaydet
            register_candidate(sig, decision, reason)

        # Ghost tracking güncelle
        update_candidate_outcome(symbol, market_ctx["last_price"])


def main():
    """Ana bot giriş noktası."""
    logger.info("=" * 60)
    logger.info("AX Trade Engine v3 başlatılıyor…")
    logger.info("=" * 60)

    # DB init
    database.init_db()
    database.migrate_db()

    # Safety check
    if not safety_check():
        logger.error("Güvenlik kontrolü başarısız – çıkılıyor!")
        sys.exit(1)

    telegram = TelegramDelivery()
    telegram.send_message("🤖 AX Bot başlatıldı (paper mode)")

    # Heartbeat
    database.update_bot_status("status", "running")
    database.update_bot_status(
        "heartbeat",
        datetime.now(timezone.utc).isoformat(),
    )

    loop_count = 0
    while _running:
        loop_count += 1
        try:
            logger.info("── Scan #%d ──", loop_count)
            run_scan_loop()

            # Heartbeat güncelle
            database.update_bot_status(
                "heartbeat",
                datetime.now(timezone.utc).isoformat(),
            )

        except Exception as exc:
            error_msg = f"{type(exc).__name__}: {exc}"
            logger.error("Scan hatası: %s", error_msg)
            logger.debug(traceback.format_exc())

            database.update_bot_status("last_error", error_msg)
            telegram.send_error("Scan Hatası", error_msg)

        if _running:
            logger.info(
                "Sonraki scan %ds sonra…",
                config.SCAN_INTERVAL_SECONDS,
            )
            time.sleep(config.SCAN_INTERVAL_SECONDS)

    # Shutdown
    database.update_bot_status("status", "stopped")
    telegram.send_message("🛑 AX Bot durduruldu")
    logger.info("Bot durduruldu.")


if __name__ == "__main__":
    main()
