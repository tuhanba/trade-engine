"""P0-6 regression: execution gate re-asserts AI ALLOW + valid risk.

Directive Section 7.3: a trade opens ONLY when ai_decision==ALLOW and the risk
result is valid. core.execution_gate.execution_block_reason returns a reject
reason otherwise (None means "may open"). This is what makes the P0-4 fallback
(paper => WATCH) actually prevent execution.
"""
from core.execution_gate import execution_block_reason

_VALID_RISK = {"valid": True}


def test_allow_with_valid_risk_passes():
    assert execution_block_reason({"decision": "ALLOW"}, _VALID_RISK) is None


def test_watch_is_blocked():
    assert execution_block_reason({"decision": "WATCH"}, _VALID_RISK) == "ai_decision_watch"


def test_veto_is_blocked():
    assert execution_block_reason({"decision": "VETO"}, _VALID_RISK) == "ai_decision_veto"


def test_missing_decision_is_blocked():
    assert execution_block_reason({}, _VALID_RISK) == "ai_decision_missing"


def test_invalid_risk_is_blocked_even_when_allow():
    assert execution_block_reason({"decision": "ALLOW"}, {"valid": False}) == "risk_invalid_at_gate"
    assert execution_block_reason({"decision": "ALLOW"}, {}) == "risk_invalid_at_gate"


def test_ai_check_precedes_risk_check():
    # non-ALLOW reported as AI block regardless of risk validity
    assert execution_block_reason({"decision": "WATCH"}, {"valid": False}) == "ai_decision_watch"


def test_none_inputs_fail_closed():
    assert execution_block_reason(None, None) is not None
