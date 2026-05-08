"""
Tests for core/score_engine.py
Sabit 50 skor problemi regression testleri dahil.
"""
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest
from core.score_engine import (
    clamp_score,
    score_bar,
    score_grade,
    cold_start_score,
    compute_final_score,
    normalize_signal_scores,
)


# ── clamp_score ──────────────────────────────────────────────────────────────

def test_clamp_score_bounds():
    assert clamp_score(150) == 100.0
    assert clamp_score(-10) == 0.0
    assert clamp_score(55) == 55.0


def test_clamp_score_invalid():
    assert clamp_score(None) == 0.0
    assert clamp_score("abc") == 0.0


# ── score_bar ────────────────────────────────────────────────────────────────

def test_score_bar_none_shows_na():
    bar = score_bar(None)
    assert "N/A" in bar
    assert "50" not in bar


def test_score_bar_known_score():
    bar = score_bar(80)
    assert "80/100" in bar
    assert "░" in bar or "█" in bar


# ── score_grade ──────────────────────────────────────────────────────────────

def test_score_grade_tiers():
    assert score_grade(92) == "S"
    assert score_grade(85) == "A+"
    assert score_grade(78) == "A"
    assert score_grade(67) == "B"
    assert score_grade(52) == "C"
    assert score_grade(30) == "D"
    assert score_grade(None) == "N/A"


# ── cold_start_score ─────────────────────────────────────────────────────────

STRONG_SIGNAL = {
    "symbol": "BTCUSDT",
    "direction": "LONG",
    "adx15": 36,
    "rv": 2.4,
    "rsi5": 55,
    "rsi1": 58,
    "bb_width": 3.2,
    "bb_width_chg": 0.7,
    "momentum_3c": 1.8,
    "btc_trend": "BULLISH",
    "rr": 2.0,
}

WEAK_SIGNAL = {
    "symbol": "XYZUSDT",
    "direction": "LONG",
    "adx15": 12,
    "rv": 0.5,
    "rsi5": 82,
    "rsi1": 90,
    "bb_width": 8.0,
    "bb_width_chg": -0.6,
    "momentum_3c": -1.0,
    "btc_trend": "BEARISH",
    "rr": 1.0,
}

def test_cold_start_strong_signal_above_70():
    score = cold_start_score(STRONG_SIGNAL)
    assert score > 70, f"Güçlü sinyal için cold_start_score={score} > 70 olmalı"


def test_cold_start_weak_signal_below_45():
    score = cold_start_score(WEAK_SIGNAL)
    assert score < 45, f"Zayıf sinyal için cold_start_score={score} < 45 olmalı"


def test_cold_start_never_returns_static_50_for_strong():
    score = cold_start_score(STRONG_SIGNAL)
    assert score != 50, "Güçlü sinyal için cold_start_score 50 dönmemeli"


def test_cold_start_empty_signal_below_60():
    score = cold_start_score({})
    assert 0 <= score <= 60, f"Boş sinyalde skor makul aralıkta olmalı, got {score}"


def test_cold_start_short_direction():
    sig = dict(STRONG_SIGNAL)
    sig["direction"] = "SHORT"
    sig["btc_trend"] = "BEARISH"
    sig["momentum_3c"] = -2.0
    score = cold_start_score(sig)
    assert score > 70, f"Güçlü SHORT sinyal için cold_start_score={score} > 70 olmalı"


# ── compute_final_score ──────────────────────────────────────────────────────

def test_compute_final_score_with_ml():
    result = compute_final_score(
        technical_score=84.0,
        risk_score=86.0,
        ai_score=80.0,
        ml_score=78.0,
    )
    assert result["score_source"] == "mixed"
    assert result["score_confidence"] == 0.75
    assert result["final_score"] > 0
    assert result["ml_score"] == 78.0
    assert result["cold_start_score"] is None


def test_compute_final_score_without_ml_uses_cold_start():
    result = compute_final_score(
        technical_score=84.0,
        risk_score=86.0,
        ai_score=80.0,
        ml_score=None,
        fallback_signal=STRONG_SIGNAL,
    )
    assert result["score_source"] == "cold_start"
    assert result["score_confidence"] == 0.45
    assert result["ml_score"] is None
    assert result["cold_start_score"] is not None
    # cold_start değeri kör 50 olmamalı (güçlü sinyal)
    assert result["cold_start_score"] != 50


def test_compute_final_score_clamp():
    result = compute_final_score(
        technical_score=200,
        risk_score=200,
        ai_score=200,
        ml_score=200,
    )
    assert result["final_score"] <= 100.0


# ── normalize_signal_scores ──────────────────────────────────────────────────

def test_normalize_missing_final_score():
    sig = dict(STRONG_SIGNAL)
    sig.pop("final_score", None)
    result = normalize_signal_scores(sig)
    assert result.get("final_score") is not None
    assert result.get("score_source") in ("cold_start", "mixed")


def test_normalize_suspicious_static_50():
    """final_score=50 ama score_source yok → recompute edilmeli"""
    sig = {
        "symbol": "BTCUSDT",
        "direction": "LONG",
        "final_score": 50,
        **STRONG_SIGNAL,
    }
    # score_source kasıtlı olarak yok
    sig.pop("score_source", None)
    result = normalize_signal_scores(sig)
    # Güçlü sinyal için recompute sonrası 50 olmamalı
    assert result["score_source"] != ""
    assert result["final_score"] != 50.0 or result.get("cold_start_score") is not None


def test_normalize_preserves_valid_score():
    """score_source mevcut olan final_score recompute edilmemeli"""
    sig = {
        "symbol": "BTCUSDT",
        "direction": "LONG",
        "final_score": 77.5,
        "score_source": "mixed",
        "score_confidence": 0.75,
    }
    result = normalize_signal_scores(sig)
    assert result["final_score"] == 77.5
    assert result["score_source"] == "mixed"
