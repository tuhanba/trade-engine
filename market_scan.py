"""
market_scan.py — AX Piyasa Tarama Motoru
==========================================
COIN_UNIVERSE içinden trade edilebilir coinleri filtreler ve sıralar.

Filtreler:
  - Futures aktif
  - Min hacim (coin profiline göre)
  - Spread uygun (< %0.1)
  - Hareket var (volatilite > eşik)
  - Cooldown yok
  - Blacklist değil
  - Açık trade yok

Çıktı: [{"symbol": ..., "volume": ..., "change_pct": ..., "spread_pct": ...}]
skor sırasına göre.
"""

import logging
import time
from datetime import datetime, timezone

from config import (
    COIN_UNIVERSE,
    CAND_MIN_VOLUME_M, CAND_MIN_CHANGE_PCT,
    EXEC_MIN_VOLUME_M,
    DEBUG_SIGNAL_MODE,
)
from database import get_conn, is_coin_in_cooldown, get_open_trades
from coin_library import get_coin_params, is_coin_enabled

logger = logging.getLogger(__name__)

# Candidate modu (gevşek) — AX öğrenimi için
CAND_MAX_SPREAD_PCT = 0.15
CAND_MAX_CHANGE_ABS = 30.0

# Execution modu (sıkı) — backward compat
MIN_VOLUME_M   = EXEC_MIN_VOLUME_M
MAX_SPREAD_PCT = 0.10
MIN_CHANGE_ABS = CAND_MIN_CHANGE_PCT
MAX_CHANGE_ABS = 25.0

# Ticker cache — Binance API'ye gereksiz çarpma önler
_ticker_cache: dict = {}
_ticker_ts: float = 0
_TICKER_TTL = 45  # saniye


def _get_tickers(client) -> dict:
    """Tüm futures ticker'larını cache'li getir."""
    global _ticker_cache, _ticker_ts
    now = time.time()
    if now - _ticker_ts < _TICKER_TTL and _ticker_cache:
        return _ticker_cache
    try:
        tickers = client.futures_ticker()
        _ticker_cache = {t["symbol"]: t for t in tickers}
        _ticker_ts = now
    except Exception as e:
        logger.warning(f"[MarketScan] Ticker alınamadı: {e}")
    return _ticker_cache


def _get_open_symbols() -> set:
    """Şu anda açık pozisyon olan sembolleri döndür."""
    try:
        return {t["symbol"] for t in get_open_trades()}
    except Exception:
        return set()


def scan(client, open_symbols: set = None) -> list:
    """
    COIN_UNIVERSE'i tara, trade edilebilir coinleri döndür.

    Candidate modu (gevşek): AX öğrenmesi için daha fazla coin geçirir.
    Execution modu (sıkı): Trade açmak için daha az coin geçirir.
    Bu fonksiyon CANDIDATE modunda çalışır — scalp_bot execution filtresi uygular.

    Returns:
        list of dicts: [{"symbol", "volume", "change_pct", "spread_pct", "score"}]
        Skor sırasına göre azalan.
    """
    if open_symbols is None:
        open_symbols = _get_open_symbols()

    tickers = _get_tickers(client)
    if not tickers:
        return []

    # DEBUG_SIGNAL_MODE'da minimum filtre
    min_vol_threshold = CAND_MIN_VOLUME_M
    min_chg_threshold = CAND_MIN_CHANGE_PCT
    max_chg_threshold = CAND_MAX_CHANGE_ABS
    max_spread        = CAND_MAX_SPREAD_PCT

    results = []
    scanned = 0
    passed  = 0

    for symbol in COIN_UNIVERSE:
        scanned += 1

        # ── Temel filtreler ──────────────────────────────────────────────────
        if symbol in open_symbols:
            continue

        if not is_coin_enabled(symbol):
            continue

        if is_coin_in_cooldown(symbol):
            continue

        ticker = tickers.get(symbol)
        if not ticker:
            continue

        # ── Hacim filtresi (coin profilinden al, fallback candidate threshold) ─
        try:
            volume_m = float(ticker["quoteVolume"]) / 1_000_000
        except (KeyError, ValueError):
            continue

        coin_p  = get_coin_params(symbol)
        # Coin bazlı min_volume_m'yi candidate eşiğiyle karşılaştır;
        # candidate modunda daha gevşek olan tercih edilir
        coin_min_vol = coin_p.get("min_volume_m", min_vol_threshold)
        effective_min_vol = min(coin_min_vol, min_vol_threshold) if DEBUG_SIGNAL_MODE else min_vol_threshold
        if volume_m < effective_min_vol:
            continue

        # ── Hareket filtresi ─────────────────────────────────────────────────
        try:
            change_pct = abs(float(ticker["priceChangePercent"]))
        except (KeyError, ValueError):
            continue

        if change_pct < min_chg_threshold or change_pct > max_chg_threshold:
            continue

        # ── Spread filtresi ──────────────────────────────────────────────────
        try:
            bid = float(ticker.get("bidPrice", 0))
            ask = float(ticker.get("askPrice", 0))
            spread_pct = (ask - bid) / bid * 100 if bid > 0 and ask > 0 else 0
        except (KeyError, ValueError):
            spread_pct = 0

        if spread_pct > max_spread:
            continue

        passed += 1

        # ── Skor hesapla ─────────────────────────────────────────────────────
        vol_score    = min(volume_m / 500, 1.0) * 40
        move_score   = min(change_pct / 5.0, 1.0) * 40
        spread_score = max(0, (max_spread - spread_pct) / max_spread) * 20
        score = vol_score + move_score + spread_score

        results.append({
            "symbol":     symbol,
            "volume":     round(volume_m, 2),
            "change_pct": round(float(ticker["priceChangePercent"]), 2),
            "spread_pct": round(spread_pct, 4),
            "price":      float(ticker.get("lastPrice", 0)),
            "score":      round(score, 2),
        })

    results.sort(key=lambda x: x["score"], reverse=True)
    logger.info(
        f"[MarketScan] {len(COIN_UNIVERSE)} tarandı | "
        f"{passed} hacim+hareket geçti | {len(results)} sonuç döndü"
    )
    return results


def get_current_session() -> str:
    """Mevcut trading seansını döndür."""
    hour = datetime.now(timezone.utc).hour
    if 8 <= hour < 13:
        return "LONDON"
    elif 13 <= hour < 17:
        return "OVERLAP"   # London + NY çakışması — en iyi seans
    elif 17 <= hour < 22:
        return "NEW_YORK"
    elif 22 <= hour or hour < 3:
        return "LATE_NY"
    else:
        return "ASIA"      # 03-08 UTC — düşük hacim


def get_market_regime(client) -> str:
    """
    BTC 1h trendine bakarak genel piyasa rejimini döndür.
    BULLISH | BEARISH | CHOPPY | NEUTRAL
    """
    try:
        klines = client.futures_klines(symbol="BTCUSDT", interval="1h", limit=50)
        closes = [float(k[4]) for k in klines]

        if len(closes) < 20:
            return "NEUTRAL"

        ema9  = _ema(closes, 9)
        ema21 = _ema(closes, 21)
        last  = closes[-1]

        # Trend belirleme
        if ema9 > ema21 * 1.002 and last > ema9:
            return "BULLISH"
        elif ema9 < ema21 * 0.998 and last < ema9:
            return "BEARISH"

        # Choppy: son 20 mumda range dar mı?
        recent = closes[-20:]
        rng = (max(recent) - min(recent)) / min(recent) * 100
        if rng < 1.5:
            return "CHOPPY"

        return "NEUTRAL"
    except Exception as e:
        logger.debug(f"[MarketScan] Regime alınamadı: {e}")
        return "NEUTRAL"


def _ema(closes: list, period: int) -> float:
    """Basit EMA hesabı."""
    if len(closes) < period:
        return closes[-1]
    k = 2 / (period + 1)
    ema = closes[0]
    for c in closes[1:]:
        ema = c * k + ema * (1 - k)
    return ema
