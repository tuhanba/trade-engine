#!/usr/bin/env python3
"""
scripts/backtest_engine.py
============================
Geçmiş signal_candidates verilerini kullanarak accounting modülüyle
simüle edilmiş backtest çalıştırır.

Kullanım:
    python3 scripts/backtest_engine.py
    python3 scripts/backtest_engine.py --db /path/to/trading.db --balance 500 --risk 3.0
"""
import sys
import os
import sqlite3
import argparse
from datetime import datetime

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

try:
    from config import DB_PATH, PAPER_LEVERAGE, RISK_PCT
except ImportError:
    DB_PATH = "trading.db"
    PAPER_LEVERAGE = 15
    RISK_PCT = 3.0

from core.accounting import (
    calculate_position_size,
    calculate_close_pnl,
    DEFAULT_TAKER_FEE,
)

def run_backtest(db_path: str, initial_balance: float, risk_pct: float):
    print(f"\n[Backtest] DB: {db_path}")
    print(f"[Backtest] Başlangıç Bakiye: {initial_balance}$  |  Risk/Trade: {risk_pct}%")
    print(f"[Backtest] Kaldıraç: {PAPER_LEVERAGE}x  |  Fee: {DEFAULT_TAKER_FEE*100:.2f}%\n")

    if not os.path.exists(db_path):
        print(f"[HATA] DB bulunamadı: {db_path}")
        sys.exit(1)

    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row

    # Sonucu belli olan sinyalleri al
    rows = conn.execute("""
        SELECT symbol, direction, entry, sl, tp1, tp2, tp3,
               future_outcome, setup_quality, final_score
        FROM signal_candidates
        WHERE future_outcome IN ('WIN','LOSS','TP1','TP2','TP3','SL')
          AND entry > 0 AND sl > 0 AND tp1 > 0
        ORDER BY created_at ASC
    """).fetchall()
    conn.close()

    if not rows:
        print("[Backtest] Sonucu belli sinyal bulunamadı. future_outcome alanı dolu değil.")
        return

    balance = initial_balance
    trades  = []
    wins = losses = 0
    total_pnl = 0.0
    max_balance = balance
    min_balance = balance
    peak = balance
    max_dd = 0.0

    for row in rows:
        t = dict(row)
        entry     = float(t["entry"])
        sl        = float(t["sl"])
        tp1       = float(t["tp1"])
        tp2       = float(t.get("tp2") or 0)
        direction = (t["direction"] or "LONG").upper()
        outcome   = (t["future_outcome"] or "").upper()

        # Pozisyon hesapla
        pos = calculate_position_size(
            balance, risk_pct, entry, sl, PAPER_LEVERAGE, DEFAULT_TAKER_FEE
        )
        qty      = pos["qty"]
        open_fee = pos["open_fee"]

        # Simüle kapanış fiyatı
        if outcome in ("WIN", "TP1", "TP2", "TP3"):
            close_price = tp2 if (outcome in ("TP2", "TP3") and tp2) else tp1
            reason = "tp"
        else:
            close_price = sl
            reason = "sl"

        result = calculate_close_pnl(
            entry, close_price, qty, direction, 0.0, open_fee, DEFAULT_TAKER_FEE
        )
        net_pnl = result["net_pnl"]
        balance += net_pnl
        total_pnl += net_pnl

        if net_pnl > 0:
            wins += 1
        else:
            losses += 1

        # Max drawdown
        if balance > peak:
            peak = balance
        dd = (peak - balance) / peak * 100 if peak else 0
        if dd > max_dd:
            max_dd = dd

        max_balance = max(max_balance, balance)
        min_balance = min(min_balance, balance)

        trades.append({
            "symbol":    t["symbol"],
            "direction": direction,
            "outcome":   outcome,
            "net_pnl":   net_pnl,
            "balance":   balance,
            "quality":   t.get("setup_quality", "?"),
        })

    total = wins + losses
    win_rate = wins / total * 100 if total else 0
    profit_factor = sum(t["net_pnl"] for t in trades if t["net_pnl"] > 0) / \
                    abs(sum(t["net_pnl"] for t in trades if t["net_pnl"] < 0) or 1)

    print(f"{'═'*55}")
    print(f"  BACKTEST SONUÇLARI")
    print(f"{'═'*55}")
    print(f"  Toplam Trade:      {total}")
    print(f"  Win / Loss:        {wins} / {losses}")
    print(f"  Win Rate:          {win_rate:.1f}%")
    print(f"  Profit Factor:     {profit_factor:.2f}")
    print(f"  Toplam Net PnL:    {total_pnl:+.2f}$")
    print(f"  Başlangıç Bakiye:  {initial_balance:.2f}$")
    print(f"  Final Bakiye:      {balance:.2f}$")
    print(f"  Max Bakiye:        {max_balance:.2f}$")
    print(f"  Min Bakiye:        {min_balance:.2f}$")
    print(f"  Max Drawdown:      {max_dd:.2f}%")
    print(f"{'═'*55}")

    # Kalite bazlı özet
    from collections import defaultdict
    qual_stats = defaultdict(lambda: {"wins": 0, "losses": 0, "pnl": 0.0})
    for t in trades:
        q = t["quality"]
        if t["net_pnl"] > 0:
            qual_stats[q]["wins"] += 1
        else:
            qual_stats[q]["losses"] += 1
        qual_stats[q]["pnl"] += t["net_pnl"]

    print(f"\n  Kalite Bazlı Özet:")
    print(f"  {'Kalite':<8}  {'W':>4}  {'L':>4}  {'WR%':>6}  {'PnL':>9}")
    print(f"  {'─'*40}")
    for q in sorted(qual_stats.keys()):
        s = qual_stats[q]
        tot = s["wins"] + s["losses"]
        wr  = s["wins"] / tot * 100 if tot else 0
        print(f"  {q:<8}  {s['wins']:>4}  {s['losses']:>4}  {wr:>5.1f}%  {s['pnl']:>+9.2f}$")

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Accounting tabanlı backtest")
    parser.add_argument("--db",      default=None)
    parser.add_argument("--balance", type=float, default=500.0)
    parser.add_argument("--risk",    type=float, default=RISK_PCT)
    args = parser.parse_args()
    run_backtest(args.db or DB_PATH, args.balance, args.risk)
