#!/usr/bin/env python3
"""Read-only live-readiness report."""
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
    from core.live_readiness import check

    data = check(environment="paper")
    data["status"] = "PASS" if data.get("ready") else "FAIL"
    return data


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    logging.disable(logging.CRITICAL)
    data = collect()
    if args.json:
        print(json.dumps(data, indent=2, ensure_ascii=True))
    else:
        print(f"Live readiness: {data['status']}")
        for gate in data.get("gates", []):
            mark = "PASS" if gate.get("passed") else "FAIL"
            print(f"- {mark} {gate.get('name')}: {gate.get('detail')}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
