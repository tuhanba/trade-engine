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

from config import COIN_UNIVERSE
from database import get_conn, is_coin_in_cooldown, get_open_trades
from coin_library import get_coin_params, is_coin_enabled

logger = logging.getLogger(__name__)

# Tarama filtre sabitleri
MIN_VOLUME_M      = 10.0    # Milyon $ minimum 24s hacim
MAX_SPREAD_PCT    = 0.10    # % maksimum spread
MIN_CHANGE_ABS    = 0.3     # % minimum mutlak fiyat değişimi (hareket var mı)
MAX_CHANGE_ABS    = 25.0    # % maksimum fiyat değişimi (pump/dump engellemek)

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

    Returns:
        list of dicts: [{"symbol", "volume", "change_pct", "spread_pct", "score"}]
        Skor sırasına göre azalan.
    """
    if open_symbols is None:
        open_symbols = _get_open_symbols()

    tickers = _get_tickers(client)
    if not tickers:
        return []

    results = []

    for symbol in COIN_UNIVERSE:
        # ── Temel filtreler ──────────────────────────────────────────────────
        if symbol in open_symbols:
            continue  # Zaten açık trade var

        if not is_coin_enabled(symbol):
            continue  # Devre dışı

        if is_coin_in_cooldown(symbol):
            continue  # Cooldown'da

        ticker = tickers.get(symbol)
        if not ticker:
            continue  # Futures'da yok

        # ── Hacim filtresi ───────────────────────────────────────────────────
        try:
            volume_m = float(ticker["quoteVolume"]) / 1_000_000
        except (KeyError, ValueError):
            continue

        coin_p = get_coin_params(symbol)
        min_vol = coin_p.get("min_volume_m", MIN_VOLUME_M)
        if volume_m < min_vol:
            continue

        # ── Hareket filtresi ─────────────────────────────────────────────────
        try:
            change_pct = abs(float(ticker["priceChangePercent"]))
        except (KeyError, ValueError):
            continue

        if change_pct < MIN_CHANGE_ABS or change_pct > MAX_CHANGE_ABS:
            continue

        # ── Spread filtresi ──────────────────────────────────────────────────
        try:
            bid = float(ticker.get("bidPrice", 0))
            ask = float(ticker.get("askPrice", 0))
            if bid <= 0 or ask <= 0:
                spread_pct = 0
            else:
                spread_pct = (ask - bid) / bid * 100
        except (KeyError, ValueError):
            spread_pct = 0

        if spread_pct > MAX_SPREAD_PCT:
            continue

        # ── Skor hesapla ─────────────────────────────────────────────────────
        # Yüksek hacim + orta hareket = iyi fırsat
        vol_score    = min(volume_m / 500, 1.0) * 40        # max 40 puan
        move_score   = min(change_pct / 5.0, 1.0) * 40     # max 40 puan
        spread_score = max(0, (MAX_SPREAD_PCT - spread_pct) / MAX_SPREAD_PCT) * 20  # max 20 puan
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
    logger.info(f"[MarketScan] {len(results)}/{len(COIN_UNIVERSE)} coin geçti.")
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
