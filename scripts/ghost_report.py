#!/usr/bin/env python3
"""Read-only Ghost Learning report."""
from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def collect() -> dict:
    import database

    stats = database.get_ghost_stats()
    pending_suggestions = 0
    try:
        with database.get_conn() as conn:
            pending_suggestions = conn.execute(
                "SELECT COUNT(*) FROM ghost_suggestions WHERE applied=0"
            ).fetchone()[0] or 0
    except Exception:
        pending_suggestions = 0

    samples = int(stats.get("ghost_total", 0) or 0)
    recommendation = "do_not_change_threshold_yet"
    if samples >= 30 and pending_suggestions:
        recommendation = "review_coin_setup_suggestions_manually"

    return {
        "mode": "report_only",
        "samples": samples,
        "pending_suggestions": int(pending_suggestions),
        "stats": stats,
        "recommendation": recommendation,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    logging.disable(logging.CRITICAL)
    data = collect()
    if args.json:
        print(json.dumps(data, indent=2, ensure_ascii=True))
    else:
        print(f"Ghost Learning mode: {data['mode']}")
        print(f"Samples: {data['samples']}")
        print(f"Recommendation: {data['recommendation']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
