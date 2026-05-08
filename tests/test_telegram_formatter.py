"""
Tests for core/telegram_formatter.py
Sahte 50 skor ve zorunlu alan regression testleri.
"""
import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest
from core.telegram_formatter import (
    format_trade_open,
    format_paper_open,
    format_tp_hit,
    format_trade_close,
)

BASE_SIGNAL = {
    "symbol": "BTCUSDT",
    "direction": "LONG",
    "entry": 68420.5,
    "stop_loss": 67980.25,
    "tp1": 68860.75,
    "tp2": 69301.0,
    "tp3": 69741.25,
    "rr": 2.0,
    "final_score": 82.0,
    "technical_score": 84.0,
    "ai_score": 80.5,
    "risk_score": 86.0,
    "ml_score": 78.0,
    "setup_quality": "A+",
    "score_source": "mixed",
    "score_confidence": 0.75,
    "execution_mode": "paper",
    "risk_percent": 1.5,
    "risk_amount": 7.5,
    "leverage_suggestion": 10,
    "reason": "Trend, momentum ve risk yapısı onaylandı.",
}


# ── format_trade_open ────────────────────────────────────────────────────────

def test_format_trade_open_contains_required_fields():
    msg = format_trade_open(BASE_SIGNAL)
    assert "AX SCALP SIGNAL" in msg
    assert "BTCUSDT" in msg
    assert "PAPER" in msg
    assert "LONG" in msg


def test_format_trade_open_shows_score_breakdown():
    msg = format_trade_open(BASE_SIGNAL)
    assert "Skor Dağılımı" in msg
    assert "Teknik" in msg
    assert "AI" in msg
    assert "Risk" in msg
    assert "ML" in msg


def test_format_trade_open_shows_correct_score():
    msg = format_trade_open(BASE_SIGNAL)
    assert "82/100" in msg


def test_format_trade_open_shows_score_source():
    msg = format_trade_open(BASE_SIGNAL)
    assert "mixed" in msg
    assert "75" in msg  # %75 güven


def test_format_trade_open_shows_tp_sl_entry():
    msg = format_trade_open(BASE_SIGNAL)
    assert "Entry" in msg
    assert "Stop" in msg
    assert "TP1" in msg
    assert "TP2" in msg
    assert "TP3" in msg
    assert "RR" in msg


def test_format_trade_open_shows_risk_leverage():
    msg = format_trade_open(BASE_SIGNAL)
    assert "Risk" in msg
    assert "Kaldıraç" in msg or "x10" in msg


def test_format_trade_open_shows_ai_reason():
    msg = format_trade_open(BASE_SIGNAL)
    assert "Not:" in msg
    assert "Trend" in msg


def test_format_trade_open_shows_utc_time():
    msg = format_trade_open(BASE_SIGNAL)
    assert "UTC" in msg


# ── static 50 regression ─────────────────────────────────────────────────────

def test_no_fake_50_when_score_is_none():
    """final_score=None olduğunda mesajda '50/100' OLMAMALI, 'N/A' olmalı."""
    sig = dict(BASE_SIGNAL)
    sig["final_score"] = None
    msg = format_trade_open(sig)
    assert "50/100" not in msg
    assert "N/A" in msg


def test_no_fake_50_for_minimal_signal():
    """Sadece temel alanlar varsa 50/100 basılmamalı."""
    sig = {
        "symbol": "ETHUSDT",
        "direction": "SHORT",
        "entry": 3000,
        "stop_loss": 3050,
        "tp1": 2950,
        "tp2": 2900,
        "tp3": 2850,
        "rr": 1.5,
        "execution_mode": "paper",
    }
    msg = format_trade_open(sig)
    assert "50/100" not in msg


def test_real_50_score_with_source_is_allowed():
    """final_score gerçekten 50 VE score_source varsa mesajda 50/100 görünebilir."""
    sig = dict(BASE_SIGNAL)
    sig["final_score"] = 50.0
    sig["score_source"] = "mixed"
    msg = format_trade_open(sig)
    assert "50/100" in msg


# ── LIVE badge ───────────────────────────────────────────────────────────────

def test_live_badge_when_execution_mode_live():
    sig = dict(BASE_SIGNAL)
    sig["execution_mode"] = "live"
    msg = format_trade_open(sig)
    assert "LIVE" in msg
    assert "PAPER" not in msg


# ── format_paper_open ────────────────────────────────────────────────────────

def test_format_paper_open_basic():
    msg = format_paper_open(BASE_SIGNAL)
    assert "PAPER OPENED" in msg
    assert "BTCUSDT" in msg
    assert "Entry" in msg


# ── format_tp_hit ─────────────────────────────────────────────────────────────

def test_format_tp_hit_tp1_includes_breakeven():
    msg = format_tp_hit("BTCUSDT", 1, 12.5, 0.7, 512.5)
    assert "TP1" in msg
    assert "Breakeven" in msg
    assert "BTCUSDT" in msg


def test_format_tp_hit_tp2_includes_runner():
    msg = format_tp_hit("BTCUSDT", 2, 25.0, 0.2, 537.5)
    assert "TP2" in msg
    assert "Runner" in msg or "Trailing" in msg


# ── format_trade_close ────────────────────────────────────────────────────────

def test_format_trade_close_win():
    msg = format_trade_close("BTCUSDT", 14.2, 0.5, "tp2", "42dk", "LONG", 1.42, 514.2)
    assert "CLOSED" in msg
    assert "BTCUSDT" in msg
    assert "+1.42R" in msg or "1.42R" in msg


def test_format_trade_close_loss():
    msg = format_trade_close("BTCUSDT", -7.5, 0.3, "sl", "18dk", "LONG", -1.0, 492.5)
    assert "STOPPED" in msg
    assert "Stop Loss" in msg
    assert "fakeout" in msg.lower() or "learning" in msg.lower()
