"""core/execution_gate.py — P0-6: explicit execution-gate re-assertions.

Directive Section 7.3: a trade may open ONLY when the AI decision is ALLOW and
the risk result is valid. These are re-checked at the open point (not merely
upstream), so an inconsistent state — or a WATCH/VETO that slipped through —
can never open a trade. WATCH signals are still paper-tracked elsewhere; they
just do not OPEN. This is what makes the P0-4 fallback (paper => WATCH)
actually prevent execution.
"""
from __future__ import annotations

from typing import Optional


def execution_block_reason(decision: dict, risk_result: dict) -> Optional[str]:
    """Return a reject reason if execution must be blocked, else None.

    - AI decision must be exactly ALLOW (WATCH / VETO / missing => block).
    - Risk result must be valid (truthy ``valid``).
    """
    d = str((decision or {}).get("decision", "")).upper()
    if d != "ALLOW":
        return f"ai_decision_{d.lower() or 'missing'}"
    if not (risk_result or {}).get("valid"):
        return "risk_invalid_at_gate"
    return None
