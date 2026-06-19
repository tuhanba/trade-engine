"""P0-5 regression: why_no_trade funnel exposes all directive Section 8 buckets.

Previously missing: REGIME_REJECTED, PENDING_APPROVAL, LIVE_BLOCKED_BY_PROFIT_GATE,
LIVE_BLOCKED_BY_SAFETY_GATE. The safety-gate bucket must also catch the
reject_reason emitted by the P0-1 live gate (LIVE_BLOCKED_BY_SAFETY_GATE).
"""
from scripts.why_no_trade import FUNNEL_STAGES, normalize_stage, collect


def test_new_buckets_present():
    for b in ("REGIME_REJECTED", "PENDING_APPROVAL",
              "LIVE_BLOCKED_BY_PROFIT_GATE", "LIVE_BLOCKED_BY_SAFETY_GATE"):
        assert b in FUNNEL_STAGES


def test_normalize_direct_stage_names():
    assert normalize_stage("REGIME_REJECTED") == "REGIME_REJECTED"
    assert normalize_stage("PENDING_APPROVAL") == "PENDING_APPROVAL"
    assert normalize_stage("LIVE_BLOCKED_BY_PROFIT_GATE") == "LIVE_BLOCKED_BY_PROFIT_GATE"
    assert normalize_stage("LIVE_BLOCKED_BY_SAFETY_GATE") == "LIVE_BLOCKED_BY_SAFETY_GATE"


def test_normalize_aliases():
    assert normalize_stage("AWAITING_APPROVAL") == "PENDING_APPROVAL"
    assert normalize_stage("SKIPPED_BY_REGIME") == "REGIME_REJECTED"


def test_execution_rejected_splits_into_live_buckets():
    # P0-1 live gate logs EXECUTION_REJECTED + reject_reason=LIVE_BLOCKED_BY_SAFETY_GATE
    assert normalize_stage("EXECUTION_REJECTED", "LIVE_BLOCKED_BY_SAFETY_GATE") == "LIVE_BLOCKED_BY_SAFETY_GATE"
    assert normalize_stage("EXECUTION_REJECTED", "profit_gate_fail") == "LIVE_BLOCKED_BY_PROFIT_GATE"
    assert normalize_stage("EXECUTION_REJECTED", "live_confirm_required") == "LIVE_BLOCKED_BY_CONFIG"
    # generic execution reject stays generic
    assert normalize_stage("EXECUTION_REJECTED", "spread_too_high") == "ALLOW_BUT_EXECUTION_BLOCKED"


def test_rejected_reason_routing():
    assert normalize_stage("REJECTED", "regime not aligned") == "REGIME_REJECTED"
    assert normalize_stage("REJECTED", "weak trend") == "TREND_REJECTED"


def test_collect_counts_new_buckets(test_db):
    test_db.save_signal_event("s1", "REGIME_REJECTED", symbol="BTCUSDT", reject_reason="regime_bearish")
    test_db.save_signal_event("s2", "PENDING_APPROVAL", symbol="ETHUSDT", reason="awaiting_confirm")
    test_db.save_signal_event("s3", "EXECUTION_REJECTED", symbol="SOLUSDT",
                              reject_reason="LIVE_BLOCKED_BY_SAFETY_GATE")
    res = collect(limit=50)
    assert res["summary"]["REGIME_REJECTED"] == 1
    assert res["summary"]["PENDING_APPROVAL"] == 1
    assert res["summary"]["LIVE_BLOCKED_BY_SAFETY_GATE"] == 1
