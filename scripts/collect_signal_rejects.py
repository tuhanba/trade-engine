#!/usr/bin/env python3
"""
scripts/collect_signal_rejects.py

Collect aggregated counts of rejected signals and reasons from the database.
Useful to quickly spot why signals are VETOed / REJECTED / WATCH.

Usage:
    python scripts/collect_signal_rejects.py --output diagnostics/rejects.json

This script is read-only and safe to run against production/dev DB.
"""
from __future__ import annotations
import json
import os
import argparse
from collections import Counter, defaultdict

# Try to use project database helper if available
try:
    from database import get_conn
    from config import DB_PATH
except Exception:
    import sqlite3
    DB_PATH = os.environ.get('DB_PATH', 'trading.db')
    def get_conn():
        conn = sqlite3.connect(DB_PATH)
        conn.row_factory = sqlite3.Row
        return conn


def collect_counts(conn):
    """Return aggregated counts for signal_events and signal_candidates reasons."""
    out = {
        'signal_events': {},
        'signal_candidates': {},
    }

    try:
        cur = conn.execute(
            "SELECT event_type, reject_reason, COUNT(*) as cnt FROM signal_events GROUP BY event_type, reject_reason"
        )
        rows = cur.fetchall()
        ev = defaultdict(Counter)
        for r in rows:
            et = r['event_type'] if 'event_type' in r.keys() else r[0]
            rr = r['reject_reason'] if 'reject_reason' in r.keys() else r[1]
            ev[et][rr or 'UNKNOWN'] += int(r['cnt'] if 'cnt' in r.keys() else r[2])
        out['signal_events'] = {k: dict(v) for k, v in ev.items()}
    except Exception as e:
        out['signal_events_error'] = str(e)

    try:
        cur = conn.execute(
            "SELECT decision, reject_reason, COUNT(*) as cnt FROM signal_candidates GROUP BY decision, reject_reason"
        )
        rows = cur.fetchall()
        sc = defaultdict(Counter)
        for r in rows:
            dec = r['decision'] if 'decision' in r.keys() else r[0]
            rr = r['reject_reason'] if 'reject_reason' in r.keys() else r[1]
            sc[dec][rr or 'UNKNOWN'] += int(r['cnt'] if 'cnt' in r.keys() else r[2])
        out['signal_candidates'] = {k: dict(v) for k, v in sc.items()}
    except Exception as e:
        out['signal_candidates_error'] = str(e)

    # Top AI veto reasons
    try:
        cur = conn.execute(
            "SELECT reject_reason, COUNT(*) as cnt FROM signal_events WHERE event_type LIKE '%AI%' GROUP BY reject_reason ORDER BY cnt DESC LIMIT 20"
        )
        out['top_ai_veto_reasons'] = [{ 'reason': r[0], 'count': r[1] } for r in cur.fetchall()]
    except Exception:
        pass

    # Last 50 veto rows for inspection
    try:
        cur = conn.execute(
            "SELECT * FROM signal_events WHERE event_type IN ('AI_VETOED','RISK_REJECTED','EXECUTION_REJECTED') ORDER BY created_at DESC LIMIT 50"
        )
        out['last_rejects'] = [dict(r) for r in cur.fetchall()]
    except Exception:
        out['last_rejects_error'] = 'table or columns missing'

    return out


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--db', default=DB_PATH, help='Path to SQLite DB (default from config)')
    parser.add_argument('--output', default='diagnostics/rejects.json')
    args = parser.parse_args()

    os.makedirs(os.path.dirname(args.output) or '.', exist_ok=True)

    # Connect
    try:
        conn = get_conn()
    except Exception:
        import sqlite3
        conn = sqlite3.connect(args.db)
        conn.row_factory = sqlite3.Row

    result = collect_counts(conn)

    with open(args.output, 'w', encoding='utf-8') as f:
        json.dump(result, f, indent=2, ensure_ascii=False)

    print(f"Wrote diagnostics to {args.output}")


if __name__ == '__main__':
    main()
