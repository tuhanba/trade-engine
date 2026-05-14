"""
core/coin_library.py – Tradable sembol kütüphanesi ve filtre modülü.

USDT pariteleri hacim ve hareketle filtrelenir.
Düşük hacimli / istenmeyen pariteler elenir.
"""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger("ax.coin_library")

# İstenmeyen base coinler
_SKIP_BASES = {"BUSD", "USDC", "TUSD", "DAI", "FDUSD"}

# Leveraged / kaldıraçlı token etiketleri
_SKIP_TAGS = ("UP", "DOWN", "BULL", "BEAR")


def build_symbol_universe(
    tickers: list[dict],
    min_volume_usdt: float = 5_000_000.0,
    min_move_pct: float = 0.5,
) -> list[dict]:
    """
    Ticker listesinden tradable USDT sembollerini filtreler.

    Args:
        tickers: Binance ticker verisi listesi
        min_volume_usdt: Minimum 24h USDT hacmi
        min_move_pct: Minimum |priceChangePercent|

    Returns:
        Filtrelenmiş ticker listesi
    """
    universe = []
    for t in tickers:
        sym = t.get("symbol", "")
        if not sym.endswith("USDT"):
            continue

        base = sym.replace("USDT", "")
        if base in _SKIP_BASES:
            continue
        if any(tag in base for tag in _SKIP_TAGS):
            continue

        try:
            vol = float(t.get("quoteVolume", 0))
            change = abs(float(t.get("priceChangePercent", 0)))
        except (ValueError, TypeError):
            continue

        if vol >= min_volume_usdt and change >= min_move_pct:
            universe.append(t)

    logger.info(
        "Symbol universe: %d/%d sembol (vol>=%.0f, move>=%.1f%%)",
        len(universe), len(tickers), min_volume_usdt, min_move_pct,
    )
    return universe


def rank_symbols_by_activity(symbols: list[dict]) -> list[dict]:
    """
    Sembolleri hacim * hareket skoru ile sıralar (en aktif en üstte).
    """
    scored = []
    for s in symbols:
        try:
            vol = float(s.get("quoteVolume", 0))
            change = abs(float(s.get("priceChangePercent", 0)))
        except (ValueError, TypeError):
            vol, change = 0, 0
        scored.append((vol * change, s))
    scored.sort(key=lambda x: x[0], reverse=True)
    return [s for _, s in scored]


def exclude_bad_symbols(symbols: list[dict]) -> list[dict]:
    """
    İstenmeyen / sorunlu sembolleri eler.
    Şu an: stablecoin base ve leveraged token filtreleme.
    İleride: blacklist, delist uyarısı gibi filtreler eklenebilir.
    """
    result = []
    for s in symbols:
        sym = s.get("symbol", "")
        base = sym.replace("USDT", "")
        if base in _SKIP_BASES:
            continue
        if any(tag in base for tag in _SKIP_TAGS):
            continue
        result.append(s)
    return result
