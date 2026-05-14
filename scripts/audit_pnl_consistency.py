"""
scripts/audit_pnl_consistency.py – PnL/DB audit script.

Kontroller:
- DB erişilebilir mi?
- Tablolar var mı?
- Open trades tutarlı mı?
- Closed trades realized_pnl var mı?
- Kritik alanlar NULL/negatif mi?
- Balance ledger var mı?

Veri silmez. ERROR/WARNING/OK raporu verir.
"""

from __future__ import annotations

import sys
import os

# Proje kökünü path'e ekle
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import database
import config


def audit() -> dict:
    """Tam audit çalıştırır ve sonuçları döner."""
    errors = []
    warnings = []
    ok_items = []

    # 1. DB erişim
    try:
        conn = database.get_connection()
        conn.execute("SELECT 1")
        ok_items.append("DB erişimi OK")
    except Exception as exc:
        errors.append(f"DB erişim hatası: {exc}")
        print_report(errors, warnings, ok_items)
        return {"errors": errors, "warnings": warnings, "ok": ok_items}

    try:
        # 2. Tablo kontrolü
        tables = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()
        table_names = {r[0] for r in tables}

        for t in ("trades", "signal_candidates", "balance_ledger", "bot_status"):
            if t in table_names:
                ok_items.append(f"Tablo '{t}' mevcut")
            else:
                errors.append(f"Tablo '{t}' eksik!")

        if "trades" not in table_names:
            print_report(errors, warnings, ok_items)
            return {"errors": errors, "warnings": warnings, "ok": ok_items}

        # 3. Open trades kontrolü
        open_trades = conn.execute(
            "SELECT * FROM trades WHERE status='OPEN'"
        ).fetchall()
        for t in open_trades:
            tid = t["id"]
            # current_price kontrolü
            if t["current_price"] is None or t["current_price"] <= 0:
                warnings.append(
                    f"Trade #{tid}: current_price boş/sıfır"
                )
            # quantity kontrolü
            if t["quantity"] is None or t["quantity"] <= 0:
                warnings.append(f"Trade #{tid}: quantity boş/sıfır")
            # entry_price kontrolü
            if t["entry_price"] is None or t["entry_price"] <= 0:
                errors.append(f"Trade #{tid}: entry_price boş/sıfır!")

        ok_items.append(f"Open trades kontrol: {len(open_trades)} adet")

        # 4. Closed trades kontrolü
        closed_trades = conn.execute(
            "SELECT * FROM trades WHERE status='CLOSED'"
        ).fetchall()
        for t in closed_trades:
            tid = t["id"]
            if t["realized_pnl"] is None:
                warnings.append(f"Trade #{tid}: realized_pnl NULL")
            if t["exit_price"] is None or t["exit_price"] <= 0:
                warnings.append(f"Trade #{tid}: exit_price boş/sıfır")

        ok_items.append(f"Closed trades kontrol: {len(closed_trades)} adet")

        # 5. Kritik alan kontrolü (tüm trade'ler)
        all_trades = conn.execute("SELECT * FROM trades").fetchall()
        for t in all_trades:
            tid = t["id"]
            if t["stop_loss"] is None or t["stop_loss"] <= 0:
                warnings.append(f"Trade #{tid}: stop_loss boş/sıfır")

        # 6. Balance ledger kontrolü
        if "balance_ledger" in table_names:
            ledger_count = conn.execute(
                "SELECT COUNT(*) FROM balance_ledger"
            ).fetchone()[0]
            if ledger_count == 0:
                warnings.append("Balance ledger boş")
            else:
                ok_items.append(f"Balance ledger: {ledger_count} kayıt")

    except Exception as exc:
        errors.append(f"Audit hatası: {exc}")
    finally:
        conn.close()

    print_report(errors, warnings, ok_items)
    return {"errors": errors, "warnings": warnings, "ok": ok_items}


def print_report(errors: list, warnings: list, ok_items: list) -> None:
    """Audit raporunu yazdırır."""
    print("\n" + "=" * 50)
    print("  AX Trade Engine – PnL Audit Raporu")
    print("=" * 50)

    for item in ok_items:
        print(f"  ✓ OK      : {item}")
    for item in warnings:
        print(f"  ⚠ WARNING : {item}")
    for item in errors:
        print(f"  ✗ ERROR   : {item}")

    print("-" * 50)
    print(f"  OK: {len(ok_items)}  |  WARNING: {len(warnings)}  |  ERROR: {len(errors)}")
    print("=" * 50 + "\n")


if __name__ == "__main__":
    database.init_db()
    database.migrate_db()
    result = audit()
    sys.exit(1 if result["errors"] else 0)
