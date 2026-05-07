"""
scripts/backtest_engine.py — AX Backtest Engine v5.1 (PAPER-ONLY / LIVE-BLOCKED)
=================================================================
core/accounting.py fonksiyonlarını kullanarak backtest yapar.
risk_breach ve margin_breach gerçek ihlal sayısını hesaplar.
Coin, setup_quality ve yön bazında kırılım raporlar.
"""
import os
import sys
import argparse
import logging
from datetime import datetime, timezone
from collections import defaultdict

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.accounting import (
    calculate_partial_close_pnl,
    calculate_position_size,
    calculate_r_multiple,
    calculate_margin_loss_pct,
)
from config import (
    DB_PATH, DEFAULT_FEE_RATE, TP1_CLOSE_PCT, TP2_CLOSE_PCT,
    MAX_MARGIN_LOSS_PCT,
)

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger("backtest")


class BacktestEngine:
    def __init__(self, initial_balance: float = 250.0, fee_rate: float = None):
        self.initial_balance = initial_balance
        self.balance         = initial_balance
        self.fee_rate        = fee_rate or DEFAULT_FEE_RATE
        self.results         = []
        self.peak_balance    = initial_balance
        self.max_drawdown    = 0.0
        self.risk_breaches   = 0
        self.margin_breaches = 0

    def simulate_trade(self, entry: float, sl: float, tp1: float, tp2: float,
                       direction: str, leverage: int = 10, risk_pct: float = 1.0,
                       outcome: str = "tp1", symbol: str = "",
                       setup_quality: str = "", market_regime: str = "") -> dict | None:
        """
        Tek trade simülasyonu.
        accounting.py v5.1 fee mimarisi: open_fee tek seferlik,
        partial close'larda sadece exit-side fee.
        """
        pos = calculate_position_size(
            self.balance, risk_pct, entry, sl, leverage, self.fee_rate
        )
        if not pos.get("valid"):
            return None

        # Margin ihlali kontrolü
        mlp = calculate_margin_loss_pct(entry, sl, leverage)
        if mlp > MAX_MARGIN_LOSS_PCT:
            self.margin_breaches += 1
            return {"skipped": True, "reason": "margin_breach"}

        qty        = pos["qty"]
        qty_tp1    = pos["qty_tp1"]
        qty_tp2    = pos["qty_tp2"]
        qty_runner = pos["qty_runner"]
        risk_usd   = pos["risk_usd"]

        # Risk ihlali kontrolü: max_loss_after_fee > risk_usd * 1.05
        if pos["max_loss_after_fee"] > risk_usd * 1.05:
            self.risk_breaches += 1

        # open_fee: açılışta tek seferlik
        total_fee = pos["open_fee"]
        total_pnl = 0.0

        if outcome == "sl":
            pnl, fee = calculate_partial_close_pnl(direction, entry, sl, qty, self.fee_rate)
            total_pnl  = pnl
            total_fee += fee

        elif outcome == "tp1":
            pnl1, fee1 = calculate_partial_close_pnl(direction, entry, tp1, qty_tp1, self.fee_rate)
            pnl2, fee2 = calculate_partial_close_pnl(direction, entry, entry, qty_tp2 + qty_runner, self.fee_rate)
            total_pnl  = pnl1 + pnl2
            total_fee += fee1 + fee2

        elif outcome == "tp2":
            pnl1, fee1 = calculate_partial_close_pnl(direction, entry, tp1, qty_tp1, self.fee_rate)
            pnl2, fee2 = calculate_partial_close_pnl(direction, entry, tp2, qty_tp2, self.fee_rate)
            pnl3, fee3 = calculate_partial_close_pnl(direction, entry, entry, qty_runner, self.fee_rate)
            total_pnl  = pnl1 + pnl2 + pnl3
            total_fee += fee1 + fee2 + fee3

        elif outcome == "full_win":
            runner_exit = tp2 * 1.02 if direction == "LONG" else tp2 * 0.98
            pnl1, fee1 = calculate_partial_close_pnl(direction, entry, tp1, qty_tp1, self.fee_rate)
            pnl2, fee2 = calculate_partial_close_pnl(direction, entry, tp2, qty_tp2, self.fee_rate)
            pnl3, fee3 = calculate_partial_close_pnl(direction, entry, runner_exit, qty_runner, self.fee_rate)
            total_pnl  = pnl1 + pnl2 + pnl3
            total_fee += fee1 + fee2 + fee3

        r_multiple = calculate_r_multiple(total_pnl, risk_usd)

        self.balance += total_pnl
        if self.balance > self.peak_balance:
            self.peak_balance = self.balance
        dd = self.peak_balance - self.balance
        if dd > self.max_drawdown:
            self.max_drawdown = dd

        result = {
            "symbol":        symbol,
            "direction":     direction,
            "entry":         entry,
            "sl":            sl,
            "tp1":           tp1,
            "tp2":           tp2,
            "outcome":       outcome,
            "net_pnl":       round(total_pnl, 4),
            "total_fee":     round(total_fee, 6),
            "r_multiple":    r_multiple,
            "balance_after": round(self.balance, 4),
            "risk_usd":      round(risk_usd, 4),
            "setup_quality": setup_quality,
            "market_regime": market_regime,
        }
        self.results.append(result)
        return result

    def run_from_db(self, limit: int = 500) -> dict:
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
            entry     = float(r["entry"] or 0)
            sl        = float(r["sl"] or 0)
            tp1       = float(r["tp1"] or 0)
            tp2       = float(r["tp2"] or 0) or (tp1 * 1.02 if tp1 else 0)
            direction = r["direction"] or "LONG"
            leverage  = int(r["leverage"] or 10)
            sq        = r["setup_quality"] or ""
            regime    = r["market_regime"] or ""
            symbol    = r["symbol"] or ""

            if not entry or not sl:
                continue

            tp1_hit = r["tp1_hit"] if "tp1_hit" in r.keys() else 0
            tp2_hit = r["tp2_hit"] if "tp2_hit" in r.keys() else 0

            if tp2_hit:
                outcome = "tp2"
            elif tp1_hit:
                outcome = "tp1"
            elif float(r["net_pnl"] or 0) > 0:
                outcome = "tp1"
            else:
                outcome = "sl"

            self.simulate_trade(
                entry, sl, tp1 or entry * 1.01, tp2 or entry * 1.02,
                direction, leverage,
                outcome=outcome, symbol=symbol,
                setup_quality=sq, market_regime=regime
            )

        return self.generate_report()

    def generate_report(self) -> dict:
        if not self.results:
            report = {
                "total_trades": 0, "win_rate": 0, "profit_factor": 0,
                "expectancy": 0, "avg_r": 0, "max_drawdown": 0,
                "total_fees": 0, "tp1_hit_rate": 0, "tp2_hit_rate": 0,
                "final_balance": self.balance, "initial_balance": self.initial_balance,
                "net_return_pct": 0, "risk_breach": 0, "margin_breach": 0,
            }
            self._print_report(report)
            return report

        total = len(self.results)
        wins   = [r for r in self.results if r["net_pnl"] > 0]
        losses = [r for r in self.results if r["net_pnl"] <= 0]

        win_rate    = len(wins) / total if total > 0 else 0
        avg_win     = sum(r["net_pnl"] for r in wins) / len(wins) if wins else 0
        avg_loss    = abs(sum(r["net_pnl"] for r in losses) / len(losses)) if losses else 0
        expectancy  = win_rate * avg_win - (1 - win_rate) * avg_loss
        gross_profit = sum(r["net_pnl"] for r in wins)
        gross_loss   = abs(sum(r["net_pnl"] for r in losses))
        pf           = round(gross_profit / gross_loss, 2) if gross_loss > 0 else 0

        tp1_hits = sum(1 for r in self.results if r["outcome"] in ("tp1", "tp2", "full_win"))
        tp2_hits = sum(1 for r in self.results if r["outcome"] in ("tp2", "full_win"))

        # Coin bazında kırılım
        coin_stats = defaultdict(lambda: {"n": 0, "pnl": 0.0, "wins": 0})
        for r in self.results:
            sym = r["symbol"] or "UNKNOWN"
            coin_stats[sym]["n"]    += 1
            coin_stats[sym]["pnl"]  += r["net_pnl"]
            if r["net_pnl"] > 0:
                coin_stats[sym]["wins"] += 1

        coin_results = {
            sym: {
                "n":        v["n"],
                "pnl":      round(v["pnl"], 4),
                "win_rate": round(v["wins"] / v["n"], 4) if v["n"] > 0 else 0,
            }
            for sym, v in sorted(coin_stats.items(), key=lambda x: -x[1]["pnl"])
        }

        # Setup quality kırılımı
        sq_stats = defaultdict(lambda: {"n": 0, "pnl": 0.0, "wins": 0})
        for r in self.results:
            sq = r["setup_quality"] or "UNKNOWN"
            sq_stats[sq]["n"]    += 1
            sq_stats[sq]["pnl"]  += r["net_pnl"]
            if r["net_pnl"] > 0:
                sq_stats[sq]["wins"] += 1

        setup_quality_results = {
            sq: {
                "n":        v["n"],
                "pnl":      round(v["pnl"], 4),
                "win_rate": round(v["wins"] / v["n"], 4) if v["n"] > 0 else 0,
            }
            for sq, v in sq_stats.items()
        }

        # Yön kırılımı
        long_r  = [r for r in self.results if str(r["direction"]).upper() == "LONG"]
        short_r = [r for r in self.results if str(r["direction"]).upper() == "SHORT"]

        def dir_stats(items):
            if not items:
                return {"n": 0, "pnl": 0, "win_rate": 0}
            w = sum(1 for r in items if r["net_pnl"] > 0)
            return {
                "n":        len(items),
                "pnl":      round(sum(r["net_pnl"] for r in items), 4),
                "win_rate": round(w / len(items), 4),
            }

        # Runner katkısı (tp2 ve full_win sonuçlarından)
        runner_trades = [r for r in self.results if r["outcome"] in ("tp2", "full_win")]
        runner_contribution = 0.0
        if runner_trades and gross_profit > 0:
            runner_contribution = round(
                sum(r["net_pnl"] for r in runner_trades) / gross_profit, 4
            )

        report = {
            "total_trades":          total,
            "win_rate":              round(win_rate, 4),
            "profit_factor":         pf,
            "expectancy":            round(expectancy, 4),
            "avg_r":                 round(sum(r["r_multiple"] for r in self.results) / total, 3),
            "avg_win":               round(avg_win, 4),
            "avg_loss":              round(avg_loss, 4),
            "max_drawdown":          round(self.max_drawdown, 4),
            "total_fees":            round(sum(r["total_fee"] for r in self.results), 4),
            "tp1_hit_rate":          round(tp1_hits / total, 4) if total else 0,
            "tp2_hit_rate":          round(tp2_hits / total, 4) if total else 0,
            "runner_contribution":   runner_contribution,
            "final_balance":         round(self.balance, 4),
            "initial_balance":       self.initial_balance,
            "net_return_pct":        round((self.balance - self.initial_balance) / self.initial_balance * 100, 2),
            "risk_breach":           self.risk_breaches,
            "margin_breach":         self.margin_breaches,
            "long_stats":            dir_stats(long_r),
            "short_stats":           dir_stats(short_r),
            "coin_results":          coin_results,
            "setup_quality_results": setup_quality_results,
        }
        self._print_report(report)
        return report

    def _print_report(self, r: dict):
        print("\n" + "=" * 55)
        print("BACKTEST PERFORMANS RAPORU — AX v5.1")
        print("=" * 55)

        core_fields = [
            "total_trades", "win_rate", "profit_factor", "expectancy",
            "avg_r", "avg_win", "avg_loss", "max_drawdown",
            "total_fees", "tp1_hit_rate", "tp2_hit_rate", "runner_contribution",
            "final_balance", "initial_balance", "net_return_pct",
            "risk_breach", "margin_breach",
        ]
        for k in core_fields:
            if k in r:
                label = k.replace("_", " ").title()
                print(f"  {label:<28} {r[k]}")

        if r.get("long_stats") and r.get("short_stats"):
            print(f"\n  LONG  ({r['long_stats']['n']} trade): pnl={r['long_stats']['pnl']} wr={r['long_stats']['win_rate']:.1%}")
            print(f"  SHORT ({r['short_stats']['n']} trade): pnl={r['short_stats']['pnl']} wr={r['short_stats']['win_rate']:.1%}")

        if r.get("setup_quality_results"):
            print("\n  SETUP QUALITY KIRILIMLARI:")
            for sq, v in r["setup_quality_results"].items():
                print(f"    {sq:<6} n={v['n']:<4} pnl={v['pnl']:<8} wr={v['win_rate']:.1%}")

        if r.get("coin_results"):
            print("\n  COIN KIRILIMLARI (en iyi 10):")
            for sym, v in list(r["coin_results"].items())[:10]:
                print(f"    {sym:<15} n={v['n']:<4} pnl={v['pnl']:<8} wr={v['win_rate']:.1%}")

        print()
        if r.get("margin_breach", 0) > 0:
            print(f"  UYARI: {r['margin_breach']} margin ihlali tespit edildi.")
        if r.get("risk_breach", 0) > 0:
            print(f"  UYARI: {r['risk_breach']} risk ihlali tespit edildi.")
        if r.get("expectancy", 0) > 0:
            print("  Pozitif expectancy — paper mode devam ettirilebilir.")
        else:
            print("  Negatif/sıfır expectancy — canlıya geçiş ÖNERİLMEZ.")
        print("=" * 55)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="AX Backtest Engine v5.1")
    parser.add_argument("--limit",   type=int,   default=500)
    parser.add_argument("--balance", type=float, default=250.0)
    args = parser.parse_args()

    engine = BacktestEngine(initial_balance=args.balance)
    engine.run_from_db(limit=args.limit)
