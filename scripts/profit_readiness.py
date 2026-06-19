#!/usr/bin/env python3
"""scripts/profit_readiness.py — directive Section 10 go-live proof gate.

Read-only. Computes fee-adjusted profitability metrics over closed PAPER trades
and returns PASS/FAIL with exact reasons. Supports --json.

This is the "profit_readiness == PASS" condition referenced by the live safety
gate. It is intentionally strict and fails CLOSED (no data => NOT READY).

    python scripts/profit_readiness.py
    python scripts/profit_readiness.py --json
    python scripts/profit_readiness.py --days 30 --json
"""
from __future__ import annotations

import argparse
import json
import sys
from collections import defaultdict
from datetime import datetime, timezone, timedelta
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

# ── Gate thresholds (directive Section 10) ───────────────────────────────────
MIN_CLOSED_TRADES = 300
MIN_EXPECTANCY_R = 0.10
MIN_PROFIT_FACTOR = 1.25
MAX_DRAWDOWN_PCT = 7.0
MAX_COIN_CONCENTRATION = 0.35
MAX_SETUP_CONCENTRATION = 0.70
MAX_SESSION_CONCENTRATION = 0.40
DEFAULT_BASE_BALANCE = 1000.0


def _f(v, default: float = 0.0) -> float:
    try:
        if v in (None, ""):
            return default
        return float(v)
    except (TypeError, ValueError):
        return default


def _r(t: dict) -> float:
    """R-multiple of a trade; falls back to net_pnl / risk_usd."""
    rm = t.get("r_multiple")
    if rm not in (None, ""):
        return _f(rm)
    pnl = _pnl(t)
    risk = _f(t.get("risk_usd"))
    return pnl / risk if risk > 0 else 0.0


def _pnl(t: dict) -> float:
    v = t.get("net_pnl")
    if v in (None, ""):
        v = t.get("realized_pnl")
    return _f(v)


def _session(t: dict) -> str:
    s = t.get("session")
    if not s:
        md = t.get("metadata")
        if isinstance(md, dict):
            s = md.get("session")
    return str(s or "UNKNOWN")


def _setup(t: dict) -> str:
    return str(t.get("setup_type") or "UNKNOWN")


def _max_drawdown_pct(pnls: list[float], base_balance: float = DEFAULT_BASE_BALANCE) -> float:
    """Peak-to-trough decline of the equity curve (base + cumulative PnL), %."""
    base = base_balance if base_balance > 0 else DEFAULT_BASE_BALANCE
    equity = base
    peak = base
    max_dd = 0.0
    for p in pnls:
        equity += p
        if equity > peak:
            peak = equity
        if peak > 0:
            dd = (peak - equity) / peak * 100.0
            if dd > max_dd:
                max_dd = dd
    return max_dd


def _concentration(trades: list[dict], key_fn) -> tuple[float, str]:
    """Max share of GROSS POSITIVE pnl from a single bucket. (share, bucket)."""
    pos = defaultdict(float)
    total = 0.0
    for t in trades:
        p = _pnl(t)
        if p > 0:
            pos[key_fn(t)] += p
            total += p
    if total <= 0 or not pos:
        return 0.0, ""
    bucket, share = max(pos.items(), key=lambda kv: kv[1])
    return share / total, bucket


def _gate(name: str, passed: bool, detail: str, value: Any) -> dict:
    return {"gate": name, "pass": bool(passed), "detail": detail, "value": value}


def compute(trades: list[dict], base_balance: float = DEFAULT_BASE_BALANCE) -> dict:
    """Pure metric + gate computation over a list of closed-trade dicts."""
    n = len(trades)
    gates: list[dict] = []

    if n == 0:
        gates.append(_gate("min_trades", False, "0 kapanmış paper işlem", 0))
        return {
            "ready": False, "n_trades": 0, "metrics": {}, "gates": gates,
            "summary": "NOT READY — veri yok",
        }

    rs = [_r(t) for t in trades]
    pnls = [_pnl(t) for t in trades]
    wins = [t for t in trades if _r(t) > 0]
    losses = [t for t in trades if _r(t) <= 0]

    win_rate = len(wins) / n
    avg_win_r = (sum(_r(t) for t in wins) / len(wins)) if wins else 0.0
    avg_loss_r = (sum(_r(t) for t in losses) / len(losses)) if losses else 0.0
    expectancy_r = win_rate * avg_win_r + (1 - win_rate) * avg_loss_r

    net_pnl = sum(pnls)
    gross_win = sum(p for p in pnls if p > 0)
    gross_loss = abs(sum(p for p in pnls if p < 0))
    profit_factor = (gross_win / gross_loss) if gross_loss > 0 else (float("inf") if gross_win > 0 else 0.0)
    max_dd = _max_drawdown_pct(pnls, base_balance)

    coin_conc, coin_top = _concentration(trades, lambda t: str(t.get("symbol") or "?"))
    setup_conc, setup_top = _concentration(trades, _setup)
    sess_conc, sess_top = _concentration(trades, _session)

    metrics = {
        "n_trades": n,
        "net_pnl": round(net_pnl, 4),
        "win_rate": round(win_rate, 4),
        "avg_win_r": round(avg_win_r, 4),
        "avg_loss_r": round(avg_loss_r, 4),
        "expectancy_r": round(expectancy_r, 4),
        "profit_factor": round(profit_factor, 4) if profit_factor != float("inf") else "inf",
        "max_drawdown_pct": round(max_dd, 4),
        "coin_concentration": round(coin_conc, 4), "coin_top": coin_top,
        "setup_concentration": round(setup_conc, 4), "setup_top": setup_top,
        "session_concentration": round(sess_conc, 4), "session_top": sess_top,
    }

    gates.append(_gate("min_trades", n >= MIN_CLOSED_TRADES,
                       f"{n}/{MIN_CLOSED_TRADES} kapanmış paper işlem", n))
    gates.append(_gate("expectancy", expectancy_r > MIN_EXPECTANCY_R,
                       f"expectancy {expectancy_r:+.3f}R (> {MIN_EXPECTANCY_R}R)", round(expectancy_r, 4)))
    gates.append(_gate("profit_factor", profit_factor > MIN_PROFIT_FACTOR,
                       f"profit factor {metrics['profit_factor']} (> {MIN_PROFIT_FACTOR})", metrics["profit_factor"]))
    gates.append(_gate("max_drawdown", max_dd < MAX_DRAWDOWN_PCT,
                       f"max drawdown %{max_dd:.2f} (< %{MAX_DRAWDOWN_PCT})", round(max_dd, 4)))
    gates.append(_gate("net_positive", net_pnl > 0,
                       f"fee-adjusted net PnL {net_pnl:+.2f} (> 0)", round(net_pnl, 4)))
    gates.append(_gate("coin_concentration", coin_conc <= MAX_COIN_CONCENTRATION,
                       f"en yoğun coin {coin_top} %{coin_conc*100:.0f} (≤ %{MAX_COIN_CONCENTRATION*100:.0f})", round(coin_conc, 4)))
    gates.append(_gate("setup_concentration", setup_conc <= MAX_SETUP_CONCENTRATION,
                       f"en yoğun setup {setup_top} %{setup_conc*100:.0f} (≤ %{MAX_SETUP_CONCENTRATION*100:.0f})", round(setup_conc, 4)))
    gates.append(_gate("session_concentration", sess_conc <= MAX_SESSION_CONCENTRATION,
                       f"en yoğun session {sess_top} %{sess_conc*100:.0f} (≤ %{MAX_SESSION_CONCENTRATION*100:.0f})", round(sess_conc, 4)))

    ready = all(g["pass"] for g in gates)
    failed = [g["gate"] for g in gates if not g["pass"]]
    return {
        "ready": ready,
        "n_trades": n,
        "metrics": metrics,
        "gates": gates,
        "failed_gates": failed,
        "summary": "READY" if ready else f"NOT READY — {', '.join(failed)}",
    }


def _load_trades(db_path: str | None, days: int | None, environment: str) -> list[dict]:
    import database
    where = ["status = 'closed'", "environment = ?", "COALESCE(is_valid_for_stats, 1) = 1"]
    params: list[Any] = [environment]
    if days:
        where.append("close_time >= ?")
        params.append((datetime.now(timezone.utc) - timedelta(days=days)).isoformat())
    sql = (
        "SELECT symbol, net_pnl, realized_pnl, risk_usd, r_multiple, mae, mfe, "
        "close_reason, setup_type, metadata, close_time "
        "FROM trades WHERE " + " AND ".join(where)
    )
    conn = database.get_conn() if not db_path else __import__("sqlite3").connect(db_path)
    try:
        try:
            conn.row_factory = __import__("sqlite3").Row
        except Exception:
            pass
        rows = conn.execute(sql, params).fetchall()
    finally:
        try:
            conn.close()
        except Exception:
            pass
    out = []
    for r in rows:
        d = dict(r)
        md = d.get("metadata")
        if isinstance(md, str):
            try:
                d["metadata"] = json.loads(md)
            except Exception:
                d["metadata"] = {}
        out.append(d)
    return out


def collect(db_path: str | None = None, days: int | None = None,
            environment: str = "paper") -> dict:
    try:
        trades = _load_trades(db_path, days, environment)
    except Exception as exc:
        return {"ready": False, "n_trades": 0, "metrics": {}, "gates": [],
                "summary": f"NOT READY — error: {exc}", "error": str(exc)}
    base = DEFAULT_BASE_BALANCE
    try:
        import config
        base = float(getattr(config, "BASE_ACCOUNT_SIZE", DEFAULT_BASE_BALANCE))
    except Exception:
        pass
    return compute(trades, base_balance=base)


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description="Profit readiness PASS/FAIL gate (directive Section 10).")
    p.add_argument("--days", type=int, default=None, help="Lookback window in days (default: all).")
    p.add_argument("--db", default=None, help="SQLite DB path. Defaults to config.DB_PATH.")
    p.add_argument("--environment", default="paper")
    p.add_argument("--json", action="store_true", help="Emit JSON.")
    args = p.parse_args(argv)

    result = collect(db_path=args.db, days=args.days, environment=args.environment)
    if args.json:
        print(json.dumps(result, indent=2, ensure_ascii=False))
        return 0 if result.get("ready") else 1

    print(f"PROFIT READINESS: {'PASS ✅' if result.get('ready') else 'FAIL ❌'} — {result.get('summary')}")
    for g in result.get("gates", []):
        print(f"  [{'PASS' if g['pass'] else 'FAIL'}] {g['gate']}: {g['detail']}")
    return 0 if result.get("ready") else 1


if __name__ == "__main__":
    raise SystemExit(main())
