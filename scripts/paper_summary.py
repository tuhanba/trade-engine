#!/usr/bin/env python3
"""Read-only paper-trading summary."""
from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def collect(limit: int = 5) -> dict:
    import database

    try:
        open_trades = database.get_open_trades("paper")
    except Exception:
        open_trades = []
    try:
        balance = database.get_paper_balance()
    except Exception:
        balance = 0.0

    closed_count = 0
    last_rows = []
    try:
        with database.get_conn() as conn:
            has_trades = conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name='trades'"
            ).fetchone()
            if has_trades:
                row = conn.execute(
                    "SELECT COUNT(*) FROM trades WHERE environment='paper' AND LOWER(status)='closed'"
                ).fetchone()
                closed_count = int(row[0] or 0)
                last_rows = conn.execute(
                    "SELECT id, symbol, direction, status, entry, close_price, net_pnl, open_time, close_time "
                    "FROM trades WHERE environment='paper' ORDER BY id DESC LIMIT ?",
                    (limit,),
                ).fetchall()
    except Exception:
        closed_count = 0
        last_rows = []

    return {
        "mode": "paper",
        "paper_balance": balance,
        "paper_trades_open": len(open_trades),
        "paper_trades_closed": closed_count,
        "open_trades": open_trades[:limit],
        "last_trades": [dict(r) for r in last_rows],
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--json", action="store_true")
    parser.add_argument("--limit", type=int, default=5)
    args = parser.parse_args(argv)
    logging.disable(logging.CRITICAL)
    data = collect(limit=args.limit)
    if args.json:
        print(json.dumps(data, indent=2, ensure_ascii=True))
    else:
        print(f"Paper balance: {data['paper_balance']:.2f}")
        print(f"Open paper trades: {data['paper_trades_open']}")
        print(f"Closed paper trades: {data['paper_trades_closed']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
