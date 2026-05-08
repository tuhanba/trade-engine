"""
Score Engine — AX Scalp Engine
================================
Merkezi skor hesaplama modülü.
Tüm skor kaynakları bu modülden geçer; sistem hiçbir yerde kör 50 üretemez.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional


def clamp_score(value: float, lo: float = 0.0, hi: float = 100.0) -> float:
    try:
        return max(lo, min(hi, float(value)))
    except Exception:
        return 0.0


def score_bar(score: Optional[float]) -> str:
    if score is None:
        return "░░░░░░░░░░ N/A"
    s = int(clamp_score(score))
    filled = max(0, min(10, round(s / 10)))
    return "█" * filled + "░" * (10 - filled) + f" {s}/100"


def score_grade(score: Optional[float]) -> str:
    if score is None:
        return "N/A"
    s = clamp_score(score)
    if s >= 90:
        return "S"
    if s >= 82:
        return "A+"
    if s >= 75:
        return "A"
    if s >= 65:
        return "B"
    if s >= 50:
        return "C"
    return "D"


def cold_start_score(signal: Dict[str, Any]) -> int:
    """
    ML modeli eğitilmemiş veya hata aldığında sabit 50 yerine
    teknik veriden dinamik skor üretir.
    """
    score = 45.0
    bonuses: List[str] = []
    penalties: List[str] = []

    adx = float(signal.get("adx15", signal.get("adx", 0)) or 0)
    rv = float(signal.get("rv", signal.get("relative_volume", 0)) or 0)
    rsi5 = float(signal.get("rsi5", 50) or 50)
    rsi1 = float(signal.get("rsi1", 50) or 50)
    bb_width = float(signal.get("bb_width", signal.get("bb_width_pct", 0)) or 0)
    bb_chg = float(signal.get("bb_width_chg", 0) or 0)
    momentum = float(signal.get("momentum_3c", 0) or 0)
    direction = str(signal.get("direction", "LONG")).upper()
    btc_trend = str(signal.get("btc_trend", "NEUTRAL")).upper()
    rr = float(signal.get("rr", 0) or 0)

    # Trend gücü
    if adx >= 35:
        score += 14
        bonuses.append("ADX strong")
    elif adx >= 28:
        score += 10
        bonuses.append("ADX valid")
    elif adx >= 22:
        score += 4
    elif adx > 0:
        score -= 8
        penalties.append("weak ADX")

    # Hacim
    if rv >= 2.0:
        score += 10
        bonuses.append("relative volume spike")
    elif rv >= 1.4:
        score += 5
    elif 0 < rv < 0.8:
        score -= 6
        penalties.append("low volume")

    # Volatilite / band genişliği
    if 2.0 <= bb_width <= 5.5:
        score += 7
        bonuses.append("healthy volatility")
    elif bb_width > 7.0:
        score -= 5
        penalties.append("overextended volatility")

    if bb_chg >= 0.5:
        score += 7
        bonuses.append("band expansion")
    elif bb_chg <= -0.5:
        score -= 4
        penalties.append("band contraction")

    # Momentum uyumu
    if direction == "LONG" and momentum > 1.0:
        score += 8
        bonuses.append("LONG momentum aligned")
    elif direction == "SHORT" and momentum < -1.0:
        score += 8
        bonuses.append("SHORT momentum aligned")
    elif abs(momentum) < 0.25:
        score -= 3
        penalties.append("flat momentum")

    # RSI aşırılık kontrolü
    if rsi5 >= 78 or rsi5 <= 22 or rsi1 >= 85 or rsi1 <= 15:
        score -= 6
        penalties.append("RSI extreme")
    elif 35 <= rsi5 <= 65:
        score += 3

    # BTC trend uyumu
    if direction == "LONG" and btc_trend == "BULLISH":
        score += 5
        bonuses.append("BTC trend aligned")
    elif direction == "SHORT" and btc_trend == "BEARISH":
        score += 5
        bonuses.append("BTC trend aligned")
    elif btc_trend != "NEUTRAL":
        score -= 5
        penalties.append("BTC trend conflict")

    # RR kalitesi
    if rr >= 2.0:
        score += 5
        bonuses.append("RR >= 2")
    elif 0 < rr < 1.5:
        score -= 10
        penalties.append("low RR")

    return int(round(clamp_score(score)))


def compute_final_score(
    technical_score: Optional[float],
    risk_score: Optional[float],
    ai_score: Optional[float],
    ml_score: Optional[float],
    fallback_signal: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    """
    Tüm kaynaklardan ağırlıklı final skoru hesaplar.
    ML model yoksa cold_start_score() devreye girer.
    """
    tech = clamp_score(technical_score if technical_score is not None else 50)
    risk = clamp_score(risk_score if risk_score is not None else 50)
    ai = clamp_score(ai_score if ai_score is not None else 50)

    if ml_score is None:
        ml_component = cold_start_score(fallback_signal or {})
        source = "cold_start"
        confidence = 0.45
    else:
        ml_component = clamp_score(ml_score)
        source = "mixed"
        confidence = 0.75

    final = (
        tech * 0.35
        + risk * 0.25
        + ai * 0.25
        + ml_component * 0.15
    )

    return {
        "technical_score": round(tech, 1),
        "risk_score": round(risk, 1),
        "ai_score": round(ai, 1),
        "ml_score": None if ml_score is None else round(ml_component, 1),
        "cold_start_score": round(ml_component, 1) if ml_score is None else None,
        "final_score": round(clamp_score(final), 1),
        "setup_quality": score_grade(final),
        "score_source": source,
        "score_confidence": confidence,
    }


def normalize_signal_scores(signal: dict) -> dict:
    """
    Sinyal sözlüğüne final_score yoksa veya kaynak bilinmiyorsa
    compute_final_score() ile yeniden hesaplar.
    Telegram/Dashboard/Paper akışında çağrılmalıdır.
    """
    raw_fs = signal.get("final_score")
    try:
        fs_val = float(raw_fs) if raw_fs is not None else None
    except (TypeError, ValueError):
        fs_val = None

    # final_score=50 ama score_source yoksa büyük ihtimal kör fallback
    suspicious_static_50 = (
        fs_val == 50.0
        and not signal.get("score_source")
    )

    if fs_val is None or suspicious_static_50:
        technical = signal.get("technical_score", signal.get("score"))
        risk = signal.get("risk_score")
        ai = signal.get("ai_score")
        ml = signal.get("ml_score") if signal.get("ml_score") not in (None, 50) else None

        scores = compute_final_score(
            technical_score=float(technical) if technical is not None else None,
            risk_score=float(risk) if risk is not None else None,
            ai_score=float(ai) if ai is not None else None,
            ml_score=float(ml) if ml is not None else None,
            fallback_signal=signal,
        )
        signal.update(scores)

    return signal
