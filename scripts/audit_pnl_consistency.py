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


# ── Extended 12-point audit functions (v5.1) ──────────────────────────────────

import sqlite3 as _sqlite3

from config import DB_PATH as _DB_PATH

_TOLERANCE = 0.01
_errors = 0
_warnings = 0


def _err(msg):
    global _errors
    _errors += 1
    print(f"  [ERROR] {msg}")


def _warn(msg):
    global _warnings
    _warnings += 1
    print(f"  [WARN]  {msg}")


def _ok(msg):
    print(f"  [OK]    {msg}")


def audit_schema(conn):
    print("\n── 1. Schema Kontrolü ──────────────────────────────────────")
    required_tables = [
        "trades", "partial_closes", "balance_ledger", "paper_account",
        "signal_candidates", "paper_results", "ai_logs",
        "coin_profiles", "coin_library", "trade_events",
    ]
    cursor = conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
    existing = {row[0] for row in cursor.fetchall()}
    for tbl in required_tables:
        if tbl not in existing:
            _err(f"Tablo eksik: {tbl}")
        else:
            _ok(f"Tablo mevcut: {tbl}")

    required_cols = {
        "symbol", "direction", "status", "entry", "sl", "tp1", "tp2", "tp3",
        "original_qty", "remaining_qty", "qty_tp1", "qty_tp2", "qty_runner",
        "leverage", "notional_size", "margin_used", "risk_pct", "risk_usd",
        "max_loss_after_fee", "realized_pnl", "unrealized_pnl", "net_pnl",
        "total_fee", "open_fee", "close_fee", "fee_rate",
        "tp1_hit", "tp2_hit", "open_time", "close_time", "close_price",
        "duration_seconds", "close_reason", "r_multiple",
        "setup_quality", "final_score", "market_regime",
        "is_valid_for_stats", "archived_reason",
    }
    cursor = conn.execute("PRAGMA table_info(trades)")
    existing_cols = {row[1] for row in cursor.fetchall()}
    missing = required_cols - existing_cols
    if missing:
        _err(f"trades tablosunda eksik kolonlar: {missing}")
    else:
        _ok("trades tablosu tüm zorunlu kolonlara sahip")


def audit_ledger(conn):
    print("\n── 2. Ledger vs trades.net_pnl ─────────────────────────────")
    trades = conn.execute(
        "SELECT id, symbol, net_pnl, open_fee FROM trades WHERE status='closed'"
    ).fetchall()
    if not trades:
        _warn("Kapalı trade yok — ledger denetimi atlandı.")
        return
    mismatches = 0
    for t in trades:
        tid = t["id"]
        net_pnl = float(t["net_pnl"] or 0)
        ledger_rows = conn.execute(
            "SELECT COALESCE(SUM(amount), 0) FROM balance_ledger WHERE trade_id=?",
            (tid,)
        ).fetchone()
        ledger_total = float(ledger_rows[0])
        if abs(ledger_total - net_pnl) > _TOLERANCE:
            _err(
                f"Trade #{tid} ({t['symbol']}): "
                f"ledger_total={ledger_total:.4f} ≠ net_pnl={net_pnl:.4f}"
            )
            mismatches += 1
    if mismatches == 0:
        _ok(f"Ledger kontrolü geçti ({len(trades)} trade)")


def audit_partial_closes(conn):
    print("\n── 3. partial_closes vs realized_pnl ───────────────────────")
    trades = conn.execute(
        "SELECT id, symbol, realized_pnl FROM trades WHERE status='closed'"
    ).fetchall()
    mismatches = 0
    for t in trades:
        tid = t["id"]
        realized = float(t["realized_pnl"] or 0)
        pc = conn.execute(
            "SELECT COALESCE(SUM(net_pnl), 0) FROM partial_closes WHERE trade_id=? AND close_type IN ('TP1','TP2')",
            (tid,)
        ).fetchone()
        pc_sum = float(pc[0])
        if abs(pc_sum - realized) > _TOLERANCE:
            _err(
                f"Trade #{tid} ({t['symbol']}): "
                f"partial_closes={pc_sum:.4f} ≠ realized_pnl={realized:.4f}"
            )
            mismatches += 1
    if mismatches == 0:
        _ok(f"Partial close kontrolü geçti ({len(trades)} trade)")


def audit_duplicate_tp(conn):
    print("\n── 4. Duplicate TP Kapanışı ─────────────────────────────────")
    for tp_type in ("TP1", "TP2"):
        rows = conn.execute(
            "SELECT trade_id, COUNT(*) as cnt FROM partial_closes WHERE close_type=? GROUP BY trade_id HAVING cnt > 1",
            (tp_type,)
        ).fetchall()
        if rows:
            for r in rows:
                _err(f"Trade #{r['trade_id']}: {tp_type} {r['cnt']} kez kapanmış")
        else:
            _ok(f"Duplicate {tp_type} yok")


def audit_remaining_qty(conn):
    print("\n── 5. Remaining Qty Kontrolü ────────────────────────────────")
    neg = conn.execute(
        "SELECT id, symbol, remaining_qty FROM trades WHERE remaining_qty < 0"
    ).fetchall()
    if neg:
        for r in neg:
            _err(f"Trade #{r['id']} ({r['symbol']}): negatif remaining_qty={r['remaining_qty']}")
    else:
        _ok("Negatif remaining_qty yok")

    closed_rem = conn.execute(
        "SELECT id, symbol, remaining_qty FROM trades WHERE status='closed' AND remaining_qty > 0.0001"
    ).fetchall()
    if closed_rem:
        for r in closed_rem:
            _err(f"Trade #{r['id']} ({r['symbol']}): closed ama remaining_qty={r['remaining_qty']}")
    else:
        _ok("Kapalı trade remaining_qty=0 tutarlı")


def audit_duration(conn):
    print("\n── 6. Duration Kontrolü ─────────────────────────────────────")
    rows = conn.execute(
        "SELECT id, symbol, duration_seconds, open_time, close_time FROM trades WHERE status='closed'"
    ).fetchall()
    bad = 0
    for r in rows:
        if (r["duration_seconds"] is None or r["duration_seconds"] <= 0):
            if r["open_time"] and r["close_time"]:
                _err(f"Trade #{r['id']} ({r['symbol']}): duration_seconds=0 ama open/close_time var")
                bad += 1
    if bad == 0:
        _ok(f"Duration kontrolü geçti ({len(rows)} kapalı trade)")


def audit_live_api():
    print("\n── 7. /api/live open_count vs DB ────────────────────────────")
    try:
        import requests
        resp = requests.get("http://127.0.0.1:5000/api/live", timeout=5)
        data = resp.json()
        api_count = data.get("data", {}).get("open_count", -1)
        c2 = _sqlite3.connect(_DB_PATH)
        db_count = c2.execute(
            "SELECT COUNT(*) FROM trades WHERE status NOT IN ('closed') AND status IS NOT NULL"
        ).fetchone()[0]
        c2.close()
        if api_count != db_count:
            _err(f"/api/live open_count={api_count} ≠ DB açık trade={db_count}")
        else:
            _ok(f"/api/live open_count={api_count} = DB açık trade={db_count}")
    except Exception as e:
        _warn(f"/api/live testi atlandı (servis çalışmıyor olabilir): {e}")


_SECRET_PATTERNS = ["BOT_TOKEN", "CHAT_ID", "API_KEY", "API_SECRET"]
_SECRET_SKIP_DIRS = {".git", "__pycache__", "archive", "deprecated",
                     "venv", ".venv", ".backup_pre_restore", "scripts"}


def audit_hardcoded_secrets():
    print("\n── 8. Hardcoded Secret Tarama ───────────────────────────────")
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    found = 0
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in _SECRET_SKIP_DIRS]
        for fname in filenames:
            if not fname.endswith(".py"):
                continue
            fpath = os.path.join(dirpath, fname)
            rel = os.path.relpath(fpath, root)
            try:
                with open(fpath, "r", encoding="utf-8", errors="ignore") as f:
                    for lineno, line in enumerate(f, 1):
                        stripped = line.strip()
                        if stripped.startswith("#"):
                            continue
                        if "os.getenv" in line or "os.environ" in line:
                            continue
                        for pat in _SECRET_PATTERNS:
                            if (pat in line and "=" in line
                                    and ('"' in line or "'" in line)
                                    and not any(kw in line for kw in ["json=", "data=", "params=", "f\"", "f'", ":", "patterns"])):
                                _err(f"Olası hardcoded secret: {rel}:{lineno} — {stripped[:80]}")
                                found += 1
            except Exception:
                pass
    if found == 0:
        _ok("Hardcoded secret bulunamadı")


def audit_fee_double_count(conn):
    print("\n── 9. Fee Double-Count Kontrolü ─────────────────────────────")
    trades = conn.execute(
        "SELECT id, symbol, original_qty, entry, fee_rate, total_fee FROM trades WHERE status='closed'"
    ).fetchall()
    suspect = 0
    for t in trades:
        qty = float(t["original_qty"] or 0)
        entry = float(t["entry"] or 0)
        fee_rate = float(t["fee_rate"] or 0.0004)
        total_fee = float(t["total_fee"] or 0)
        if qty <= 0 or entry <= 0:
            continue
        notional = qty * entry
        expected_max = notional * fee_rate * 2 * 1.15
        if total_fee > expected_max:
            _err(
                f"Trade #{t['id']} ({t['symbol']}): "
                f"total_fee={total_fee:.4f} > expected_max={expected_max:.4f}"
            )
            suspect += 1
    if suspect == 0:
        _ok(f"Fee double-count şüphesi yok ({len(trades)} trade)")


def audit_balance(conn):
    print("\n── 10. Paper Balance Tutarlılığı ────────────────────────────")
    row = conn.execute("SELECT balance, initial_balance FROM paper_account WHERE id=1").fetchone()
    if not row:
        _err("paper_account tablosunda kayıt yok")
        return
    current = float(row[0])
    initial = float(row[1])
    ledger = conn.execute("SELECT COALESCE(SUM(amount), 0) FROM balance_ledger").fetchone()[0]
    expected = initial + float(ledger)
    if abs(expected - current) > _TOLERANCE:
        _err(
            f"paper_account.balance={current:.2f} ≠ "
            f"initial({initial:.2f}) + ledger({float(ledger):.2f}) = {expected:.2f}"
        )
    else:
        _ok(f"Paper balance tutarlı: {current:.2f} USDT")


def audit_close_price(conn):
    print("\n── 11. Close Price Kontrolü ─────────────────────────────────")
    try:
        rows = conn.execute(
            "SELECT id, symbol FROM trades WHERE status='closed' AND (close_price IS NULL OR close_price = 0)"
        ).fetchall()
        if rows:
            _warn(f"{len(rows)} kapalı trade'de close_price=0/NULL (eski kayıtlar olabilir)")
        else:
            _ok("Tüm kapalı trade'lerde close_price mevcut")
    except Exception as e:
        _warn(f"close_price kolonu bulunamadı — migration gerekli: {e}")


def audit_signal_decisions(conn):
    print("\n── 12. Signal Candidate Decision Dağılımı ───────────────────")
    rows = conn.execute(
        "SELECT decision, COUNT(*) as cnt FROM signal_candidates GROUP BY decision ORDER BY cnt DESC"
    ).fetchall()
    if not rows:
        _warn("signal_candidates tablosu boş — ghost learning verisi yok")
        return
    all_allow = all(r["decision"] == "ALLOW" for r in rows)
    if all_allow and len(rows) > 0:
        _err("Tüm sinyaller decision='ALLOW' — ghost learning verisi bozuk olabilir")
    else:
        for r in rows:
            _ok(f"Decision '{r['decision']}': {r['cnt']} sinyal")


def main():
    global _errors, _warnings
    _errors = 0
    _warnings = 0
    print("=" * 60)
    print("AX PnL Tutarlılık Denetimi v5.1")
    print(f"DB: {_DB_PATH}")
    print("=" * 60)

    if not os.path.exists(_DB_PATH):
        print(f"[FATAL] DB bulunamadı: {_DB_PATH}")
        sys.exit(1)

    conn = _sqlite3.connect(_DB_PATH)
    conn.row_factory = _sqlite3.Row

    audit_schema(conn)
    audit_ledger(conn)
    audit_partial_closes(conn)
    audit_duplicate_tp(conn)
    audit_remaining_qty(conn)
    audit_duration(conn)
    audit_live_api()
    audit_hardcoded_secrets()
    audit_fee_double_count(conn)
    audit_balance(conn)
    audit_close_price(conn)
    audit_signal_decisions(conn)

    conn.close()

    print("\n" + "=" * 60)
    print(f"SONUÇ: {_errors} ERROR, {_warnings} WARNING")
    if _errors == 0 and _warnings == 0:
        print("TUM DENETIMLER GECTI")
    elif _errors == 0:
        print("ERROR yok ama WARNING var — gözden geçir")
    else:
        print("FINAL READY DEGIL — ERROR'lar giderilmeli")
    print("=" * 60)
    sys.exit(0 if _errors == 0 else 1)
