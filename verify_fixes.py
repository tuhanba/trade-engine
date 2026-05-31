#!/usr/bin/env python3
"""
verify_fixes.py — AurvexAI Düzeltme Doğrulama Scripti

Her düzeltmeyi tek tek test eder.
Geçen: ✅   Geçemeyen: ❌   Uyarı: ⚠️

Kullanım:
    python3 verify_fixes.py

Tümü geçmeli. Geçemeyen varsa o fix'e geri dön.
"""

import sys
import os
import importlib
import traceback

# ── Renk ──────────────────────────────────────────────────────────────────────
GREEN  = "\033[92m"
RED    = "\033[91m"
YELLOW = "\033[93m"
RESET  = "\033[0m"
BOLD   = "\033[1m"
DIM    = "\033[2m"

passed = []
failed = []
warned = []


def check(name: str, fn):
    try:
        result = fn()
        if result is True:
            print(f"  {GREEN}✅{RESET} {name}")
            passed.append(name)
        elif result is False:
            print(f"  {RED}❌{RESET} {name}")
            failed.append(name)
        else:
            # String → warning
            print(f"  {YELLOW}⚠️ {RESET} {name} — {result}")
            warned.append(name)
    except Exception as e:
        print(f"  {RED}❌{RESET} {name}")
        print(f"     {DIM}{traceback.format_exc().strip()}{RESET}")
        failed.append(name)


# ── Dizin Kontrolü ────────────────────────────────────────────────────────────
if not os.path.exists("database.py"):
    print(f"{RED}Bu script trade-engine/ kök dizininden çalıştırılmalı.{RESET}")
    sys.exit(1)

sys.path.insert(0, os.getcwd())


# ── TEST BLOKLARI ─────────────────────────────────────────────────────────────

def test_db_tables():
    """Kritik tablolar var mı?"""
    import sqlite3
    import config
    conn = sqlite3.connect(config.DB_PATH)
    required = [
        "trades", "signal_candidates", "signal_events", "telegram_messages",
        "ghost_signals", "ghost_results", "paper_results", "paper_account",
        "daily_summary",  # yeni eklenen
    ]
    missing = []
    tables = {r[0] for r in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table'"
    ).fetchall()}
    for t in required:
        if t not in tables:
            missing.append(t)
    conn.close()
    if missing:
        raise AssertionError(f"Eksik tablolar: {missing}")
    return True


def test_daily_summary_ddl():
    """daily_summary init_db()'den sonra mevcut."""
    import database
    database.init_db()
    import sqlite3, config
    conn = sqlite3.connect(config.DB_PATH)
    r = conn.execute(
        "SELECT name FROM sqlite_master WHERE name='daily_summary'"
    ).fetchone()
    conn.close()
    if not r:
        raise AssertionError("daily_summary tablosu yok — init_db() DDL'i eksik")
    return True


def test_ghost_sql_no_evaluated_at():
    """Ghost stats sorgusu 'evaluated_at' içermemeli."""
    with open("core/ai_decision_engine.py", "r") as f:
        content = f.read()
    if "evaluated_at" in content:
        raise AssertionError(
            "ai_decision_engine.py hala 'evaluated_at' içeriyor "
            "(ghost_results'ta bu kolon yok)"
        )
    return True


def test_ghost_sql_uses_join():
    """Ghost stats sorgusu JOIN ghost_signals kullanmalı."""
    with open("core/ai_decision_engine.py", "r") as f:
        content = f.read()
    if "JOIN ghost_signals" not in content:
        raise AssertionError(
            "Ghost stats sorgusu JOIN ghost_signals kullanmıyor — "
            "ghost WR her zaman 0 döner"
        )
    return True


def test_ghost_sql_simulated_at():
    """Ghost stats sorgusu simulated_at kullanmalı."""
    with open("core/ai_decision_engine.py", "r") as f:
        content = f.read()
    if "r.simulated_at" not in content and "simulated_at" not in content:
        raise AssertionError("Ghost sorgusu simulated_at kullanmıyor")
    return True


def test_ghost_manager_runs():
    """GhostMemoryManager.get_symbol_ghost_stats() hata vermeden çalışmalı."""
    from core.ai_decision_engine import GhostMemoryManager
    g = GhostMemoryManager()
    result = g.get_symbol_ghost_stats("BTCUSDT")
    assert isinstance(result, dict), "dict dönmeli"
    assert "total" in result
    assert "ghost_winrate" in result
    return True


def test_no_duplicate_telegram_in_execution_engine():
    """execution_engine.py'de send_trade_open çağrısı olmamalı."""
    with open("execution_engine.py", "r") as f:
        content = f.read()
    if "send_trade_open as _tg_open" in content:
        raise AssertionError(
            "execution_engine.py hala duplicate Telegram çağrısı içeriyor — "
            "Patch 3 uygulanmadı"
        )
    return True


def test_signal_events_write():
    """save_signal_event() çalışıyor ve geri okunabiliyor."""
    import database
    database.init_db()

    test_id = "verify_test_001"
    database.save_signal_event(test_id, "RISK_REJECTED",
                               symbol="TESTUSDT", reject_reason="test_patch")
    database.save_signal_event(test_id, "EXECUTED",
                               symbol="TESTUSDT", reject_reason="trade_id=9999")

    import sqlite3, config
    conn = sqlite3.connect(config.DB_PATH)
    rows = conn.execute(
        "SELECT stage FROM signal_events WHERE signal_id=?", (test_id,)
    ).fetchall()
    stages = [r[0] for r in rows]

    # Temizle
    conn.execute("DELETE FROM signal_events WHERE signal_id=?", (test_id,))
    conn.commit()
    conn.close()

    assert "RISK_REJECTED" in stages, f"RISK_REJECTED yazılmadı, yazılanlar: {stages}"
    assert "EXECUTED" in stages, f"EXECUTED yazılmadı, yazılanlar: {stages}"
    return True


def test_risk_service_imports():
    """risk_service.py import ediliyor ve RiskService sınıfı var."""
    import importlib.util
    spec = importlib.util.spec_from_file_location(
        "risk_service", "core/services/risk_service.py"
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    assert hasattr(mod, "RiskService"), "RiskService sınıfı yok"
    return True


def test_scanner_service_imports():
    """scanner_service.py import ediliyor ve ScannerService sınıfı var."""
    import importlib.util
    spec = importlib.util.spec_from_file_location(
        "scanner_service", "core/services/scanner_service.py"
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    assert hasattr(mod, "ScannerService"), "ScannerService sınıfı yok"
    return True


def test_execution_service_imports():
    """execution_service.py import ediliyor."""
    import importlib.util
    spec = importlib.util.spec_from_file_location(
        "execution_service", "core/services/execution_service.py"
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    assert hasattr(mod, "ExecutionService"), "ExecutionService sınıfı yok"
    return True


def test_telegram_delivery_imports():
    """telegram_delivery.py import ediliyor ve tüm fonksiyonlar mevcut."""
    import telegram_delivery as td
    required_fns = [
        "send_trade_open", "send_tp_hit", "send_trade_close",
        "deliver_signal", "format_signal", "send_message",
        "recover_queued_messages", "TelegramDelivery",
    ]
    missing = [fn for fn in required_fns if not hasattr(td, fn)]
    if missing:
        raise AssertionError(f"telegram_delivery'de eksik: {missing}")
    return True


def test_telegram_no_duplicate_path():
    """telegram_delivery.py'de execution_engine'e özgü Telegram çağrısı yok."""
    with open("telegram_delivery.py", "r") as f:
        content = f.read()
    # Kontrol: execution_engine'den duplicate çağrı gelmemeli
    # Bu test execution_engine'i kontrol eder
    with open("execution_engine.py", "r") as f:
        eng_content = f.read()
    if "send_trade_open as _tg_open" in eng_content:
        raise AssertionError("execution_engine.py'de duplicate Telegram çağrısı var")
    return True


def test_app_diagnostics_endpoint():
    """app.py'de /api/diagnostics endpoint'i tanımlı."""
    with open("app.py", "r") as f:
        content = f.read()
    if "/api/diagnostics" not in content:
        raise AssertionError("/api/diagnostics endpoint'i app.py'de yok")
    return True


def test_app_funnel_uses_signal_events():
    """/api/stats funnel metrikleri signal_events'i okumalı."""
    with open("app.py", "r") as f:
        content = f.read()
    checks = ["risk_reject", "ai_veto", "executed", "trend_ok"]
    missing = [c for c in checks if c not in content]
    if missing:
        raise AssertionError(
            f"app.py funnel'da eksik metrikler: {missing} — "
            "hala pipeline_scanned kullanıyor olabilir"
        )
    return True


def test_syntax_all_files():
    """Değiştirilen tüm dosyalar Python olarak parse ediliyor."""
    import py_compile
    files = [
        "database.py",
        "telegram_delivery.py",
        "execution_engine.py",
        "app.py",
        "core/ai_decision_engine.py",
        "core/services/risk_service.py",
        "core/services/scanner_service.py",
        "core/services/execution_service.py",
    ]
    errors = []
    for f in files:
        try:
            py_compile.compile(f, doraise=True)
        except py_compile.PyCompileError as e:
            errors.append(f"{f}: {e}")
    if errors:
        raise AssertionError("Syntax hataları:\n" + "\n".join(errors))
    return True


def test_bot_heartbeat():
    """Bot çalışıyor mu? Heartbeat 120 saniyeden yeni olmalı."""
    import sqlite3, config
    from datetime import datetime, timezone
    conn = sqlite3.connect(config.DB_PATH)
    row = conn.execute(
        "SELECT value FROM bot_status WHERE key='heartbeat'"
    ).fetchone()
    conn.close()
    if not row:
        return "Heartbeat kaydı yok — bot çalışıyor mu?"
    hb = row[0]
    try:
        dt = datetime.fromisoformat(hb.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        elapsed = (datetime.now(timezone.utc) - dt).total_seconds()
        if elapsed > 120:
            return f"Heartbeat {elapsed:.0f} saniye eski — bot çalışmıyor olabilir"
        return True
    except Exception as e:
        return f"Heartbeat parse hatası: {e}"


def test_telegram_configured():
    """Telegram bot token ve chat_id tanımlı."""
    import config
    if not config.TELEGRAM_BOT_TOKEN:
        return "TELEGRAM_BOT_TOKEN boş — .env kontrol et"
    if not config.TELEGRAM_CHAT_ID:
        return "TELEGRAM_CHAT_ID boş — .env kontrol et"
    return True


# ── Çalıştır ──────────────────────────────────────────────────────────────────

print(f"\n{BOLD}{'='*55}{RESET}")
print(f"{BOLD}  AurvexAI — Fix Doğrulama  v6.0{RESET}")
print(f"{BOLD}{'='*55}{RESET}\n")

print(f"{BOLD}── Veritabanı{RESET}")
check("Kritik tablolar mevcut",         test_db_tables)
check("daily_summary tablosu mevcut",   test_daily_summary_ddl)
check("signal_events yazma/okuma",      test_signal_events_write)

print(f"\n{BOLD}── Ghost Learning{RESET}")
check("evaluated_at kolonu kullanılmıyor",  test_ghost_sql_no_evaluated_at)
check("JOIN ghost_signals kullanılıyor",    test_ghost_sql_uses_join)
check("simulated_at kullanılıyor",         test_ghost_sql_simulated_at)
check("GhostMemoryManager çalışıyor",      test_ghost_manager_runs)

print(f"\n{BOLD}── Telegram{RESET}")
check("telegram_delivery.py import OK",    test_telegram_delivery_imports)
check("Duplicate Telegram yok",            test_no_duplicate_telegram_in_execution_engine)
check("execution_engine duplicate yok",    test_telegram_no_duplicate_path)

print(f"\n{BOLD}── Servisler{RESET}")
check("risk_service.py import OK",         test_risk_service_imports)
check("scanner_service.py import OK",      test_scanner_service_imports)
check("execution_service.py import OK",    test_execution_service_imports)

print(f"\n{BOLD}── Dashboard / API{RESET}")
check("/api/diagnostics endpoint mevcut",  test_app_diagnostics_endpoint)
check("Funnel signal_events kullanıyor",   test_app_funnel_uses_signal_events)

print(f"\n{BOLD}── Syntax & Genel{RESET}")
check("Tüm Python dosyaları parse OK",     test_syntax_all_files)
check("Telegram yapılandırılmış",          test_telegram_configured)
check("Bot heartbeat",                     test_bot_heartbeat)

# ── Özet ──────────────────────────────────────────────────────────────────────
print(f"\n{BOLD}{'='*55}{RESET}")
total = len(passed) + len(failed) + len(warned)
print(
    f"  {GREEN}✅ Geçti: {len(passed)}{RESET}  "
    f"{RED}❌ Geçemedi: {len(failed)}{RESET}  "
    f"{YELLOW}⚠️  Uyarı: {len(warned)}{RESET}  "
    f"  (Toplam: {total})"
)

if failed:
    print(f"\n{RED}{BOLD}Geçemeyen testler:{RESET}")
    for f in failed:
        print(f"  {RED}•{RESET} {f}")
    print(f"\n{YELLOW}apply_patches.py çalıştır veya manuel düzelt.{RESET}")
    sys.exit(1)
elif warned:
    print(f"\n{YELLOW}Uyarılar var ama kritik değil. Sistemi başlat.{RESET}")
    sys.exit(0)
else:
    print(f"\n{GREEN}{BOLD}Tüm testler geçti! Sistem hazır.{RESET}")
    print(f"{DIM}systemctl restart aurvex-bot aurvex-dashboard{RESET}")
    sys.exit(0)
