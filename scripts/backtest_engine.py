"""
scripts/backtest_engine.py — AX Backtest Engine v5.0 (PAPER-ONLY / LIVE-BLOCKED)
=================================================================
FAZ 13: core/accounting.py fonksiyonlarını kullanarak backtest yapar.
"""
import os
import sys
import argparse
import logging
from datetime import datetime, timezone

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.accounting import (
    calculate_pnl, calculate_fee, calculate_position_size,
    calculate_partial_close_pnl, calculate_r_multiple,
    calculate_max_loss_after_fee, calculate_margin_loss_pct,
)
from config import (
    DB_PATH, DEFAULT_FEE_RATE, TP1_CLOSE_PCT, TP2_CLOSE_PCT,
    MAX_MARGIN_LOSS_PCT,
)

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("backtest")


class BacktestEngine:
    def __init__(self, initial_balance=250.0, fee_rate=None):
        self.initial_balance = initial_balance
        self.balance = initial_balance
        self.fee_rate = fee_rate or DEFAULT_FEE_RATE
        self.trades = []
        self.results = []
        self.peak_balance = initial_balance
        self.max_drawdown = 0

    def simulate_trade(self, entry, sl, tp1, tp2, direction, leverage=10,
                       risk_pct=1.0, outcome="tp1"):
        """Tek trade simülasyonu — accounting fonksiyonları ile."""
        # Pozisyon hesapla
        pos = calculate_position_size(
            self.balance, risk_pct, entry, sl, leverage, self.fee_rate
        )
        if not pos.get("valid"):
            return None

        qty = pos["qty"]
        qty_tp1 = pos["qty_tp1"]
        qty_tp2 = pos["qty_tp2"]
        qty_runner = pos["qty_runner"]
        risk_usd = pos["risk_usd"]

        # Margin loss kontrolü
        mlp = calculate_margin_loss_pct(entry, sl, leverage)
        if mlp > MAX_MARGIN_LOSS_PCT:
            return {"skipped": True, "reason": "margin_breach"}

        total_pnl = 0
        total_fee = pos["open_fee"]

        if outcome == "sl":
            # Full SL
            pnl, fee = calculate_partial_close_pnl(direction, entry, sl, qty, self.fee_rate)
            total_pnl = pnl
            total_fee += fee
        elif outcome == "tp1":
            # TP1 hit, then SL at BE
            pnl1, fee1 = calculate_partial_close_pnl(direction, entry, tp1, qty_tp1, self.fee_rate)
            pnl2, fee2 = calculate_partial_close_pnl(direction, entry, entry, qty_tp2 + qty_runner, self.fee_rate)
            total_pnl = pnl1 + pnl2
            total_fee += fee1 + fee2
        elif outcome == "tp2":
            # TP1 + TP2 hit, runner at BE
            pnl1, fee1 = calculate_partial_close_pnl(direction, entry, tp1, qty_tp1, self.fee_rate)
            pnl2, fee2 = calculate_partial_close_pnl(direction, entry, tp2, qty_tp2, self.fee_rate)
            pnl3, fee3 = calculate_partial_close_pnl(direction, entry, entry, qty_runner, self.fee_rate)
            total_pnl = pnl1 + pnl2 + pnl3
            total_fee += fee1 + fee2 + fee3
        elif outcome == "full_win":
            # TP1 + TP2 + runner at TP2*1.02
            runner_exit = tp2 * 1.02 if direction == "LONG" else tp2 * 0.98
            pnl1, fee1 = calculate_partial_close_pnl(direction, entry, tp1, qty_tp1, self.fee_rate)
            pnl2, fee2 = calculate_partial_close_pnl(direction, entry, tp2, qty_tp2, self.fee_rate)
            pnl3, fee3 = calculate_partial_close_pnl(direction, entry, runner_exit, qty_runner, self.fee_rate)
            total_pnl = pnl1 + pnl2 + pnl3
            total_fee += fee1 + fee2 + fee3

        r_multiple = calculate_r_multiple(total_pnl, risk_usd)

        # Balance güncelle
        self.balance += total_pnl

        # Drawdown
        if self.balance > self.peak_balance:
            self.peak_balance = self.balance
        dd = self.peak_balance - self.balance
        if dd > self.max_drawdown:
            self.max_drawdown = dd

        result = {
            "direction": direction,
            "entry": entry,
            "sl": sl,
            "tp1": tp1,
            "tp2": tp2,
            "outcome": outcome,
            "net_pnl": round(total_pnl, 4),
            "total_fee": round(total_fee, 6),
            "r_multiple": r_multiple,
            "balance_after": round(self.balance, 4),
            "risk_usd": round(risk_usd, 4),
        }
        self.results.append(result)
        return result

    def run_from_db(self, limit=500):
        """DB'deki geçmiş trade'lerden backtest yap."""
        import sqlite3
        conn = sqlite3.connect(DB_PATH)
        conn.row_factory = sqlite3.Row

        rows = conn.execute("""
            SELECT * FROM trades
            WHERE status='closed' AND is_valid_for_stats=1
            ORDER BY id
            LIMIT ?
        """, (limit,)).fetchall()
        conn.close()

        if not rows:
            logger.warning("DB'de closed trade bulunamadı.")
            return self.generate_report()

        for r in rows:
            entry = r["entry"] or 0
            sl = r["sl"] or 0
            tp1 = r["tp1"] or 0
            tp2 = r["tp2"] or tp1 * 1.02
            direction = r["direction"] or "LONG"
            leverage = r["leverage"] or 10

            if not entry or not sl:
                continue

            # Outcome belirle
            if r["tp2_hit"]:
                outcome = "tp2"
            elif r["tp1_hit"]:
                outcome = "tp1"
            elif (r["net_pnl"] or 0) > 0:
                outcome = "tp1"
            else:
                outcome = "sl"

            self.simulate_trade(entry, sl, tp1, tp2, direction, leverage, outcome=outcome)

        return self.generate_report()

    def generate_report(self):
        """Performans raporu üret."""
        if not self.results:
            report = {
                "total_trades": 0, "win_rate": 0, "profit_factor": 0,
                "expectancy": 0, "avg_r": 0, "max_drawdown": 0,
                "total_fees": 0, "tp1_hit_rate": 0, "tp2_hit_rate": 0,
                "final_balance": self.balance,
                "risk_breach": 0, "margin_breach": 0,
            }
            self._print_report(report)
            return report

        total = len(self.results)
        wins = [r for r in self.results if r["net_pnl"] > 0]
        losses = [r for r in self.results if r["net_pnl"] <= 0]
        win_rate = len(wins) / total if total > 0 else 0
        avg_win = sum(r["net_pnl"] for r in wins) / len(wins) if wins else 0
        avg_loss = abs(sum(r["net_pnl"] for r in losses) / len(losses)) if losses else 0
        expectancy = win_rate * avg_win - (1 - win_rate) * avg_loss
        gross_profit = sum(r["net_pnl"] for r in wins)
        gross_loss = abs(sum(r["net_pnl"] for r in losses))
        pf = gross_profit / gross_loss if gross_loss > 0 else 0

        tp1_hits = sum(1 for r in self.results if r["outcome"] in ("tp1", "tp2", "full_win"))
        tp2_hits = sum(1 for r in self.results if r["outcome"] in ("tp2", "full_win"))

        report = {
            "total_trades": total,
            "win_rate": round(win_rate, 4),
            "profit_factor": round(pf, 2),
            "expectancy": round(expectancy, 4),
            "avg_r": round(sum(r["r_multiple"] for r in self.results) / total, 3),
            "max_drawdown": round(self.max_drawdown, 4),
            "total_fees": round(sum(r["total_fee"] for r in self.results), 4),
            "tp1_hit_rate": round(tp1_hits / total, 4) if total else 0,
            "tp2_hit_rate": round(tp2_hits / total, 4) if total else 0,
            "final_balance": round(self.balance, 4),
            "initial_balance": self.initial_balance,
            "net_return_pct": round((self.balance - self.initial_balance) / self.initial_balance * 100, 2),
            "risk_breach": 0,
            "margin_breach": 0,
        }
        self._print_report(report)
        return report

    def _print_report(self, r):
        print("\n" + "=" * 50)
        print("📈 BACKTEST PERFORMANS RAPORU")
        print("=" * 50)
        for k, v in r.items():
            label = k.replace("_", " ").title()
            print(f"  {label}: {v}")

        if r.get("expectancy", 0) > 0:
            print("\n✅ Pozitif expectancy — canlıya geçiş uygun")
        else:
            print("\n⚠️ Negatif/sıfır expectancy — canlıya ÖNERİLMEZ")
        print("=" * 50)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="AX Backtest Engine")
    parser.add_argument("--limit", type=int, default=500)
    parser.add_argument("--balance", type=float, default=250.0)
    args = parser.parse_args()

    engine = BacktestEngine(initial_balance=args.balance)
    engine.run_from_db(limit=args.limit)
