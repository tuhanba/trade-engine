#!/usr/bin/env python3
"""
scripts/audit_pnl_consistency.py
==================================
Kapalı trade'lerin PnL tutarlılığını denetler.
core/accounting.py formülleriyle DB'deki net_pnl değerlerini karşılaştırır.
Sapma varsa raporlar.

Kullanım:
    python3 scripts/audit_pnl_consistency.py
    python3 scripts/audit_pnl_consistency.py --db /path/to/trading.db --threshold 0.01
"""
import sys
import os
import sqlite3
import argparse

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

try:
    from config import DB_PATH
except ImportError:
    DB_PATH = "trading.db"

from core.accounting import calculate_close_pnl, DEFAULT_TAKER_FEE

def audit(db_path: str, threshold: float = 0.01):
    print(f"\n[Audit] DB: {db_path}")
    print(f"[Audit] Sapma eşiği: {threshold}$\n")

    if not os.path.exists(db_path):
        print(f"[HATA] DB bulunamadı: {db_path}")
        sys.exit(1)

    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row

    rows = conn.execute("""
        SELECT id, symbol, direction, entry, close_price, qty, leverage,
               realized_pnl, net_pnl, open_fee, fee_rate, result,
               qty_tp1, qty_tp2, qty_runner, close_reason
        FROM trades
        WHERE status IN ('closed','sl','trail','tp3','timeout')
          AND entry > 0 AND close_price > 0 AND qty > 0
        ORDER BY id DESC
        LIMIT 200
    """).fetchall()

    total = len(rows)
    ok_count = 0
    warn_count = 0
    errors = []

    for row in rows:
        t = dict(row)
        entry       = float(t.get("entry") or 0)
        close_price = float(t.get("close_price") or 0)
        qty         = float(t.get("qty") or 0)
        direction   = (t.get("direction") or "LONG").upper()
        realized    = float(t.get("realized_pnl") or 0)
        open_fee    = float(t.get("open_fee") or 0)
        fee_rate    = float(t.get("fee_rate") or DEFAULT_TAKER_FEE)
        db_net_pnl  = float(t.get("net_pnl") or 0)

        # Accounting modülüyle yeniden hesapla
        calc = calculate_close_pnl(
            entry, close_price, qty, direction,
            realized, open_fee, fee_rate
        )
        calc_net = calc["net_pnl"]
        diff = abs(db_net_pnl - calc_net)

        if diff <= threshold:
            ok_count += 1
        else:
            warn_count += 1
            errors.append({
                "id":         t["id"],
                "symbol":     t["symbol"],
                "direction":  direction,
                "db_net_pnl": db_net_pnl,
                "calc_net":   calc_net,
                "diff":       diff,
                "reason":     t.get("close_reason", "?"),
            })

    conn.close()

    print(f"Toplam denetlenen: {total}")
    print(f"Tutarlı (diff ≤ {threshold}$): {ok_count}")
    print(f"Tutarsız (diff > {threshold}$): {warn_count}")

    if errors:
        print(f"\n{'─'*70}")
        print(f"{'ID':>5}  {'Symbol':<12}  {'Dir':<6}  {'DB PnL':>9}  {'Calc PnL':>9}  {'Diff':>7}  Neden")
        print(f"{'─'*70}")
        for e in sorted(errors, key=lambda x: -x["diff"])[:50]:
            print(
                f"{e['id']:>5}  {e['symbol']:<12}  {e['direction']:<6}  "
                f"{e['db_net_pnl']:>9.4f}  {e['calc_net']:>9.4f}  "
                f"{e['diff']:>7.4f}  {e['reason']}"
            )
        print(f"\n[Audit] {warn_count} trade'de PnL tutarsızlığı tespit edildi.")
        print("[Audit] Bu trade'ler eski formülle hesaplanmış olabilir.")
        print("[Audit] Yeni trade'ler accounting modülüyle doğru hesaplanacak.")
    else:
        print("\n[Audit] Tüm trade'ler tutarlı. ✅")

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="PnL tutarlılık denetimi")
    parser.add_argument("--db", default=None)
    parser.add_argument("--threshold", type=float, default=0.01,
                        help="Kabul edilebilir sapma miktarı (varsayılan: 0.01$)")
    args = parser.parse_args()
    audit(args.db or DB_PATH, args.threshold)
