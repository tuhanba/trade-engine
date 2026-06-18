#!/usr/bin/env python3
"""Summarize recent signal decisions.

Read-only production diagnostic for the question: "Why are trades not opening?"
"""
from __future__ import annotations

import argparse
import json
import os
import sqlite3
import sys
from collections import Counter
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


FUNNEL_STAGES = [
    "SCANNED",
    "TREND_REJECTED",
    "TRIGGER_REJECTED",
    "REGIME_REJECTED",
    "RISK_REJECTED",
    "AI_VETO",
    "PENDING_APPROVAL",
    "ALLOW_BUT_EXECUTION_BLOCKED",
    "PAPER_OPENED",
    "LIVE_BLOCKED_BY_CONFIG",
    "LIVE_BLOCKED_BY_PROFIT_GATE",
    "LIVE_BLOCKED_BY_SAFETY_GATE",
    "LIVE_OPENED",
]


def _connect(db_path: str | None = None):
    if db_path:
        conn = sqlite3.connect(db_path)
        conn.row_factory = sqlite3.Row
        return conn

    try:
        import database

        return database.get_conn()
    except Exception:
        import config

        conn = sqlite3.connect(config.DB_PATH)
        conn.row_factory = sqlite3.Row
        return conn


def _event_columns(conn) -> dict[str, str]:
    cols = {row[1] for row in conn.execute("PRAGMA table_info(signal_events)").fetchall()}
    return {
        "stage": "stage" if "stage" in cols else "event_type",
        "reason": "reject_reason" if "reject_reason" in cols else "",
    }


def _live_block_bucket(reason: str) -> str | None:
    """Map a reject reason to a LIVE_BLOCKED_* bucket, or None.

    NEDEN (P0-5, directive Section 8): canli kapilarin (config / profit gate /
    safety gate) reddini ayri kovalara ayir. P0-1 safety gate'i
    reject_reason=LIVE_BLOCKED_BY_SAFETY_GATE ile loglar.
    """
    reason = (reason or "").lower()
    if "safety_gate" in reason:
        return "LIVE_BLOCKED_BY_SAFETY_GATE"
    if "profit_gate" in reason or "profit_readiness" in reason:
        return "LIVE_BLOCKED_BY_PROFIT_GATE"
    if "live_trading_disabled" in reason or "live_confirm" in reason or "dry_run" in reason:
        return "LIVE_BLOCKED_BY_CONFIG"
    return None


def normalize_stage(stage: str, reason: str = "", execution_mode: str = "paper") -> str:
    stage = (stage or "").upper()
    reason = (reason or "").lower()

    if stage in FUNNEL_STAGES:
        return stage
    if stage in {"AI_VETOED"}:
        return "AI_VETO"
    if stage in {"SKIPPED_BY_REGIME", "REGIME_BLOCKED"}:
        return "REGIME_REJECTED"
    if stage in {"AWAITING_APPROVAL", "PENDING_CONFIRMATION", "CONFIRMATION_PENDING"}:
        return "PENDING_APPROVAL"
    if stage in {"SKIPPED_BY_RISK"}:
        return "RISK_REJECTED"
    if stage in {"EXECUTION_REJECTED"}:
        return _live_block_bucket(reason) or "ALLOW_BUT_EXECUTION_BLOCKED"
    if stage in {"OPENED", "EXECUTED"}:
        return "LIVE_OPENED" if execution_mode == "live" else "PAPER_OPENED"
    if stage == "REJECTED":
        if "trend" in reason:
            return "TREND_REJECTED"
        if "trigger" in reason:
            return "TRIGGER_REJECTED"
        if "regime" in reason:
            return "REGIME_REJECTED"
        if "risk" in reason or "rr" in reason or "balance" in reason or "exposure" in reason:
            return "RISK_REJECTED"
        if "ai" in reason or "veto" in reason or "confidence" in reason:
            return "AI_VETO"
        return _live_block_bucket(reason) or "ALLOW_BUT_EXECUTION_BLOCKED"
    return stage or "UNKNOWN"


def collect(limit: int = 100, db_path: str | None = None) -> dict[str, Any]:
    empty = {
        "limit": limit,
        "total_candidates": 0,
        "summary": {stage: 0 for stage in FUNNEL_STAGES},
        "other": {},
        "recent": [],
    }
    execution_mode = os.environ.get("EXECUTION_MODE", "paper")

    try:
        with _connect(db_path) as conn:
            has_events = conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name='signal_events'"
            ).fetchone()
            if not has_events:
                return {**empty, "error": "signal_events table not found"}
            cols = _event_columns(conn)
            stage_col = cols["stage"]
            reason_col = cols["reason"]
            reason_expr = reason_col if reason_col else "''"
            rows = conn.execute(
                f"""
                SELECT signal_id, {stage_col} AS stage, {reason_expr} AS reject_reason,
                       symbol, created_at
                FROM signal_events
                ORDER BY id DESC
                LIMIT ?
                """,
                (max(limit * 6, limit),),
            ).fetchall()
    except Exception as exc:
        return {**empty, "error": str(exc)}

    seen = set()
    decisions = []
    for row in rows:
        signal_id = row["signal_id"] or f"{row['symbol']}:{row['created_at']}:{len(seen)}"
        if signal_id in seen:
            continue
        seen.add(signal_id)
        stage = normalize_stage(row["stage"], row["reject_reason"], execution_mode)
        decisions.append(
            {
                "signal_id": row["signal_id"],
                "symbol": row["symbol"],
                "stage": stage,
                "reason": row["reject_reason"] or "",
                "created_at": row["created_at"],
            }
        )
        if len(decisions) >= limit:
            break

    counts = Counter(d["stage"] for d in decisions)
    return {
        "limit": limit,
        "total_candidates": len(decisions),
        "summary": {stage: counts.get(stage, 0) for stage in FUNNEL_STAGES},
        "other": {stage: count for stage, count in counts.items() if stage not in FUNNEL_STAGES},
        "recent": decisions[:20],
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Explain why recent signals did or did not open trades.")
    parser.add_argument("--limit", type=int, default=100, help="Number of recent unique signal candidates.")
    parser.add_argument("--db", default=None, help="SQLite DB path. Defaults to config.DB_PATH.")
    parser.add_argument("--json", action="store_true", help="Emit JSON.")
    args = parser.parse_args(argv)

    result = collect(limit=args.limit, db_path=args.db)
    if args.json:
        print(json.dumps(result, indent=2, ensure_ascii=False))
        return 0

    print(f"Last {result['total_candidates']} candidates:")
    labels = {
        "SCANNED": "scanned only",
        "TREND_REJECTED": "trend rejected",
        "TRIGGER_REJECTED": "trigger rejected",
        "REGIME_REJECTED": "regime rejected",
        "RISK_REJECTED": "risk rejected",
        "AI_VETO": "AI veto",
        "PENDING_APPROVAL": "pending approval",
        "ALLOW_BUT_EXECUTION_BLOCKED": "execution blocked",
        "PAPER_OPENED": "paper opened",
        "LIVE_BLOCKED_BY_CONFIG": "live blocked by config",
        "LIVE_BLOCKED_BY_PROFIT_GATE": "live blocked by profit gate",
        "LIVE_BLOCKED_BY_SAFETY_GATE": "live blocked by safety gate",
        "LIVE_OPENED": "live opened",
    }
    for stage in FUNNEL_STAGES:
        print(f"- {result['summary'][stage]} {labels[stage]}")
    for stage, count in result["other"].items():
        print(f"- {count} {stage.lower()}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
