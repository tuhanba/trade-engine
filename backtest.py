"""
AX Backtest — Parametre Grid Search
=====================================
Mevcut trade verileri + outcome_labels (MFE/MAE) kullanarak
farklı TP/SL kombinasyonlarını retroaktif olarak simüle eder.

Mantık:
  - Her trade için ATR = sl_dist / sl_atr_mult_o (o anki params'tan)
  - Yeni TP/SL mesafesi = ATR × test_mult
  - Eğer MFE >= yeni_TP_pct → WIN (TP yakalandı)
  - Eğer MAE >= yeni_SL_pct → LOSS (SL tetiklendi)
  - İkisi de yok → HOLD (gerçek sonucu kullan)
  - İkisi de var → LOSS (daha kötü senaryo, ihtiyatlı)

Çıktı: tp_mult × sl_mult kombinasyonlarının expectancy sıralaması
"""

import os
import sqlite3

DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "trading.db")

TP_RANGE = [1.5, 1.8, 2.0, 2.2, 2.5, 2.8, 3.0, 3.2, 3.5, 4.0]
SL_RANGE = [0.8, 1.0, 1.2, 1.4, 1.6, 1.8]

MIN_LABELED = 15


def _load_data():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    rows = conn.execute("""
        SELECT
            t.id, t.entry, t.sl, t.tp, t.direction,
            t.params_version, t.status, t.net_pnl,
            o.mfe_pct, o.mae_pct, o.exit_eff,
            p.sl_atr_mult, p.tp_atr_mult
        FROM trades t
        JOIN outcome_labels o ON t.id = o.trade_id
        JOIN params p ON t.params_version = p.version
        WHERE t.status IN ('WIN','LOSS')
          AND o.mfe_pct IS NOT NULL
          AND o.mae_pct IS NOT NULL
          AND t.entry > 0 AND t.sl > 0
        ORDER BY t.id DESC
        LIMIT 200
    """).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def _simulate_trade(trade: dict, tp_test: float, sl_test: float) -> str:
    """
    Verilen TP/SL çarpanıyla bu trade'in sonucunu tahmin et.
    Dönüş: 'WIN', 'LOSS', veya 'HOLD' (ambiguous → gerçek sonuç kullanılır)
    """
    entry      = trade["entry"]
    sl_dist    = abs(entry - trade["sl"])
    sl_mult_o  = trade["sl_atr_mult"] or 1.2

    if sl_dist <= 0 or entry <= 0:
        return trade["status"]

    atr = sl_dist / sl_mult_o           # ATR in price units

    new_tp_pct = (atr * tp_test) / entry * 100   # TP mesafesi (% cinsinden)
    new_sl_pct = (atr * sl_test) / entry * 100   # SL mesafesi (% cinsinden)

    mfe = trade["mfe_pct"] or 0.0   # % (pozitif)
    mae = trade["mae_pct"] or 0.0   # % (pozitif = aleyhte)

    tp_hit = mfe >= new_tp_pct
    sl_hit = mae >= new_sl_pct

    if tp_hit and not sl_hit:
        return "WIN"
    elif sl_hit and not tp_hit:
        return "LOSS"
    elif sl_hit and tp_hit:
        return "LOSS"   # ihtiyatlı: ikisi de tetiklendiyse kötü senaryo
    else:
        return "HOLD"   # gerçek sonucu kullan


def _score_combo(trades: list, tp_test: float, sl_test: float) -> dict:
    wins = losses = holds = 0
    exp_r = 0.0

    for t in trades:
        outcome = _simulate_trade(t, tp_test, sl_test)
        if outcome == "HOLD":
            outcome = t["status"]
            holds += 1
        if outcome == "WIN":
            wins += 1
            exp_r += tp_test
        else:
            losses += 1
            exp_r -= sl_test

    total = wins + losses
    if total == 0:
        return None

    win_rate   = round(wins / total * 100, 1)
    expectancy = round(exp_r / total, 3)         # R cinsinden beklenen getiri/trade
    rr_ratio   = round(tp_test / sl_test, 2)

    return {
        "tp_mult":    tp_test,
        "sl_mult":    sl_test,
        "wins":       wins,
        "losses":     losses,
        "holds":      holds,
        "total":      total,
        "win_rate":   win_rate,
        "expectancy": expectancy,
        "rr_ratio":   rr_ratio,
    }


def run_backtest(tp_range=None, sl_range=None) -> dict:
    trades = _load_data()
    if len(trades) < MIN_LABELED:
        return {
            "ok": False,
            "error": f"Yetersiz etiketli trade: {len(trades)}/{MIN_LABELED}",
            "n": len(trades),
        }

    tp_range = tp_range or TP_RANGE
    sl_range = sl_range or SL_RANGE

    results = []
    for tp in tp_range:
        for sl in sl_range:
            combo = _score_combo(trades, tp, sl)
            if combo:
                results.append(combo)

    # Expectancy'ye göre sırala (en iyisi önce)
    results.sort(key=lambda x: x["expectancy"], reverse=True)

    # Mevcut params
    conn = sqlite3.connect(DB_PATH)
    p_row = conn.execute(
        "SELECT tp_atr_mult, sl_atr_mult FROM params ORDER BY version DESC LIMIT 1"
    ).fetchone()
    conn.close()
    current_tp = p_row[0] if p_row else 2.8
    current_sl = p_row[1] if p_row else 1.2

    # Mevcut params'ın simülasyon sonucu
    current = _score_combo(trades, current_tp, current_sl)

    return {
        "ok":         True,
        "n":          len(trades),
        "current_params": {
            "tp_mult": current_tp,
            "sl_mult": current_sl,
            **current,
        } if current else {},
        "top10":      results[:10],
        "best":       results[0] if results else {},
    }


def print_report(result: dict):
    if not result.get("ok"):
        print(f"[backtest] HATA: {result.get('error')}")
        return

    n = result["n"]
    cur = result.get("current_params", {})
    best = result.get("best", {})

    print(f"\n{'='*60}")
    print(f"  AX BACKTEST — {n} etiketli trade")
    print(f"{'='*60}")
    print(f"\n  Mevcut params: TP={cur.get('tp_mult')} SL={cur.get('sl_mult')}")
    print(f"  → WR={cur.get('win_rate')}% | Expectancy={cur.get('expectancy')}R | {cur.get('wins')}W/{cur.get('losses')}L")

    if best and best.get("expectancy", 0) > cur.get("expectancy", 0):
        print(f"\n  En iyi bulunan: TP={best['tp_mult']} SL={best['sl_mult']}")
        print(f"  → WR={best['win_rate']}% | Expectancy={best['expectancy']}R | {best['wins']}W/{best['losses']}L")
        diff = round(best["expectancy"] - cur.get("expectancy", 0), 3)
        print(f"  İyileşme: +{diff}R/trade")
    else:
        print(f"\n  Mevcut params zaten optimal görünüyor.")

    print(f"\n  Top 10:")
    print(f"  {'TP':>5} {'SL':>5} {'WR':>7} {'E(R)':>8} {'W/L':>10}")
    print(f"  {'-'*40}")
    for r in result.get("top10", []):
        marker = " ◄" if r["tp_mult"] == cur.get("tp_mult") and r["sl_mult"] == cur.get("sl_mult") else ""
        print(f"  {r['tp_mult']:>5} {r['sl_mult']:>5} "
              f"{r['win_rate']:>6}% {r['expectancy']:>8} "
              f"{r['wins']:>4}W/{r['losses']:<4}L{marker}")
    print(f"{'='*60}\n")


if __name__ == "__main__":
    result = run_backtest()
    print_report(result)
