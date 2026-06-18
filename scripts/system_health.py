#!/usr/bin/env python3
"""Read-only Aurvex system health report."""
from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def collect() -> dict:
    import database
    from telegram_delivery import TelegramDelivery

    bot_status = {}
    open_paper = 0
    db_ok = True
    try:
        with database.get_conn() as conn:
            has_bot_status = conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name='bot_status'"
            ).fetchone()
            if has_bot_status:
                rows = conn.execute("SELECT key, value, updated_at FROM bot_status").fetchall()
                bot_status = {r["key"]: {"value": r["value"], "updated_at": r["updated_at"]} for r in rows}
            has_trades = conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name='trades'"
            ).fetchone()
            if has_trades:
                open_paper = conn.execute(
                    "SELECT COUNT(*) FROM trades WHERE environment='paper' AND LOWER(status) NOT IN ('closed','cancelled')"
                ).fetchone()[0] or 0
    except Exception:
        db_ok = False

    def _status_int(key: str) -> int:
        try:
            return int(bot_status.get(key, {}).get("value", 0) or 0)
        except Exception:
            return 0

    heartbeat = bot_status.get("heartbeat", {}).get("value")
    bot_alive = False
    if heartbeat:
        try:
            ts = datetime.fromisoformat(str(heartbeat).replace("Z", "+00:00"))
            if ts.tzinfo is None:
                ts = ts.replace(tzinfo=timezone.utc)
            bot_alive = (datetime.now(timezone.utc) - ts).total_seconds() < 180
        except Exception:
            bot_alive = False

    telegram_alive = TelegramDelivery().is_configured()
    components = {
        "db": "ok" if db_ok else "down",
        "engine": "ok" if bot_alive else "down",
        "telegram": "ok" if telegram_alive else "warn",
    }
    pulse = {
        "status": "ok" if db_ok and bot_alive else "down",
        "label": "OK" if db_ok and bot_alive else "ISSUE",
        "score": sum(1 for value in components.values() if value == "ok"),
        "max": len(components),
        "components": components,
    }

    return {
        "mode": os.environ.get("EXECUTION_MODE", "paper"),
        "bot_alive": bot_alive,
        "dashboard_alive": True,
        "telegram_alive": telegram_alive,
        "last_scan_time": heartbeat,
        "symbols_scanned": _status_int("pipeline_scanned"),
        "paper_trades_open": int(open_paper),
        "pulse": pulse,
        "checked_at": datetime.now(timezone.utc).isoformat(),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    data = collect()
    if args.json:
        print(json.dumps(data, indent=2, ensure_ascii=True))
    else:
        print(f"Mode: {data['mode']}")
        print(f"Bot alive: {data['bot_alive']}")
        print(f"Dashboard alive: {data['dashboard_alive']}")
        print(f"Telegram alive: {data['telegram_alive']}")
        print(f"Paper trades open: {data['paper_trades_open']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
