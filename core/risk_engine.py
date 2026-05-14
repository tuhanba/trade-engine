"""
core/risk_engine.py – Sinyal risk filtresi.

Trade açma kararını risk parametreleri bazında değerlendirir.
Max open trades, duplicate symbol, RR, leverage gibi kontrolleri yapar.
"""

from __future__ import annotations

import logging
from typing import Any

import config
from core.data_layer import SignalData, SignalDecision
from core.accounting import calculate_rr

logger = logging.getLogger("ax.risk_engine")


def evaluate_signal_risk(
    signal: SignalData,
    open_trades: list[dict],
    balance: float,
) -> dict[str, Any]:
    """
    Sinyalin risk durumunu değerlendirir.

    Returns:
        {
            "decision": "ALLOW" | "WATCH" | "VETO" | "SKIPPED_BY_RISK" | "SKIPPED_BY_FILTER",
            "reason": str,
            "confidence": float (0.0 – 1.0)
        }
    """
    # 1. Max open trades kontrolü
    if len(open_trades) >= config.MAX_OPEN_TRADES:
        return {
            "decision": SignalDecision.SKIPPED_BY_RISK.value,
            "reason": f"Max açık trade ({config.MAX_OPEN_TRADES}) aşıldı",
            "confidence": 1.0,
        }

    # 2. Duplicate symbol kontrolü
    open_symbols = {t.get("symbol", "") for t in open_trades}
    if signal.symbol in open_symbols:
        return {
            "decision": SignalDecision.SKIPPED_BY_RISK.value,
            "reason": f"{signal.symbol} zaten açık",
            "confidence": 1.0,
        }

    # 3. Entry price kontrolü
    if signal.entry_price <= 0:
        return {
            "decision": SignalDecision.VETO.value,
            "reason": "Entry price sıfır veya negatif",
            "confidence": 1.0,
        }

    # 4. Stop loss kontrolü
    if signal.stop_loss <= 0:
        return {
            "decision": SignalDecision.VETO.value,
            "reason": "Stop loss sıfır veya negatif",
            "confidence": 1.0,
        }

    # 5. Stop distance kontrolü
    stop_distance = abs(signal.entry_price - signal.stop_loss)
    if stop_distance <= 0:
        return {
            "decision": SignalDecision.VETO.value,
            "reason": "Stop distance sıfır",
            "confidence": 1.0,
        }

    # 6. TP kontrolü
    tp1 = signal.tp1 or 0
    if tp1 <= 0:
        return {
            "decision": SignalDecision.SKIPPED_BY_FILTER.value,
            "reason": "TP1 tanımlı değil",
            "confidence": 0.8,
        }

    # 7. RR kontrolü – minimum 1.5
    rr = calculate_rr(signal.entry_price, signal.stop_loss, tp1)
    if rr < 1.5:
        return {
            "decision": SignalDecision.SKIPPED_BY_RISK.value,
            "reason": f"RR ({rr}) minimum 1.5 altında",
            "confidence": 0.9,
        }

    # 8. Risk pct limit kontrolü
    if signal.risk_pct > config.RISK_PCT * 3:
        return {
            "decision": SignalDecision.VETO.value,
            "reason": f"Risk% ({signal.risk_pct}) çok yüksek",
            "confidence": 0.95,
        }

    # 9. Leverage limit kontrolü
    if signal.leverage > config.MAX_LEVERAGE:
        return {
            "decision": SignalDecision.SKIPPED_BY_RISK.value,
            "reason": f"Leverage ({signal.leverage}) > max ({config.MAX_LEVERAGE})",
            "confidence": 0.9,
        }

    # Tüm kontroller geçti
    confidence = min(0.6 + rr * 0.1, 0.95)
    return {
        "decision": SignalDecision.ALLOW.value,
        "reason": f"Risk kontrolleri OK (RR={rr})",
        "confidence": round(confidence, 2),
    }


def should_open_trade(
    signal: SignalData,
    open_trades: list[dict],
    balance: float,
) -> tuple[bool, str, str]:
    """
    Trade açılıp açılamayacağını kontrol eder.

    Returns:
        (can_open: bool, decision: str, reason: str)
    """
    result = evaluate_signal_risk(signal, open_trades, balance)
    decision = result["decision"]
    reason = result["reason"]
    can_open = decision == SignalDecision.ALLOW.value

    logger.info(
        "Risk değerlendirmesi: %s %s → %s – %s",
        signal.symbol, signal.side, decision, reason,
    )

    return can_open, decision, reason
