"""
<<<<<<< HEAD
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

=======
scripts/backtest_engine.py — AX Backtest Engine v5.1 (PAPER-ONLY / LIVE-BLOCKED)
=================================================================
core/accounting.py fonksiyonlarını kullanarak backtest yapar.
risk_breach ve margin_breach gerçek ihlal sayısını hesaplar.
Coin, setup_quality ve yön bazında kırılım raporlar.
"""
>>>>>>> 0797c70b8640d2006e47a50d5580ffae4606199b
import os
import sys
import argparse
import logging
<<<<<<< HEAD
import json
import sqlite3
from datetime import datetime, timezone
from typing import Optional
=======
from datetime import datetime, timezone
from collections import defaultdict
>>>>>>> 0797c70b8640d2006e47a50d5580ffae4606199b

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.accounting import (
<<<<<<< HEAD
    calculate_pnl,
    calculate_partial_close_pnl,
    calculate_fee,
=======
    calculate_partial_close_pnl,
    calculate_position_size,
>>>>>>> 0797c70b8640d2006e47a50d5580ffae4606199b
    calculate_r_multiple,
    calculate_margin_loss_pct,
)
from config import (
<<<<<<< HEAD
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
=======
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
>>>>>>> 0797c70b8640d2006e47a50d5580ffae4606199b
        }
        self.results.append(result)
        return result

<<<<<<< HEAD
    # ── DB'den backtest ───────────────────────────────────────────

    def run_from_db(self, limit: int = 500) -> dict:
        """DB'deki geçmiş trade'lerden backtest yap."""
        conn = sqlite3.connect(DB_PATH, timeout=10)
=======
    def run_from_db(self, limit: int = 500) -> dict:
        """DB'deki geçmiş trade'lerden backtest yap."""
        import sqlite3
        conn = sqlite3.connect(DB_PATH)
>>>>>>> 0797c70b8640d2006e47a50d5580ffae4606199b
        conn.row_factory = sqlite3.Row

        rows = conn.execute("""
            SELECT * FROM trades
<<<<<<< HEAD
            WHERE status='CLOSED'
=======
            WHERE status='closed' AND is_valid_for_stats=1
>>>>>>> 0797c70b8640d2006e47a50d5580ffae4606199b
            ORDER BY id
            LIMIT ?
        """, (limit,)).fetchall()
        conn.close()

        if not rows:
<<<<<<< HEAD
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
=======
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
>>>>>>> 0797c70b8640d2006e47a50d5580ffae4606199b

            if not entry or not sl:
                continue

<<<<<<< HEAD
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
=======
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
>>>>>>> 0797c70b8640d2006e47a50d5580ffae4606199b
            }
            self._print_report(report)
            return report

        total = len(self.results)
<<<<<<< HEAD
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
=======
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
>>>>>>> 0797c70b8640d2006e47a50d5580ffae4606199b
        }
        self._print_report(report)
        return report

<<<<<<< HEAD
    def _print_report(self, r: dict) -> None:
        print("\n" + "=" * 60)
        print("📈  AX BACKTEST PERFORMANS RAPORU v6.0")
        print("=" * 60)
        for k, v in r.items():
            label = k.replace("_", " ").title()
            if isinstance(v, float):
                print(f"  {label}: {v:.4f}")
            else:
                print(f"  {label}: {v}")

        ev = r.get("expectancy", 0)
        if ev > 0:
            print("\n✅ Pozitif expectancy — strateji karlı")
        else:
            print("\n⚠️  Negatif/sıfır expectancy — optimizasyon gerekli")

        wr = r.get("win_rate", 0)
        if isinstance(wr, float):
            print(f"📊 Win Rate: {wr*100:.1f}%  |  PF: {r.get('profit_factor',0):.2f}")
        print("=" * 60)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="AX Backtest Engine v6.0")
    parser.add_argument("--limit", type=int, default=500, help="DB'den max trade sayısı")
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
=======
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

    def run_walk_forward(self, n_splits: int = 3) -> dict:
        """
        Walk-forward analiz: veriyi n_splits eşit parçaya böler.
        Her parça için bağımsız backtest çalıştırır.
        Sonuçların dönemler arasında tutarlı olup olmadığını gösterir.
        """
        import sqlite3

        conn = sqlite3.connect(DB_PATH)
        conn.row_factory = sqlite3.Row
        rows = conn.execute("""
            SELECT * FROM trades
            WHERE status='closed' AND is_valid_for_stats=1
            ORDER BY id
        """).fetchall()
        conn.close()

        if not rows:
            logger.warning("[WF] DB'de closed trade bulunamadı.")
            return {}

        total = len(rows)
        size  = total // n_splits
        if size < 5:
            logger.warning(f"[WF] Bölüm başına {size} trade — yeterli veri yok (min 5).")
            return {}

        print(f"\n{'='*55}")
        print(f"WALK-FORWARD ANALİZ — {n_splits} dönem, {total} trade")
        print(f"{'='*55}")

        period_reports = []

        for i in range(n_splits):
            start_idx = i * size
            end_idx   = (i + 1) * size if i < n_splits - 1 else total
            period    = rows[start_idx:end_idx]

            engine = BacktestEngine(
                initial_balance=self.initial_balance,
                fee_rate=self.fee_rate
            )

            for r in period:
                entry     = float(r["entry"] or 0)
                sl        = float(r["sl"] or 0)
                tp1       = float(r["tp1"] or 0)
                tp2       = float(r["tp2"] or 0) or tp1 * 1.02
                direction = r["direction"] or "LONG"
                leverage  = int(r["leverage"] or 10)
                symbol    = r["symbol"] or ""
                sq        = r["setup_quality"] or ""
                regime    = r["market_regime"] or ""

                if not entry or not sl:
                    continue

                if r["tp2_hit"]:
                    outcome = "tp2"
                elif r["tp1_hit"]:
                    outcome = "tp1"
                elif float(r["net_pnl"] or 0) > 0:
                    outcome = "tp1"
                else:
                    outcome = "sl"

                engine.simulate_trade(
                    entry, sl, tp1, tp2, direction, leverage,
                    outcome=outcome, symbol=symbol,
                    setup_quality=sq, market_regime=regime
                )

            n   = len(engine.results)
            wins = sum(1 for r in engine.results if r["net_pnl"] > 0)
            pnl  = sum(r["net_pnl"] for r in engine.results)
            avg_r = sum(r["r_multiple"] for r in engine.results) / n if n > 0 else 0
            wr    = wins / n if n > 0 else 0
            gp    = sum(r["net_pnl"] for r in engine.results if r["net_pnl"] > 0)
            gl    = abs(sum(r["net_pnl"] for r in engine.results if r["net_pnl"] < 0))
            pf    = round(gp / gl, 2) if gl > 0 else 0
            exp   = (wr * (gp / wins if wins > 0 else 0)
                     - (1 - wr) * (gl / (n - wins) if n - wins > 0 else 0))

            report = {
                "period":          i + 1,
                "trades":          n,
                "win_rate":        round(wr, 4),
                "profit_factor":   pf,
                "expectancy":      round(exp, 4),
                "avg_r":           round(avg_r, 3),
                "total_pnl":       round(pnl, 4),
                "max_drawdown":    round(engine.max_drawdown, 4),
            }
            period_reports.append(report)

            sign = "+" if pnl >= 0 else ""
            print(
                f"  Dönem {i+1}: {n} trade | WR={wr:.1%} | PF={pf} | "
                f"Exp={exp:.4f} | PnL={sign}{pnl:.4f}"
            )

        positive_periods = sum(1 for r in period_reports if r["expectancy"] > 0)
        all_positive     = positive_periods == n_splits
        consistent       = positive_periods >= n_splits * 0.67

        print(f"\n  Pozitif dönem: {positive_periods}/{n_splits}")
        if all_positive:
            print("  Tutarlı pozitif expectancy — sistem tüm dönemlerde çalışıyor.")
        elif consistent:
            print("  Genel olarak tutarlı — bazı zayıf dönemler var, dikkatli ol.")
        else:
            print("  Tutarsız sonuçlar — sistemi canlıya ALMA. Daha fazla veri gerekli.")
        print(f"{'='*55}\n")

        return {
            "periods":          period_reports,
            "n_splits":         n_splits,
            "positive_periods": positive_periods,
            "consistent":       consistent,
            "all_positive":     all_positive,
        }


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="AX Backtest Engine v5.1")
    parser.add_argument("--limit",        type=int,   default=500)
    parser.add_argument("--balance",      type=float, default=250.0)
    parser.add_argument("--walk-forward", action="store_true",
                        help="Walk-forward analiz çalıştır")
    parser.add_argument("--splits",       type=int,   default=3,
                        help="Walk-forward dönem sayısı (varsayılan: 3)")
    args = parser.parse_args()

    engine = BacktestEngine(initial_balance=args.balance)

    if args.walk_forward:
        engine.run_walk_forward(n_splits=args.splits)
    else:
        engine.run_from_db(limit=args.limit)
>>>>>>> 0797c70b8640d2006e47a50d5580ffae4606199b
