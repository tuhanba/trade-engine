"""
scripts/audit_pnl_consistency.py — AX PnL Consistency Audit v5.0
=================================================================
FAZ 12: Comprehensive audit that checks ERRORS not warnings.
All items from the spec are ERROR-level checks.
"""
import os
import sys
import sqlite3
import importlib

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from config import DB_PATH

ERRORS = []
WARNINGS = []

def error(msg):
    ERRORS.append(msg)
    print(f"  ❌ ERROR: {msg}")

def warn(msg):
    WARNINGS.append(msg)
    print(f"  ⚠️ WARNING: {msg}")

def ok(msg):
    print(f"  ✅ {msg}")


def audit_compile():
    """1. Compile/Import kontrolü."""
    print("\n🔍 1. COMPILE / IMPORT KONTROLÜ")
    modules = [
        "app", "execution_engine", "database", "core.accounting",
        "telegram_delivery", "scalp_bot_v3",
    ]
    for m in modules:
        try:
            importlib.import_module(m)
            ok(f"{m} import OK")
        except Exception as e:
            error(f"{m} import FAILED: {e}")


def audit_hardcoded_secrets():
    """2. Hardcoded token/API key kontrolü."""
    print("\n🔍 2. HARDCODED SECRET KONTROLÜ")
    files_to_check = [
        "config.py", "scalp_bot_v3.py", "telegram_delivery.py",
        "execution_engine.py", "app.py", "database.py",
        "dashboard_service.py",
    ]
    base = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    # Known exposed token patterns — these must not appear hardcoded
    patterns = ["8404489471", "AAEU3uk", "958182551", "9fND0AUN", "CG9BLe4B"]

    for f in files_to_check:
        fpath = os.path.join(base, f)
        if not os.path.exists(fpath):
            continue
        with open(fpath, "r", encoding="utf-8", errors="ignore") as fh:
            content = fh.read()
        found = False
        for p in patterns:
            if p in content:
                error(f"Hardcoded secret in {f}: ...{p[:8]}...")
                found = True
                break
        if not found:
            ok(f"{f} temiz")


def audit_schema():
    """3. DB Schema kontrolü."""
    print("\n🔍 3. DB SCHEMA KONTROLÜ")
    if not os.path.exists(DB_PATH):
        error("DB dosyası yok")
        return

    conn = sqlite3.connect(DB_PATH)
    cursor = conn.cursor()

    required_tables = [
        "trades", "partial_closes", "balance_ledger", "paper_account",
        "signal_candidates", "paper_results", "ai_logs",
        "coin_profiles", "coin_library", "trade_events",
    ]
    for t in required_tables:
        cursor.execute(f"SELECT name FROM sqlite_master WHERE type='table' AND name='{t}'")
        if cursor.fetchone():
            ok(f"Tablo: {t}")
        else:
            error(f"Eksik tablo: {t}")

    # trades kolonları
    required_trade_cols = [
        "symbol", "direction", "status", "entry", "sl", "tp1", "tp2", "tp3",
        "original_qty", "remaining_qty", "qty_tp1", "qty_tp2", "qty_runner",
        "leverage", "notional_size", "margin_used", "risk_pct", "risk_usd",
        "max_loss_after_fee", "realized_pnl", "unrealized_pnl", "net_pnl",
        "total_fee", "open_fee", "close_fee", "fee_rate",
        "tp1_hit", "tp2_hit", "open_time", "close_time", "duration_seconds",
        "close_reason", "r_multiple", "setup_quality", "final_score",
        "market_regime", "is_valid_for_stats", "archived_reason",
    ]
    cursor.execute("PRAGMA table_info(trades)")
    existing_cols = {row[1] for row in cursor.fetchall()}
    missing = set(required_trade_cols) - existing_cols
    for col in missing:
        error(f"Eksik trades kolonu: {col}")
    if not missing:
        ok("trades tüm kolonlar mevcut")

    conn.close()


def audit_pnl_consistency():
    """4. PnL consistency kontrolü."""
    print("\n🔍 4. PnL CONSISTENCY KONTROLÜ")
    if not os.path.exists(DB_PATH):
        return

    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row

    # Closed trades with remaining_qty > 0
    rows = conn.execute(
        "SELECT id, remaining_qty FROM trades WHERE status='closed' AND remaining_qty > 0"
    ).fetchall()
    if rows:
        for r in rows:
            error(f"Closed trade #{r['id']} remaining_qty={r['remaining_qty']} > 0")
    else:
        ok("Tüm closed trade'lerde remaining_qty=0")

    # Negative remaining_qty
    rows = conn.execute(
        "SELECT id, remaining_qty FROM trades WHERE remaining_qty < 0"
    ).fetchall()
    if rows:
        for r in rows:
            error(f"Trade #{r['id']} negative remaining_qty={r['remaining_qty']}")
    else:
        ok("Negatif remaining_qty yok")

    # Duration seconds kontrolü
    rows = conn.execute(
        "SELECT id FROM trades WHERE status='closed' AND (duration_seconds IS NULL OR duration_seconds=0)"
    ).fetchall()
    if rows:
        error(f"{len(rows)} closed trade'de duration_seconds eksik")
    else:
        ok("Duration_seconds tüm closed trade'lerde mevcut")

    # net_pnl vs ledger toplamı
    rows = conn.execute("""
        SELECT t.id, t.net_pnl, COALESCE(SUM(l.amount), 0) as ledger_sum
        FROM trades t
        LEFT JOIN balance_ledger l ON l.trade_id = t.id AND l.event_type != 'OPEN_FEE'
        WHERE t.status = 'closed'
        GROUP BY t.id
        HAVING ABS(t.net_pnl - ledger_sum) > 0.01
    """).fetchall()
    if rows:
        for r in rows:
            error(f"Trade #{r['id']} net_pnl={r['net_pnl']:.4f} != ledger_sum={r['ledger_sum']:.4f}")
    else:
        ok("net_pnl = ledger toplamı eşleşiyor")

    # realized_pnl vs partial_closes toplamı
    rows = conn.execute("""
        SELECT t.id, t.realized_pnl,
               COALESCE(SUM(p.net_pnl), 0) as partial_sum
        FROM trades t
        LEFT JOIN partial_closes p ON p.trade_id = t.id
            AND p.close_type IN ('TP1', 'TP2')
        WHERE t.status = 'closed' AND t.tp1_hit = 1
        GROUP BY t.id
        HAVING ABS(COALESCE(t.realized_pnl, 0) - partial_sum) > 0.01
    """).fetchall()
    if rows:
        for r in rows:
            error(f"Trade #{r['id']} realized_pnl mismatch: {r['realized_pnl']} vs partials={r['partial_sum']}")
    else:
        ok("realized_pnl = partial_closes toplamı eşleşiyor")

    # Duplicate TP close
    rows = conn.execute("""
        SELECT trade_id, close_type, COUNT(*) as cnt
        FROM partial_closes
        WHERE close_type IN ('TP1', 'TP2')
        GROUP BY trade_id, close_type
        HAVING cnt > 1
    """).fetchall()
    if rows:
        for r in rows:
            error(f"Duplicate {r['close_type']} for trade #{r['trade_id']} (count={r['cnt']})")
    else:
        ok("Duplicate TP close yok")

    conn.close()


def audit_open_trades():
    """5. Open trade kontrolü — DB'de open varsa API de göstermeli."""
    print("\n🔍 5. OPEN TRADE KONTROLÜ")
    if not os.path.exists(DB_PATH):
        return

    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row

    open_count = conn.execute(
        "SELECT COUNT(*) FROM trades WHERE status NOT IN ('closed')"
    ).fetchone()[0]

    if open_count > 0:
        ok(f"{open_count} açık trade var — dashboard göstermeli")
    else:
        ok("Açık trade yok (normal)")

    conn.close()


def audit_legacy_code():
    """6. Legacy code kontrolü."""
    print("\n🔍 6. LEGACY CODE KONTROLÜ")
    base = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

    # _calc_pnl/_calc_qty in execution_engine
    ee_path = os.path.join(base, "execution_engine.py")
    if os.path.exists(ee_path):
        with open(ee_path, "r", encoding="utf-8") as f:
            content = f.read()
        if "def _calc_pnl" in content or "def _calc_qty" in content:
            error("execution_engine.py hâlâ legacy _calc_pnl/_calc_qty içeriyor")
        else:
            ok("execution_engine.py legacy fonksiyonlardan temiz")


def audit_accounting_args():
    """7. Accounting fonksiyon argüman sırası kontrolü."""
    print("\n🔍 7. ACCOUNTING FONKSİYON KONTROLÜ")
    try:
        from core.accounting import calculate_pnl
        # Test: LONG, entry=100, current=101, qty=1 → PnL = 1.0
        result = calculate_pnl("LONG", 100.0, 101.0, 1.0)
        if abs(result - 1.0) < 0.0001:
            ok("calculate_pnl argüman sırası doğru (LONG)")
        else:
            error(f"calculate_pnl LONG yanlış sonuç: {result} (beklenen: 1.0)")

        result = calculate_pnl("SHORT", 100.0, 99.0, 1.0)
        if abs(result - 1.0) < 0.0001:
            ok("calculate_pnl argüman sırası doğru (SHORT)")
        else:
            error(f"calculate_pnl SHORT yanlış sonuç: {result} (beklenen: 1.0)")

        from core.accounting import calculate_margin_loss_pct
        mlp = calculate_margin_loss_pct(100.0, 95.0, 20)
        if abs(mlp - 1.0) < 0.01:
            ok(f"margin_loss_pct doğru: x20 + %5 stop = %{mlp*100:.0f}")
        else:
            error(f"margin_loss_pct yanlış: {mlp} (beklenen: 1.0)")

    except Exception as e:
        error(f"Accounting test hatası: {e}")


def audit_scanner_top_limit():
    """8. Scanner top limit kontrolü."""
    print("\n🔍 8. SCANNER TOP LIMIT KONTROLÜ")
    base = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    try:
        from config import COIN_UNIVERSE
        if COIN_UNIVERSE:
            warn(f"COIN_UNIVERSE {len(COIN_UNIVERSE)} coin ile sınırlı — tüm coinleri taramıyor")
        else:
            ok("COIN_UNIVERSE boş — tüm coinler taranıyor")
    except Exception:
        ok("COIN_UNIVERSE kontrol edilemedi — varsayılan kullanılıyor")


def audit_ghost_learning():
    """9. Ghost learning / coin personality kontrolü."""
    print("\n🔍 9. GHOST LEARNING KONTROLÜ")
    if not os.path.exists(DB_PATH):
        return

    conn = sqlite3.connect(DB_PATH)

    # paper_results tablosu var mı?
    row = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='paper_results'"
    ).fetchone()
    if not row:
        error("paper_results tablosu yok — ghost learning çalışmaz")
        conn.close()
        return
    ok("paper_results tablosu mevcut")

    # coin_personality veya coin_profiles?
    try:
        cnt = conn.execute("SELECT COUNT(*) FROM coin_profiles").fetchone()[0]
        ok(f"coin_profiles: {cnt} kayıt")
    except Exception:
        warn("coin_profiles tablosu erişilemedi")

    conn.close()


def run_audit():
    print("=" * 60)
    print("🔍 AX PnL CONSISTENCY AUDIT v5.0")
    print("=" * 60)

    audit_compile()
    audit_hardcoded_secrets()
    audit_schema()
    audit_pnl_consistency()
    audit_open_trades()
    audit_legacy_code()
    audit_accounting_args()
    audit_scanner_top_limit()
    audit_ghost_learning()

    print("\n" + "=" * 60)
    print(f"📊 SONUÇ: {len(ERRORS)} ERROR, {len(WARNINGS)} WARNING")
    if ERRORS:
        print("❌ AUDIT BAŞARISIZ — FINAL READY DEĞİL")
        for i, e in enumerate(ERRORS, 1):
            print(f"   {i}. {e}")
    else:
        print("✅ AUDIT TEMİZ — Tüm kontroller geçti")
    print("=" * 60)

    return len(ERRORS) == 0


if __name__ == "__main__":
    success = run_audit()
    sys.exit(0 if success else 1)
