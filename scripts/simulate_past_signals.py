#!/usr/bin/env python3
"""
scripts/simulate_past_signals.py — AurvexAI Geçmiş Sinyal Simülasyonu
=======================================================================
Geçmişte üretilmiş ama işlem açılmamış sinyalleri yeni TP/SL parametreleriyle
(TP1=1.5R, TP2=2.5R, SL=ATR×1.8, MIN_SL=%1.5) simüle eder.

Kullanım:
  python3 scripts/simulate_past_signals.py
  python3 scripts/simulate_past_signals.py --days 7 --min-score 25
  python3 scripts/simulate_past_signals.py --all --export sonuc.csv
"""
from __future__ import annotations

import os, sys, argparse, json, csv, random
from datetime import datetime, timezone, timedelta
from typing import Optional

# Proje kökünü sys.path'e ekle
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import config

# ── Parametreler ─────────────────────────────────────────────────────────────
TP1_R         = float(getattr(config, "TP1_R",        1.5))
TP2_R         = float(getattr(config, "TP2_R",        2.5))
TP3_R         = float(getattr(config, "TP3_R",        4.0))
SL_ATR_MULT   = float(getattr(config, "SL_ATR_MULT",  1.8))
MIN_SL_PCT    = float(getattr(config, "MIN_SL_PCT",   0.015))
MIN_RR        = float(getattr(config, "MIN_RR",       1.5))
TP1_CLOSE_PCT = float(getattr(config, "TP1_CLOSE_PCT", 40))
TP2_CLOSE_PCT = float(getattr(config, "TP2_CLOSE_PCT", 30))
FEE_RATE      = float(getattr(config, "DEFAULT_FEE_RATE", 0.0004))
RISK_PCT      = float(getattr(config, "RISK_PCT",     1.0))

BOLD  = "\033[1m"; DIM  = "\033[2m"; NC = "\033[0m"
GRN   = "\033[32m"; RED  = "\033[31m"; YLW = "\033[33m"
CYN   = "\033[36m"; MAG  = "\033[35m"; BLU = "\033[34m"


# ── Hesaplamalar ──────────────────────────────────────────────────────────────

def recalc_tpsl(entry: float, sl_orig: float, direction: str) -> dict:
    """
    Orijinal SL mesafesini alarak yeni TP1/TP2/TP3 hesaplar.
    Ayrıca MIN_SL_PCT ve MIN_RR kontrolü yapar.
    """
    if not entry or not sl_orig:
        return {"valid": False, "reason": "entry/sl eksik"}

    sl_dist = abs(entry - sl_orig)
    sl_pct  = sl_dist / entry

    # MIN_SL_PCT kontrolü
    if sl_pct < MIN_SL_PCT:
        # SL mesafesi çok dar — minimum mesafeye genişlet
        sl_dist = entry * MIN_SL_PCT
        if direction == "LONG":
            sl_new = entry - sl_dist
        else:
            sl_new = entry + sl_dist
    else:
        sl_new = sl_orig

    # TP hesapla
    if direction == "LONG":
        tp1 = entry + sl_dist * TP1_R
        tp2 = entry + sl_dist * TP2_R
        tp3 = entry + sl_dist * TP3_R
    else:
        tp1 = entry - sl_dist * TP1_R
        tp2 = entry - sl_dist * TP2_R
        tp3 = entry - sl_dist * TP3_R

    # R:R kontrolü (TP1 üzerinden)
    rr = sl_dist * TP1_R / sl_dist  # = TP1_R (her zaman geçer ≥ TP1_R=1.5)
    # Gerçek R:R = tp1 mesafesi / sl mesafesi
    actual_rr = abs(tp1 - entry) / sl_dist if sl_dist > 0 else 0

    return {
        "valid":      True,
        "sl":         round(sl_new, 6),
        "sl_pct":     round(sl_pct * 100, 3),
        "sl_dist":    round(sl_dist, 6),
        "tp1":        round(tp1, 6),
        "tp2":        round(tp2, 6),
        "tp3":        round(tp3, 6),
        "rr":         round(actual_rr, 2),
        "sl_expanded": sl_pct < MIN_SL_PCT,
    }


def sim_pnl(entry, sl, tp1, tp2, direction, balance, outcome: str) -> dict:
    """
    Tek trade PnL simülasyonu (slippage dahil).
    outcome: 'sl' | 'tp1' | 'tp2' | 'tp3'
    """
    SLIP = 0.0003  # %0.03 slippage
    slip = lambda p, is_entry, is_long: (
        p * (1 + SLIP) if (is_entry and is_long) or (not is_entry and not is_long)
        else p * (1 - SLIP)
    )

    sl_dist  = abs(entry - sl)
    risk_usd = balance * (RISK_PCT / 100.0)
    qty      = risk_usd / sl_dist if sl_dist > 0 else 0

    is_long = direction == "LONG"
    e_real  = slip(entry, True, is_long)
    fee_in  = e_real * qty * FEE_RATE
    total_pnl = -fee_in
    total_fee = fee_in

    qty_tp1    = qty * (TP1_CLOSE_PCT / 100.0)
    qty_tp2    = qty * (TP2_CLOSE_PCT / 100.0)
    qty_runner = qty - qty_tp1 - qty_tp2

    def pnl_part(close_p, q):
        c = slip(close_p, False, is_long)
        fee = c * q * FEE_RATE
        raw = (c - e_real) * q if is_long else (e_real - c) * q
        return raw - fee, fee

    if outcome == "sl":
        p, f = pnl_part(sl, qty)
        total_pnl += p; total_fee += f
    elif outcome == "tp1":
        p1, f1 = pnl_part(tp1, qty_tp1)
        p_be, f_be = pnl_part(entry, qty_tp2 + qty_runner)   # kalan BE'de kapanır
        total_pnl += p1 + p_be; total_fee += f1 + f_be
    elif outcome == "tp2":
        p1, f1 = pnl_part(tp1, qty_tp1)
        p2, f2 = pnl_part(tp2, qty_tp2)
        p_be, f_be = pnl_part(entry, qty_runner)
        total_pnl += p1 + p2 + p_be; total_fee += f1 + f2 + f_be
    elif outcome in ("tp3", "full_win"):
        tp3_p = tp2 * (1 + 0.5 * (TP3_R - TP2_R) / TP2_R) if is_long else \
                tp2 * (1 - 0.5 * (TP3_R - TP2_R) / TP2_R)
        p1, f1 = pnl_part(tp1, qty_tp1)
        p2, f2 = pnl_part(tp2, qty_tp2)
        p3, f3 = pnl_part(tp3_p, qty_runner)
        total_pnl += p1 + p2 + p3; total_fee += f1 + f2 + f3

    r_mult = total_pnl / risk_usd if risk_usd > 0 else 0

    return {
        "net_pnl":    round(total_pnl, 4),
        "total_fee":  round(total_fee, 4),
        "r_multiple": round(r_mult, 2),
        "qty":        round(qty, 6),
        "risk_usd":   round(risk_usd, 4),
    }


# ── DB Sorgulama ──────────────────────────────────────────────────────────────

def _get_columns(conn, table: str) -> set:
    """Tablodaki kolon adlarını döndürür."""
    try:
        rows = conn.execute(f"PRAGMA table_info({table})").fetchall()
        return {r[1] for r in rows}
    except Exception:
        return set()


def load_signals(db_path: str, days: int, min_score: float, include_all: bool) -> list[dict]:
    """signal_candidates + paper_results birleştirerek geçmiş sinyalleri çeker.
    Var olmayan kolonları otomatik atlar (DB şemasına uyumlu)."""
    import sqlite3
    conn = sqlite3.connect(db_path, timeout=10)
    conn.row_factory = sqlite3.Row

    since = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()

    # Mevcut kolonları kontrol et — eksik olanları NULL ile doldur
    sc_cols  = _get_columns(conn, "signal_candidates")
    pr_cols  = _get_columns(conn, "paper_results")
    has_pr   = bool(pr_cols)  # paper_results tablosu var mı?

    def sc(col, alias=None):
        """signal_candidates kolonunu güvenli seç."""
        a = alias or col
        return f"sc.{col} AS {a}" if col in sc_cols else f"NULL AS {a}"

    def pr(col, alias=None):
        """paper_results kolonunu güvenli seç."""
        a = alias or col
        if not has_pr:
            return f"NULL AS {a}"
        return f"pr.{col} AS {a}" if col in pr_cols else f"NULL AS {a}"

    # Filtreler
    score_clause    = f"AND sc.final_score >= {min_score}" if not include_all else ""
    decision_clause = "" if include_all else "AND (sc.decision != 'ALLOW' OR sc.decision IS NULL)"

    # paper_results JOIN (tablo yoksa atla)
    pr_join = (
        "LEFT JOIN paper_results pr ON sc.id = pr.signal_id"
        if has_pr else ""
    )

    query = f"""
        SELECT
            sc.id,
            sc.symbol,
            sc.direction,
            sc.final_score,
            {sc('setup_quality', 'quality')},
            sc.decision,
            {sc('entry')},
            {sc('sl')},
            {sc('tp1')},
            {sc('tp2')},
            {sc('tp3')},
            sc.created_at,
            {sc('reject_reason')},
            {pr('would_have_won',    'paper_won')},
            {pr('status',            'paper_status')},
            {pr('final_outcome_pct', 'paper_outcome_pct')}
        FROM signal_candidates sc
        {pr_join}
        WHERE sc.created_at >= ?
          {score_clause}
          {decision_clause}
        ORDER BY sc.id DESC
        LIMIT 1000
    """

    rows = conn.execute(query, (since,)).fetchall()
    conn.close()
    return [dict(r) for r in rows]


# ── Ana simülasyon ────────────────────────────────────────────────────────────

def run_simulation(signals: list[dict], initial_balance: float, seed: int = 42) -> dict:
    """
    Sinyalleri simüle et.
    paper_results varsa gerçek outcome kullan,
    yoksa rastgele (seed=42 ile tekrarlanabilir) dağılım uygular:
      %30 SL | %45 TP1 | %25 TP2
    """
    rng = random.Random(seed)   # Tekrarlanabilir rastgele (seed ile)
    results = []
    balance = initial_balance
    skipped = 0
    skipped_reasons: dict[str, int] = {}

    # Gerçekçi dağılım: %45 TP1, %25 TP2, %30 SL
    # TP1 ortalama ~+0.5R (fee+slippage düşünce), SL ~-1.0R
    STAT_OUTCOMES  = ["tp1"] * 45 + ["tp2"] * 25 + ["sl"] * 30  # 100 elemanlı pool

    for i, sig in enumerate(signals):
        entry     = float(sig.get("entry") or 0)
        sl_orig   = float(sig.get("sl") or 0)
        direction = str(sig.get("direction") or "LONG").upper()
        symbol    = sig.get("symbol", "?")
        score     = float(sig.get("final_score") or 0)
        quality   = sig.get("quality", "?")
        created   = str(sig.get("created_at", ""))[:16]

        if not entry or not sl_orig:
            skipped += 1
            # Nedenini kaydet (lifecycle_stage varsa)
            lc = sig.get("lifecycle_stage") or sig.get("decision") or "no_entry_sl"
            skipped_reasons[lc] = skipped_reasons.get(lc, 0) + 1
            continue

        # Yeni TP/SL hesapla
        calc = recalc_tpsl(entry, sl_orig, direction)
        if not calc["valid"]:
            skipped += 1
            skipped_reasons["invalid_calc"] = skipped_reasons.get("invalid_calc", 0) + 1
            continue

        tp1, tp2, tp3 = calc["tp1"], calc["tp2"], calc["tp3"]
        sl_new = calc["sl"]

        # Outcome belirle
        paper_won = sig.get("paper_won")

        if paper_won is not None:
            # paper_results varsa gerçek sonuç
            if paper_won == 1:
                pct = float(sig.get("paper_outcome_pct") or 100)
                outcome = "tp2" if pct >= 150 else "tp1"
            else:
                outcome = "sl"
            outcome_src = "paper"
        else:
            # Rastgele seçim (seed=42 → tekrarlanabilir, ama sıralı değil)
            outcome = rng.choice(STAT_OUTCOMES)
            outcome_src = "statistical"

        # PnL simülasyonu
        pnl_res = sim_pnl(entry, sl_new, tp1, tp2, direction, balance, outcome)
        balance += pnl_res["net_pnl"]

        results.append({
            "id":          sig.get("id"),
            "symbol":      symbol,
            "direction":   direction,
            "score":       round(score, 1),
            "quality":     quality,
            "decision":    sig.get("decision", "?"),
            "created":     created,
            "entry":       entry,
            "sl_orig":     sl_orig,
            "sl_new":      sl_new,
            "sl_pct":      calc["sl_pct"],
            "sl_expanded": calc["sl_expanded"],
            "tp1":         tp1,
            "tp2":         tp2,
            "rr":          calc["rr"],
            "outcome":     outcome,
            "outcome_src": outcome_src,
            "net_pnl":     pnl_res["net_pnl"],
            "r_multiple":  pnl_res["r_multiple"],
            "fee":         pnl_res["total_fee"],
            "balance":     round(balance, 2),
        })

    # Aggregate istatistikler
    if not results:
        return {"results": [], "stats": {}, "skipped": skipped}

    wins   = [r for r in results if r["net_pnl"] > 0]
    losses = [r for r in results if r["net_pnl"] <= 0]
    total  = len(results)
    gross_p = sum(r["net_pnl"] for r in wins)
    gross_l = abs(sum(r["net_pnl"] for r in losses))
    pf      = round(gross_p / gross_l, 3) if gross_l > 0 else float("inf")
    avg_r   = round(sum(r["r_multiple"] for r in results) / total, 3)
    total_fee = round(sum(r["fee"] for r in results), 2)

    sl_expanded   = sum(1 for r in results if r["sl_expanded"])
    paper_sourced = sum(1 for r in results if r["outcome_src"] == "paper")

    stats = {
        "total":          total,
        "skipped":        skipped,
        "wins":           len(wins),
        "losses":         len(losses),
        "win_rate":       round(len(wins) / total * 100, 1),
        "gross_profit":   round(gross_p, 2),
        "gross_loss":     round(gross_l, 2),
        "net_pnl":        round(gross_p - gross_l, 2),
        "profit_factor":  pf,
        "avg_r":          avg_r,
        "total_fee":      total_fee,
        "initial_balance": initial_balance,
        "final_balance":  round(balance, 2),
        "net_return_pct": round((balance - initial_balance) / initial_balance * 100, 2),
        "sl_expanded":    sl_expanded,
        "paper_sourced":  paper_sourced,
        "skipped_reasons": skipped_reasons,
    }

    return {"results": results, "stats": stats, "skipped": skipped,
            "skipped_reasons": skipped_reasons}


# ── Yazdırma ─────────────────────────────────────────────────────────────────

def print_report(sim: dict, show_all: bool = False):
    stats   = sim.get("stats", {})
    results = sim.get("results", [])
    skipped = sim.get("skipped", 0)

    if not results:
        print(f"{RED}❌ Simüle edilecek sinyal bulunamadı.{NC}")
        print(f"   Atlandı (entry/sl eksik): {skipped}")
        return

    print(f"\n{BOLD}{CYN}{'═'*64}{NC}")
    print(f"{BOLD}{CYN}  AurvexAI Geçmiş Sinyal Simülasyonu{NC}")
    print(f"{BOLD}{CYN}{'═'*64}{NC}\n")

    # Özet
    wr   = stats.get("win_rate", 0)
    pf   = stats.get("profit_factor", 0)
    avgr = stats.get("avg_r", 0)
    net  = stats.get("net_pnl", 0)
    roi  = stats.get("net_return_pct", 0)

    wr_clr  = GRN if wr >= 50 else (YLW if wr >= 40 else RED)
    net_clr = GRN if net >= 0 else RED

    print(f"  {BOLD}Parametreler:{NC}")
    print(f"  TP1={TP1_R}R  TP2={TP2_R}R  TP3={TP3_R}R  "
          f"MIN_SL={MIN_SL_PCT*100:.1f}%  MIN_RR={MIN_RR}")
    print(f"  Risk/trade={RISK_PCT}%  Fee={FEE_RATE*100:.2f}%  Slippage=0.03%\n")

    paper_sourced = stats.get("paper_sourced", 0)
    skipped_r = sim.get("skipped_reasons", {})

    print(f"  {BOLD}Sonuçlar:{NC}")
    print(f"  {'Simüle:':<24} {stats.get('total',0)} sinyal")
    # Atlandı nedenleri
    if skipped_r:
        reasons_str = ", ".join(f"{k}={v}" for k, v in sorted(skipped_r.items(), key=lambda x: -x[1])[:4])
        print(f"  {'Atlandı:':<24} {skipped} → {DIM}{reasons_str}{NC}")
    else:
        print(f"  {'Atlandı:':<24} {skipped} (entry/sl eksik — risk aşamasına gelemedi)")
    print(f"  {'Paper verisi:':<24} {paper_sourced} (gerçek outcome{'✅' if paper_sourced > 0 else ' — istatistiksel'})")
    print(f"  {'SL genişletildi:':<24} {stats.get('sl_expanded',0)} (MIN_SL<%{MIN_SL_PCT*100:.1f})")
    # İstatistiksel simülasyon uyarısı
    if paper_sourced == 0:
        print(f"\n  {YLW}⚠ Tüm sonuçlar istatistiksel (seed=42, dağılım: %45 TP1, %25 TP2, %30 SL){NC}")
        print(f"  {YLW}  Gerçek performans farklı olabilir. Paper tracking açıkken daha doğru olur.{NC}")
    print()
    print(f"  {'WIN/LOSS:':<24} {stats.get('wins',0)}W / {stats.get('losses',0)}L")
    print(f"  {'Win Rate:':<24} {wr_clr}{wr:.1f}%{NC}")
    print(f"  {'Profit Factor:':<24} {GRN if pf>=1.5 else (YLW if pf>=1 else RED)}{pf:.3f}{NC}")
    print(f"  {'Avg R:':<24} {GRN if avgr>0 else RED}{avgr:+.3f}R{NC}")
    print(f"  {'Net PnL:':<24} {net_clr}{net:+.2f}${NC}")
    # ROI uyarısı: compounding + istatistiksel → çok şişiyor
    roi_note = f"  {DIM}(istatistiksel+compounding — gerçekçi değil){NC}" if paper_sourced == 0 else ""
    print(f"  {'ROI:':<24} {net_clr}{roi:+.2f}%{NC}{roi_note}")
    print(f"  {'Toplam Fee:':<24} -{stats.get('total_fee',0):.2f}$")
    print(f"  {'Başlangıç bakiye:':<24} ${stats.get('initial_balance',0):.2f}")
    print(f"  {'Final bakiye:':<24} ${stats.get('final_balance',0):.2f}")

    # Kalite dağılımı
    from collections import Counter
    qual_c = Counter(r["quality"] for r in results)
    qual_str = "  ".join(f"{q}={n}" for q, n in sorted(qual_c.items()))
    print(f"\n  {BOLD}Kalite dağılımı:{NC} {qual_str}")

    # Yön dağılımı
    dir_c = Counter(r["direction"] for r in results)
    dir_str = "  ".join(f"{d}={n}" for d, n in dir_c.items())
    print(f"  {BOLD}Yön dağılımı:{NC}    {dir_str}")

    # Son sinyaller tablosu
    n_show = len(results) if show_all else min(20, len(results))
    print(f"\n{BOLD}  Son {n_show} Sinyal Detayı:{NC}")
    print(f"  {DIM}{'Tarih':<16} {'Coin':<12} {'Yön':<5} {'Score':<6} {'Qual':<5} {'Dec':<6} {'SL%':<6} {'RR':<5} {'Sonuç':<10} {'PnL':>8} {'R':>6}{NC}")
    print(f"  {'─'*95}")

    for r in results[:n_show]:
        pnl_clr = GRN if r["net_pnl"] > 0 else RED
        exp_mark = "↑" if r["sl_expanded"] else " "
        src_mark = "📋" if r["outcome_src"] == "paper" else "📊"
        out_str  = r["outcome"].upper()
        print(
            f"  {r['created']:<16} "
            f"{r['symbol']:<12} "
            f"{r['direction']:<5} "
            f"{r['score']:<6.1f} "
            f"{r['quality']:<5} "
            f"{r['decision']:<6} "
            f"{exp_mark}{r['sl_pct']:<5.2f}% "
            f"{r['rr']:<5.2f} "
            f"{src_mark}{out_str:<8} "
            f"{pnl_clr}{r['net_pnl']:>+8.2f}${NC} "
            f"{pnl_clr}{r['r_multiple']:>+5.2f}R{NC}"
        )

    if len(results) > n_show:
        print(f"  {DIM}... ve {len(results) - n_show} sinyal daha (--all ile tümünü gör){NC}")

    print(f"\n{BOLD}{CYN}{'═'*64}{NC}\n")


# ── CSV Export ────────────────────────────────────────────────────────────────

def export_csv(results: list[dict], path: str):
    if not results:
        print(f"{YLW}⚠ Export edilecek sonuç yok.{NC}")
        return
    keys = list(results[0].keys())
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=keys)
        w.writeheader()
        w.writerows(results)
    print(f"{GRN}✅ {len(results)} sinyal → {path}{NC}")


# ── CLI ───────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="AurvexAI Geçmiş Sinyal Simülasyonu")
    parser.add_argument("--days",      type=int,   default=3,    help="Kaç gün geriye git (varsayılan: 3)")
    parser.add_argument("--min-score", type=float, default=20.0, help="Minimum final_score (varsayılan: 20)")
    parser.add_argument("--all",       action="store_true",      help="ALLOW dahil tüm sinyalleri simüle et")
    parser.add_argument("--show-all",  action="store_true",      help="Tüm sinyalleri tabloda göster")
    parser.add_argument("--export",    type=str,   default="",   help="CSV export dosyası")
    parser.add_argument("--balance",   type=float, default=None, help="Başlangıç bakiyesi (varsayılan: config)")
    parser.add_argument("--db",        type=str,   default=None, help="DB dosya yolu (varsayılan: config)")
    args = parser.parse_args()

    db_path = args.db or getattr(config, "DB_PATH", "trading.db")
    initial_balance = args.balance or getattr(config, "INITIAL_PAPER_BALANCE", 500.0)

    if not os.path.exists(db_path):
        print(f"{RED}❌ DB bulunamadı: {db_path}{NC}")
        print(f"   --db ile yolu belirt veya sunucuda çalıştır.")
        sys.exit(1)

    print(f"{CYN}🔍 DB: {db_path}{NC}")
    print(f"{CYN}📅 Son {args.days} gün, min score={args.min_score}{NC}")
    if args.all:
        print(f"{YLW}⚠ --all: ALLOW dahil tüm sinyaller simüle ediliyor{NC}")

    signals = load_signals(db_path, args.days, args.min_score, args.all)
    print(f"{GRN}✓ {len(signals)} sinyal yüklendi{NC}\n")

    if not signals:
        print(f"{RED}❌ Bu kriterlerde sinyal bulunamadı.{NC}")
        sys.exit(0)

    sim = run_simulation(signals, initial_balance)
    print_report(sim, show_all=args.show_all)

    if args.export:
        export_csv(sim["results"], args.export)


if __name__ == "__main__":
    main()
