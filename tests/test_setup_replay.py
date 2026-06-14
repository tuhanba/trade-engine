"""
tests/test_setup_replay.py — Faz 6.2 Setup Replay ('bu trade neden açıldı').
"""
import json

import pytest

import dashboard_service


def _insert_trade(db, metadata: dict, **cols):
    base = dict(symbol="SOLUSDT", direction="LONG", status="closed", entry=100.0,
                sl=99.0, tp1=102.0, final_score=68, setup_quality="A",
                market_regime="TRENDING_HIGH_VOL", net_pnl=31.4, close_reason="tp2",
                environment="paper")
    base.update(cols)
    keys = ",".join(base.keys()) + ",metadata"
    qs = ",".join(["?"] * (len(base) + 1))
    with db.get_conn() as conn:
        cur = conn.execute(
            f"INSERT INTO trades ({keys}) VALUES ({qs})",
            (*base.values(), json.dumps(metadata)),
        )
        return cur.lastrowid


def test_setup_replay_extracts_indicators(test_db):
    tid = _insert_trade(test_db, {
        "reason": "CVD divergence + trend align",
        "adx": 28.5, "rsi5": 62.0, "cvd_value": 1.4, "oi_change_pct": 3.2,
    })
    r = dashboard_service.get_trade_setup_replay(tid)
    assert r["found"] is True
    assert r["symbol"] == "SOLUSDT"
    assert r["rationale"] == "CVD divergence + trend align"
    assert r["indicators"]["ADX"] == 28.5
    assert r["indicators"]["RSI 5m"] == 62.0
    assert r["indicators"]["CVD"] == 1.4
    assert r["score"] == 68.0
    assert r["market_regime"] == "TRENDING_HIGH_VOL"


def test_setup_replay_trigger_type_fallback(test_db):
    tid = _insert_trade(test_db, {"trigger_type": "breakout", "adx": 20.0})
    r = dashboard_service.get_trade_setup_replay(tid)
    assert r["rationale"] == "breakout"
    assert r["indicators"]["ADX"] == 20.0


def test_setup_replay_no_metadata(test_db):
    tid = _insert_trade(test_db, {})
    r = dashboard_service.get_trade_setup_replay(tid)
    assert r["found"] is True
    assert r["indicators"] == {}
    assert r["rationale"] == "—"


def test_setup_replay_missing_trade(test_db):
    r = dashboard_service.get_trade_setup_replay(999999)
    assert r["found"] is False
