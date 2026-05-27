#!/usr/bin/env python3
"""
scripts/analyze_trades.py — AurvexAI Trade Win Rate Tanı Aracı

Kullanım:
  python3 scripts/analyze_trades.py
  python3 scripts/analyze_trades.py --verbose
  python3 scripts/analyze_trades.py --candidates  # signal_candidates da incele

DB: /root/trade_engine/trade-engine/trading.db (BASE veya DB_PATH env'den okunur)
"""

import os
import sys
import argparse
from pathlib import Path

# ── Path setup ────────────────────────────────────────────────────────────────
BASE = os.environ.get("BASE", "/root/trade_engine/trade-engine")
sys.path.insert(0, BASE)
DB_PATH = os.environ.get("DB_PATH", os.path.join(BASE, "trading.db"))

import sqlite3
from datetime import datetime, timezone


def get_conn():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


SEP = "─" * 64


def fmt_pct(n, d):
    return f"{n/d*100:.1f}%" if d else "N/A"


def section(title):
    print(f"\n{'━'*64}")
    print(f"  {title}")
    print('━'*64)


# ─────────────────────────────────────────────────────────────────────────────

def analyze_trades(verbose=False):
    conn = get_conn()

    # ── 1. Genel özet ─────────────────────────────────────────────────────────
    section("1. TRADE GENEL ÖZET")
    row = conn.execute("""
        SELECT
          COUNT(*) total,
          SUM(CASE WHEN net_pnl > 0 THEN 1 ELSE 0 END) wins,
          SUM(CASE WHEN net_pnl <= 0 THEN 1 ELSE 0 END) losses,
          COALESCE(SUM(net_pnl),0) total_pnl,
          COALESCE(AVG(net_pnl),0) avg_pnl,
          COALESCE(MIN(net_pnl),0) worst,
          COALESCE(MAX(net_pnl),0) best
        FROM trades WHERE status='closed'
    """).fetchone()

    wins, losses, total = row["wins"] or 0, row["losses"] or 0, row["total"] or 0
    print(f"  Toplam trade  : {total}")
    print(f"  Kazanç        : {wins}  ({fmt_pct(wins, total)})")
    print(f"  Kayıp         : {losses}  ({fmt_pct(losses, total)})")
    print(f"  Net PnL       : ${row['total_pnl']:.2f}")
    print(f"  Ort PnL/trade : ${row['avg_pnl']:.2f}")
    print(f"  En kötü trade : ${row['worst']:.2f}")
    print(f"  En iyi trade  : ${row['best']:.2f}")

    # ── 2. Yön bazlı ──────────────────────────────────────────────────────────
    section("2. YÖN BAZLI (LONG / SHORT)")
    rows = conn.execute("""
        SELECT
          COALESCE(direction, side, 'UNK') dir,
          COUNT(*) cnt,
          SUM(CASE WHEN net_pnl > 0 THEN 1 ELSE 0 END) wins,
          COALESCE(SUM(net_pnl),0) pnl
        FROM trades WHERE status='closed'
        GROUP BY 1
    """).fetchall()
    for r in rows:
        w = r["wins"] or 0
        print(f"  {r['dir']:<6}  {r['cnt']:>3} trade  {fmt_pct(w, r['cnt']):>7} kazanç  PnL={r['pnl']:.2f}$")

    # ── 3. Setup kalite bazlı ─────────────────────────────────────────────────
    section("3. SETUP KALİTE BAZLI")
    rows = conn.execute("""
        SELECT
          COALESCE(setup_quality, 'UNK') q,
          COUNT(*) cnt,
          SUM(CASE WHEN net_pnl > 0 THEN 1 ELSE 0 END) wins,
          COALESCE(SUM(net_pnl),0) pnl,
          COALESCE(AVG(leverage),1) avg_lev
        FROM trades WHERE status='closed'
        GROUP BY 1 ORDER BY cnt DESC
    """).fetchall()
    for r in rows:
        w = r["wins"] or 0
        print(f"  [{r['q']:<2}] {r['cnt']:>3} trade  {fmt_pct(w, r['cnt']):>7} kazanç  "
              f"PnL={r['pnl']:.2f}$  avg_lev={r['avg_lev']:.1f}x")

    # ── 4. Leverage dağılımı ──────────────────────────────────────────────────
    section("4. LEVERAGE DAĞILIMI")
    rows = conn.execute("""
        SELECT
          COALESCE(leverage,1) lev,
          COUNT(*) cnt,
          SUM(CASE WHEN net_pnl > 0 THEN 1 ELSE 0 END) wins,
          COALESCE(SUM(net_pnl),0) pnl
        FROM trades WHERE status='closed'
        GROUP BY 1 ORDER BY 1
    """).fetchall()
    if not rows:
        print("  (veri yok)")
    for r in rows:
        w = r["wins"] or 0
        bar = "█" * r["cnt"]
        print(f"  {r['lev']:>3}x  {r['cnt']:>3} trade  {fmt_pct(w, r['cnt']):>7}  PnL={r['pnl']:.2f}$  {bar}")

    # ── 5. SL/TP mesafe analizi ───────────────────────────────────────────────
    section("5. SL MESAFESİ ANALİZİ")
    rows = conn.execute("""
        SELECT
          symbol, direction, entry, sl, tp1, tp2,
          close_price, net_pnl, close_reason,
          ROUND(ABS(entry - sl) / entry * 100, 3) sl_pct,
          ROUND(ABS(tp1 - entry) / entry * 100, 3) tp1_pct
        FROM trades
        WHERE status='closed' AND entry > 0 AND sl > 0
        ORDER BY net_pnl ASC
        LIMIT 20
    """).fetchall()

    for r in rows:
        outcome = "✅" if (r["net_pnl"] or 0) > 0 else "❌"
        reason = (r["close_reason"] or "?")[:12]
        print(f"  {outcome} {r['symbol']:<12} {r['direction']:<5}  "
              f"entry={r['entry']:.4f}  SL_dist={r['sl_pct']:.2f}%  "
              f"TP1_dist={r['tp1_pct']:.2f}%  "
              f"close={reason}  PnL={r['net_pnl']:.2f}$")

    # ── 6. Son 10 trade detayı ────────────────────────────────────────────────
    if verbose:
        section("6. SON 10 TRADE DETAYI")
        rows = conn.execute("""
            SELECT symbol, direction, entry, sl, tp1, tp2,
                   close_price, net_pnl, leverage, close_reason,
                   setup_quality, open_time, close_time
            FROM trades WHERE status='closed'
            ORDER BY id DESC LIMIT 10
        """).fetchall()
        for r in rows:
            outcome = "WIN ✅" if (r["net_pnl"] or 0) > 0 else "LOSS ❌"
            print(f"\n  {outcome} {r['symbol']} {r['direction']} [{r['setup_quality'] or '?'}]  lev={r['leverage'] or 1}x")
            print(f"    entry={r['entry']}  SL={r['sl']}  TP1={r['tp1']}  TP2={r['tp2']}")
            print(f"    close={r['close_price']}  reason={r['close_reason']}  PnL={r['net_pnl']:.2f}$")
            print(f"    open={r['open_time']}  close={r['close_time']}")

    conn.close()


def analyze_candidates(verbose=False):
    conn = get_conn()

    section("7. SIGNAL CANDIDATES (SON 200)")
    row = conn.execute("""
        SELECT
          COUNT(*) total,
          ROUND(AVG(final_score),2) avg_score,
          ROUND(AVG(trigger_score),2) avg_trig,
          ROUND(AVG(trend_score),2) avg_trend,
          ROUND(AVG(risk_score),2) avg_risk
        FROM signal_candidates
        WHERE id > (SELECT MAX(id)-200 FROM signal_candidates)
    """).fetchone()
    print(f"  Son 200 candidate:  avg_final={row['avg_score']}  "
          f"trig={row['avg_trig']}  trend={row['avg_trend']}  risk={row['avg_risk']}")

    section("8. FINAL_SCORE DAĞILIMI")
    rows = conn.execute("""
        SELECT
          CASE
            WHEN final_score = 0    THEN '00 (sıfır)'
            WHEN final_score < 20   THEN '01-19'
            WHEN final_score < 25   THEN '20-24 (data)'
            WHEN final_score < 28   THEN '25-27 (watchlist)'
            WHEN final_score < 35   THEN '28-34 (telegram)'
            ELSE '35+ (trade)'
          END bucket,
          COUNT(*) cnt
        FROM signal_candidates
        GROUP BY 1 ORDER BY MIN(final_score)
    """).fetchall()
    total = sum(r["cnt"] for r in rows)
    for r in rows:
        bar = "█" * min(40, r["cnt"] * 40 // (total or 1))
        print(f"  {r['bucket']:<22}  {r['cnt']:>5}  {fmt_pct(r['cnt'], total):>6}  {bar}")

    section("9. LIFECYCLE AŞAMA DAĞILIMI")
    rows = conn.execute("""
        SELECT lifecycle_stage, COUNT(*) cnt
        FROM signal_candidates
        WHERE id > (SELECT MAX(id)-500 FROM signal_candidates)
        GROUP BY 1 ORDER BY cnt DESC
    """).fetchall()
    for r in rows:
        print(f"  {r['lifecycle_stage'] or 'NULL':<30}  {r['cnt']:>5}")

    section("10. REJECT NEDENLERI")
    rows = conn.execute("""
        SELECT reject_reason, COUNT(*) cnt
        FROM signal_candidates
        WHERE reject_reason IS NOT NULL
          AND id > (SELECT MAX(id)-500 FROM signal_candidates)
        GROUP BY 1 ORDER BY cnt DESC LIMIT 15
    """).fetchall()
    for r in rows:
        print(f"  {r['reject_reason'] or 'NULL':<35}  {r['cnt']:>5}")

    conn.close()


# ─────────────────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--verbose", "-v", action="store_true")
    parser.add_argument("--candidates", "-c", action="store_true")
    args = parser.parse_args()

    print(f"\n{'═'*64}")
    print(f"  AurvexAI Trade Analizi — {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M UTC')}")
    print(f"  DB: {DB_PATH}")
    print(f"{'═'*64}")

    analyze_trades(verbose=args.verbose)
    if args.candidates:
        analyze_candidates(verbose=args.verbose)

    print(f"\n{'═'*64}\n")
