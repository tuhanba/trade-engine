#!/usr/bin/env python3
"""Safe JSON operations report for Friday/Gemini."""
from __future__ import annotations

import argparse
import json
import logging
import os
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def collect(limit: int = 100) -> dict:
    from core.ml_signal_scorer import get_scorer
    from scripts.ghost_report import collect as collect_ghost
    from scripts.live_readiness import collect as collect_live_readiness
    from scripts.paper_summary import collect as collect_paper
    from scripts.system_health import collect as collect_health
    from scripts.why_no_trade import collect as collect_why

    health = collect_health()
    paper = collect_paper()
    why = collect_why(limit=limit)
    ml = get_scorer().get_status()
    ghost = collect_ghost()
    readiness = collect_live_readiness()

    risks = []
    mode = os.environ.get("EXECUTION_MODE", "paper")
    live_enabled = os.environ.get("LIVE_TRADING_ENABLED", "false").lower() == "true"
    dry_run = os.environ.get("DRY_RUN", "true").lower() == "true"
    if mode == "live":
        risks.append("execution_mode_live")
    if live_enabled:
        risks.append("live_trading_enabled")
    if not dry_run:
        risks.append("dry_run_disabled")
    if not health.get("bot_alive"):
        risks.append("bot_not_alive")

    next_actions = []
    if why.get("summary", {}).get("RISK_REJECTED", 0):
        next_actions.append("review_risk_reject_reasons")
    if not ml.get("ready"):
        next_actions.append("collect_more_validated_samples_for_ml")
    if readiness.get("status") != "PASS":
        next_actions.append("keep_live_trading_disabled")

    return {
        "mode": mode,
        "bot_alive": health.get("bot_alive", False),
        "dashboard_alive": health.get("dashboard_alive", False),
        "telegram_alive": health.get("telegram_alive", False),
        "last_scan_time": health.get("last_scan_time"),
        "symbols_scanned": health.get("symbols_scanned", 0),
        "paper_trades_open": paper.get("paper_trades_open", 0),
        "why_no_trade_summary": {
            "trend_rejected": why["summary"].get("TREND_REJECTED", 0),
            "trigger_rejected": why["summary"].get("TRIGGER_REJECTED", 0),
            "risk_rejected": why["summary"].get("RISK_REJECTED", 0),
            "ai_veto": why["summary"].get("AI_VETO", 0),
            "execution_blocked": why["summary"].get("ALLOW_BUT_EXECUTION_BLOCKED", 0),
            "paper_opened": why["summary"].get("PAPER_OPENED", 0),
        },
        "ml_status": {
            "ready": ml.get("ready", False),
            "reason": ml.get("reason", "unknown"),
            "fallback": ml.get("fallback"),
            "n_samples": ml.get("n_samples", 0),
            "min_samples": ml.get("min_samples", 0),
        },
        "ghost_learning": {
            "mode": ghost.get("mode", "report_only"),
            "samples": ghost.get("samples", 0),
            "recommendation": ghost.get("recommendation", "do_not_change_threshold_yet"),
        },
        "live_readiness": readiness.get("status", "FAIL"),
        "risks": risks,
        "next_actions": next_actions,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--limit", type=int, default=100)
    args = parser.parse_args(argv)
    logging.disable(logging.CRITICAL)
    report = collect(limit=args.limit)
    if args.json:
        print(json.dumps(report, indent=2, ensure_ascii=True))
    else:
        print(f"Mode: {report['mode']}")
        print(f"Bot alive: {report['bot_alive']}")
        print(f"Paper trades open: {report['paper_trades_open']}")
        print(f"Live readiness: {report['live_readiness']}")
        print(f"Next actions: {', '.join(report['next_actions']) or 'none'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
