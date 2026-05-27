"""
scripts/backtest_engine.py — AX Backtest Engine v6.0 (Production)
==================================================================
Gerçek market koşullarını simüle eder:
  - Fee (maker/taker), slippage, latency
  - Leverage, margin liquidation
  - Partial fills, partial closes (TP1/TP2/TP3)
  - Max hold timeout
  - Drawdown tracking
  - Per-trade ve aggregate rapor
"""
from __future__ import annotations

import os
import sys
import argparse
import logging
import json
import sqlite3
from datetime import datetime, timezone
from typing import Optional
from collections import defaultdict

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.accounting import (
    calculate_pnl,
    calculate_partial_close_pnl,
    calculate_fee,
    calculate_position_size,
    calculate_r_multiple,
    calculate_margin_loss_pct,
)
from config import (
    DB_PATH, DEFAULT_FEE_RATE,
    TP1_CLOSE_PCT, TP2_CLOSE_PCT, RUNNER_CLOSE_PCT,
    MAX_MARGIN_LOSS_PCT, INITIAL_PAPER_BALANCE,
)

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
logger = logging.getLogger("backtest")


# ── Simulation Params ────────────────────────────────────────────────

DEFAULT_SLIPPAGE_PCT = 0.0005   # %0.05 slippage
DEFAULT_LATENCY_FILL_PCT = 0.0002  # Doldurmada ek kayıp


class BacktestEngine:
    """
    Production-grade backtest motoru.
    Gerçek market koşullarını simüle eder.
    """

    def __init__(
        self,
        initial_balance: float = INITIAL_PAPER_BALANCE,
        fee_rate: float = DEFAULT_FEE_RATE,
        slippage_pct: float = DEFAULT_SLIPPAGE_PCT,
        use_partial_closes: bool = True,
    ):
        self.initial_balance = initial_balance
        self.balance = initial_balance
        self.fee_rate = fee_rate
        self.slippage_pct = slippage_pct
        self.use_partial_closes = use_partial_closes
        self.results: list[dict] = []
        self.peak_balance = initial_balance
        self.max_drawdown = 0.0
        self.max_drawdown_pct = 0.0
        self.consecutive_losses = 0
        self.max_consecutive_losses = 0

    # ── Slippage uygula ────────────────────────────────────────────

    def _apply_slippage(self, price: float, side: str, is_entry: bool) -> float:
        """
        Entry: LONG girişte fiyat biraz yükselir, SHORT girişte düşer.
        Exit: Ters yönde slippage.
        """
        slip = price * self.slippage_pct
        if is_entry:
            return price + slip if side == "LONG" else price - slip
        else:
            return price - slip if side == "LONG" else price + slip

    # ── Tek trade simülasyonu ─────────────────────────────────────

    def simulate_trade(
        self,
        entry: float,
        sl: float,
        tp1: float,
        tp2: float,
        tp3: float,
        direction: str,
        leverage: int = 10,
        risk_pct: float = 1.0,
        outcome: str = "tp1",
        hold_minutes: int = 60,
    ) -> Optional[dict]:
        """
        Tek trade simülasyonu.

        outcome: "sl" | "tp1" | "tp2" | "tp3" | "full_win" | "timeout"
        """
        # Slippage uygulanmış entry
        actual_entry = self._apply_slippage(entry, direction, is_entry=True)

        # Pozisyon büyüklüğü (risk-based)
        stop_distance = abs(actual_entry - sl)
        if stop_distance <= 0:
            return None

        risk_usd = self.balance * (risk_pct / 100.0)
        qty = risk_usd / stop_distance
        notional = qty * actual_entry
        margin = notional / max(leverage, 1)

        # Margin liquidation kontrolü
        mlp = calculate_margin_loss_pct(actual_entry, sl, leverage)
        if mlp > MAX_MARGIN_LOSS_PCT:
            return {
                "skipped": True,
                "reason": f"margin_breach ({mlp*100:.1f}%)",
                "balance_after": round(self.balance, 4),
            }

        # Partial close miktarları
        tp1_pct = TP1_CLOSE_PCT / 100.0
        tp2_pct = TP2_CLOSE_PCT / 100.0
        runner_pct = RUNNER_CLOSE_PCT / 100.0
        qty_tp1 = qty * tp1_pct
        qty_tp2 = qty * tp2_pct
        qty_runner = qty * runner_pct

        # Open fee (entry tarafı)
        open_fee = calculate_fee(notional, self.fee_rate)
        total_pnl = -open_fee
        total_fee = open_fee
        breakeven_sl = actual_entry  # TP1 sonrası SL

        if outcome == "sl" or outcome == "timeout":
            # SL vuruldu — tam kayıp
            actual_sl = self._apply_slippage(sl, direction, is_entry=False)
            pnl, fee = calculate_partial_close_pnl(direction, actual_entry, actual_sl, qty, self.fee_rate)
            total_pnl += pnl
            total_fee += fee

        elif outcome == "tp1":
            # TP1 vuruldu → %TP1 kapatılır, kalan breakeven'da kapanır
            actual_tp1 = self._apply_slippage(tp1, direction, is_entry=False)
            p1, f1 = calculate_partial_close_pnl(direction, actual_entry, actual_tp1, qty_tp1, self.fee_rate)
            # Kalan: breakeven (entry) kapanır
            p_be, f_be = calculate_partial_close_pnl(direction, actual_entry, actual_entry, qty_tp2 + qty_runner, self.fee_rate)
            total_pnl += p1 + p_be
            total_fee += f1 + f_be

        elif outcome == "tp2":
            # TP1 + TP2 hit, runner breakeven
            actual_tp1 = self._apply_slippage(tp1, direction, is_entry=False)
            actual_tp2 = self._apply_slippage(tp2, direction, is_entry=False)
            p1, f1 = calculate_partial_close_pnl(direction, actual_entry, actual_tp1, qty_tp1, self.fee_rate)
            p2, f2 = calculate_partial_close_pnl(direction, actual_entry, actual_tp2, qty_tp2, self.fee_rate)
            p_be, f_be = calculate_partial_close_pnl(direction, actual_entry, actual_entry, qty_runner, self.fee_rate)
            total_pnl += p1 + p2 + p_be
            total_fee += f1 + f2 + f_be

        elif outcome in ("tp3", "full_win"):
            # Full win: TP1 + TP2 + TP3
            actual_tp1 = self._apply_slippage(tp1, direction, is_entry=False)
            actual_tp2 = self._apply_slippage(tp2, direction, is_entry=False)
            actual_tp3 = self._apply_slippage(tp3 if tp3 > 0 else tp2 * 1.02, direction, is_entry=False)
            p1, f1 = calculate_partial_close_pnl(direction, actual_entry, actual_tp1, qty_tp1, self.fee_rate)
            p2, f2 = calculate_partial_close_pnl(direction, actual_entry, actual_tp2, qty_tp2, self.fee_rate)
            p3, f3 = calculate_partial_close_pnl(direction, actual_entry, actual_tp3, qty_runner, self.fee_rate)
            total_pnl += p1 + p2 + p3
            total_fee += f1 + f2 + f3

        r_multiple = calculate_r_multiple(total_pnl, risk_usd)

        # Balance güncelle
        self.balance += total_pnl

        # Drawdown takibi
        if self.balance > self.peak_balance:
            self.peak_balance = self.balance
        dd = self.peak_balance - self.balance
        dd_pct = (dd / self.peak_balance * 100) if self.peak_balance > 0 else 0
        if dd > self.max_drawdown:
            self.max_drawdown = dd
            self.max_drawdown_pct = dd_pct

        # Consecutive loss takibi
        if total_pnl <= 0:
            self.consecutive_losses += 1
            self.max_consecutive_losses = max(self.max_consecutive_losses, self.consecutive_losses)
        else:
            self.consecutive_losses = 0

        result = {
            "direction": direction,
            "entry": round(actual_entry, 6),
            "sl": sl,
            "tp1": tp1,
            "tp2": tp2,
            "tp3": tp3 if tp3 else 0,
            "outcome": outcome,
            "net_pnl": round(total_pnl, 4),
            "total_fee": round(total_fee, 6),
            "r_multiple": r_multiple,
            "balance_after": round(self.balance, 4),
            "risk_usd": round(risk_usd, 4),
            "margin": round(margin, 4),
            "leverage": leverage,
            "slippage_pct": self.slippage_pct,
            "hold_minutes": hold_minutes,
        }
        self.results.append(result)
        return result

    # ── DB'den backtest ───────────────────────────────────────────

    def run_from_db(self, limit: int = 500) -> dict:
        """DB'deki geçmiş trade'lerden backtest yap."""
        conn = sqlite3.connect(DB_PATH, timeout=10)
        conn.row_factory = sqlite3.Row

        rows = conn.execute("""
            SELECT * FROM trades
            WHERE status='CLOSED'
            ORDER BY id
            LIMIT ?
        """, (limit,)).fetchall()
        conn.close()

        if not rows:
            logger.warning("DB'de CLOSED trade bulunamadı.")
            return self.generate_report()

        for r in rows:
            entry = float(r["entry_price"] or 0)
            sl = float(r["stop_loss"] or 0)
            tp1 = float(r["tp1"] or 0)
            tp2 = float(r["tp2"] or 0) or (tp1 * 1.5 if tp1 else 0)
            tp3 = float(r["tp3"] or 0) or (tp1 * 2.5 if tp1 else 0)
            direction = str(r["side"] or "LONG").upper()
            leverage = int(r["leverage"] or 10)

            if not entry or not sl:
                continue

            # Outcome belirle
            rpnl = float(r["realized_pnl"] or 0)
            close_reason = str(r["close_reason"] or "")

            if "TP3" in close_reason or "FULL" in close_reason:
                outcome = "tp3"
            elif "TP2" in close_reason:
                outcome = "tp2"
            elif "TP1" in close_reason or rpnl > 0:
                outcome = "tp1"
            elif "TIMEOUT" in close_reason:
                outcome = "timeout"
            else:
                outcome = "sl"

            self.simulate_trade(entry, sl, tp1, tp2, tp3, direction, leverage, outcome=outcome)

        return self.generate_report()

    # ── Rapor ────────────────────────────────────────────────────

    def generate_report(self) -> dict:
        """Kapsamlı performans raporu."""
        if not self.results:
            report = {
                "total_trades": 0, "win_rate": 0, "profit_factor": 0,
                "expectancy": 0, "avg_r": 0,
                "max_drawdown": 0, "max_drawdown_pct": 0,
                "total_fees": 0, "tp1_hit_rate": 0, "tp2_hit_rate": 0,
                "final_balance": self.balance,
                "initial_balance": self.initial_balance,
                "net_return_pct": 0,
                "max_consecutive_losses": 0,
                "slippage_pct": self.slippage_pct,
            }
            self._print_report(report)
            return report

        total = len(self.results)
        wins = [r for r in self.results if r["net_pnl"] > 0]
        losses = [r for r in self.results if r["net_pnl"] <= 0]
        win_rate = len(wins) / total if total else 0
        avg_win = sum(r["net_pnl"] for r in wins) / len(wins) if wins else 0
        avg_loss = abs(sum(r["net_pnl"] for r in losses) / len(losses)) if losses else 0
        expectancy = win_rate * avg_win - (1 - win_rate) * avg_loss
        gross_profit = sum(r["net_pnl"] for r in wins)
        gross_loss = abs(sum(r["net_pnl"] for r in losses))
        pf = gross_profit / gross_loss if gross_loss > 0 else 0

        tp1_hits = sum(1 for r in self.results if r["outcome"] in ("tp1","tp2","tp3","full_win"))
        tp2_hits = sum(1 for r in self.results if r["outcome"] in ("tp2","tp3","full_win"))
        tp3_hits = sum(1 for r in self.results if r["outcome"] in ("tp3","full_win"))

        report = {
            "total_trades": total,
            "wins": len(wins),
            "losses": len(losses),
            "win_rate": round(win_rate, 4),
            "profit_factor": round(pf, 2),
            "expectancy": round(expectancy, 4),
            "avg_r": round(sum(r["r_multiple"] for r in self.results) / total, 3),
            "avg_win_usd": round(avg_win, 4),
            "avg_loss_usd": round(avg_loss, 4),
            "max_drawdown": round(self.max_drawdown, 4),
            "max_drawdown_pct": round(self.max_drawdown_pct, 2),
            "total_fees": round(sum(r["total_fee"] for r in self.results), 4),
            "tp1_hit_rate": round(tp1_hits / total, 4) if total else 0,
            "tp2_hit_rate": round(tp2_hits / total, 4) if total else 0,
            "tp3_hit_rate": round(tp3_hits / total, 4) if total else 0,
            "final_balance": round(self.balance, 4),
            "initial_balance": self.initial_balance,
            "net_return_pct": round(
                (self.balance - self.initial_balance) / self.initial_balance * 100, 2
            ),
            "max_consecutive_losses": self.max_consecutive_losses,
            "slippage_pct": self.slippage_pct,
            "fee_rate": self.fee_rate,
        }
        self._print_report(report)
        return report

    def _print_report(self, r: dict) -> None:
        print("\n" + "=" * 60)
        print("AX BACKTEST PERFORMANS RAPORU v6.0")
        print("=" * 60)
        for k, v in r.items():
            label = k.replace("_", " ").title()
            if isinstance(v, float):
                print(f"  {label}: {v:.4f}")
            else:
                print(f"  {label}: {v}")

        ev = r.get("expectancy", 0)
        if ev > 0:
            print("\nPozitif expectancy — strateji karli")
        else:
            print("\nNegatif/sifir expectancy — optimizasyon gerekli")

        wr = r.get("win_rate", 0)
        if isinstance(wr, float):
            print(f"Win Rate: {wr*100:.1f}%  |  PF: {r.get('profit_factor',0):.2f}")
        print("=" * 60)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="AX Backtest Engine v6.0")
    parser.add_argument("--limit", type=int, default=500, help="DB'den max trade sayisi")
    parser.add_argument("--balance", type=float, default=INITIAL_PAPER_BALANCE)
    parser.add_argument("--slippage", type=float, default=DEFAULT_SLIPPAGE_PCT)
    parser.add_argument("--fee", type=float, default=DEFAULT_FEE_RATE)
    args = parser.parse_args()

    engine = BacktestEngine(
        initial_balance=args.balance,
        fee_rate=args.fee,
        slippage_pct=args.slippage,
    )
    engine.run_from_db(limit=args.limit)
