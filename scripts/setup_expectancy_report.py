#!/usr/bin/env python3
"""scripts/setup_expectancy_report.py — per-setup expectancy (directive Section 11).

Read-only. Groups closed paper trades by setup_type and reports per-setup
metrics + a recommendation (ENABLE / PAPER_ONLY / DISABLE / LOWER_RISK /
NEEDS_MORE_DATA). Supports --json. Friday and the dashboard can consume this.

    python scripts/setup_expectancy_report.py --json
    python scripts/setup_expectancy_report.py --days 30
"""
from __future__ import annotations

import argparse
import json
import sys
from collections import defaultdict
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts.profit_readiness import _r, _pnl, _f, _setup, _load_trades  # noqa: E402

MIN_SAMPLE = 30
STRONG_SAMPLE = 100


def _mean(xs: list[float]) -> float:
    return sum(xs) / len(xs) if xs else 0.0


def _rate(count: int, n: int) -> float:
    return round(count / n, 4) if n else 0.0


def _top_bucket(trades: list[dict], key_fn, best: bool) -> str:
    agg: dict[str, float] = defaultdict(float)
    for t in trades:
        agg[str(key_fn(t))] += _pnl(t)
    if not agg:
        return ""
    return (max if best else min)(agg.items(), key=lambda kv: kv[1])[0]


def _recommend(m: dict) -> str:
    n = m["sample_size"]
    e = m["expectancy_r"]
    pf = m["profit_factor"]
    pf_num = float("inf") if pf == "inf" else float(pf)
    if n < MIN_SAMPLE:
        return "NEEDS_MORE_DATA"
    if e <= 0 or pf_num < 1.0:
        return "DISABLE"
    if e >= 0.15 and pf_num >= 1.5 and n >= STRONG_SAMPLE:
        return "ENABLE"
    if pf_num < 1.25 or e < 0.10:
        return "LOWER_RISK"
    return "PAPER_ONLY"


def compute_by_setup(trades: list[dict]) -> dict:
    """Pure: per-setup metrics + recommendation."""
    groups: dict[str, list[dict]] = defaultdict(list)
    for t in trades:
        groups[_setup(t)].append(t)

    report: dict[str, Any] = {}
    for setup, ts in sorted(groups.items()):
        n = len(ts)
        wins = [t for t in ts if _r(t) > 0]
        losses = [t for t in ts if _r(t) <= 0]
        wr = len(wins) / n if n else 0.0
        avg_win = _mean([_r(t) for t in wins])
        avg_loss = _mean([_r(t) for t in losses])
        expectancy = wr * avg_win + (1 - wr) * avg_loss
        gw = sum(_pnl(t) for t in wins)
        gl = abs(sum(_pnl(t) for t in losses))
        pf = (gw / gl) if gl > 0 else (float("inf") if gw > 0 else 0.0)

        crs = [str(t.get("close_reason") or "").upper() for t in ts]
        tp_hits = sum(1 for c in crs if "TP" in c)
        stop_first = sum(1 for c in crs if "SL" in c or "STOP" in c)

        m: dict[str, Any] = {
            "setup_type": setup,
            "sample_size": n,
            "win_rate": round(wr, 4),
            "avg_win_r": round(avg_win, 4),
            "avg_loss_r": round(avg_loss, 4),
            "expectancy_r": round(expectancy, 4),
            "profit_factor": ("inf" if pf == float("inf") else round(pf, 4)),
            "net_pnl": round(sum(_pnl(t) for t in ts), 4),
            "avg_mfe": round(_mean([_f(t.get("mfe")) for t in ts]), 4),
            "avg_mae": round(_mean([_f(t.get("mae")) for t in ts]), 4),
            "tp_hit_rate": _rate(tp_hits, n),
            "stop_first_rate": _rate(stop_first, n),
            "best_symbol": _top_bucket(ts, lambda t: t.get("symbol") or "?", best=True),
            "worst_symbol": _top_bucket(ts, lambda t: t.get("symbol") or "?", best=False),
        }
        m["recommendation"] = _recommend(m)
        report[setup] = m
    return report


def collect(db_path: str | None = None, days: int | None = None,
            environment: str = "paper") -> dict:
    try:
        trades = _load_trades(db_path, days, environment)
    except Exception as exc:
        return {"error": str(exc), "setups": {}}
    return {"n_trades": len(trades), "setups": compute_by_setup(trades)}


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="Per-setup expectancy report (directive Section 11).")
    p.add_argument("--days", type=int, default=None)
    p.add_argument("--db", default=None)
    p.add_argument("--environment", default="paper")
    p.add_argument("--json", action="store_true")
    args = p.parse_args(argv)

    result = collect(db_path=args.db, days=args.days, environment=args.environment)
    if args.json:
        print(json.dumps(result, indent=2, ensure_ascii=False))
        return 0

    setups = result.get("setups", {})
    if not setups:
        print("No setup data.")
        return 0
    for setup, m in setups.items():
        print(f"{setup}: n={m['sample_size']} E={m['expectancy_r']:+.3f}R "
              f"PF={m['profit_factor']} WR={m['win_rate']*100:.0f}% -> {m['recommendation']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
