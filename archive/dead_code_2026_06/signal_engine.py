"""
core/signal_engine.py – Basit sinyal üretim iskeleti.

Agresif sinyal üretmez. Score düşükse trade açılmaz ama
signal_candidates olarak kaydedilir. İleri strateji sonra eklenir.
"""

from __future__ import annotations
import logging
from typing import Optional
from core.data_layer import SignalData

logger = logging.getLogger("ax.signal_engine")


def generate_signal(
    symbol: str,
    market_context: dict,
) -> Optional[SignalData]:
    """
    Basit momentum + volume tabanlı sinyal üretir.

    market_context beklenen alanlar:
      - last_price (float)
      - price_change_pct (float)
      - volume_usdt (float)
      - high_24h (float)
      - low_24h (float)

    Returns: SignalData veya None (sinyal yoksa)
    """
    try:
        last = float(market_context.get("last_price", 0))
        change = float(market_context.get("price_change_pct", 0))
        high = float(market_context.get("high_24h", 0))
        low = float(market_context.get("low_24h", 0))
    except (ValueError, TypeError):
        return None

    if last <= 0 or high <= 0 or low <= 0:
        return None

    # ATR yaklaşık hesabı
    atr = high - low
    if atr <= 0:
        return None

    atr_pct = (atr / last) * 100

    # Trend yönü belirleme
    if change > 1.0:
        side = "LONG"
    elif change < -1.0:
        side = "SHORT"
    else:
        return None  # Yeterli hareket yok

    # Score hesaplama (0-100)
    score = min(abs(change) * 10, 50) + min(atr_pct * 5, 50)
    score = round(score, 1)

    # Entry, SL, TP belirleme
    sl_distance = atr * 0.5

    if side == "LONG":
        entry_price = last
        stop_loss = entry_price - sl_distance
        tp1 = entry_price + sl_distance * 1.5
        tp2 = entry_price + sl_distance * 2.5
        tp3 = entry_price + sl_distance * 4.0
    else:
        entry_price = last
        stop_loss = entry_price + sl_distance
        tp1 = entry_price - sl_distance * 1.5
        tp2 = entry_price - sl_distance * 2.5
        tp3 = entry_price - sl_distance * 4.0

    signal = SignalData(
        symbol=symbol,
        side=side,
        entry_price=round(entry_price, 6),
        stop_loss=round(stop_loss, 6),
        tp1=round(tp1, 6),
        tp2=round(tp2, 6),
        tp3=round(tp3, 6),
        score=score,
        leverage=5,
        risk_pct=1.0,
        reason=f"momentum {change:+.1f}% atr {atr_pct:.1f}%",
        source="signal_engine_v1",
    )

    logger.info(
        "Sinyal: %s %s @ %.4f  score=%.1f",
        symbol, side, entry_price, score,
    )
    return signal
