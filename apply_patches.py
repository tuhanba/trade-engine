#!/usr/bin/env python3
"""
apply_patches.py — Büyük dosyalara cerrahi düzeltmeler uygular.

Her patch bağımsız. Biri başarısız olsa bile diğerleri çalışır.
Sonunda verify_fixes.py çalıştır.

Kullanım:
    python3 apply_patches.py
"""

import sys
import re
import os

# ── Renk çıktısı ──────────────────────────────────────────────────────────────
GREEN  = "\033[92m"
RED    = "\033[91m"
YELLOW = "\033[93m"
RESET  = "\033[0m"
BOLD   = "\033[1m"

def ok(msg):  print(f"{GREEN}✅ {msg}{RESET}")
def err(msg): print(f"{RED}❌ {msg}{RESET}")
def warn(msg):print(f"{YELLOW}⚠️  {msg}{RESET}")
def info(msg):print(f"{BOLD}── {msg}{RESET}")


# ── Patch 1: database.py — daily_summary DDL ──────────────────────────────────

def patch_database_daily_summary():
    info("PATCH 1: database.py — daily_summary DDL")
    path = "database.py"

    try:
        with open(path, "r", encoding="utf-8") as f:
            content = f.read()

        # Zaten var mı?
        if "CREATE TABLE IF NOT EXISTS daily_summary" in content:
            ok("daily_summary DDL zaten mevcut — atlanıyor.")
            return True

        # init_db() içinde iyi bir ekleme noktası: _COIN_CONFIGS_DDL'den sonra
        anchor = "conn.execute(_COIN_CONFIGS_DDL)"
        if anchor not in content:
            err(f"Anchor bulunamadı: {anchor!r}")
            return False

        daily_summary_ddl = '''
        conn.execute("""
            CREATE TABLE IF NOT EXISTS daily_summary (
                date         TEXT PRIMARY KEY,
                trade_count  INTEGER DEFAULT 0,
                win_count    INTEGER DEFAULT 0,
                loss_count   INTEGER DEFAULT 0,
                win_rate     REAL DEFAULT 0,
                gross_pnl    REAL DEFAULT 0,
                net_pnl      REAL DEFAULT 0,
                avg_r        REAL DEFAULT 0,
                max_drawdown REAL DEFAULT 0,
                balance_eod  REAL DEFAULT 0,
                sent         INTEGER DEFAULT 0,
                best_coin    TEXT DEFAULT '',
                worst_coin   TEXT DEFAULT ''
            )
        """)'''

        new_content = content.replace(
            anchor,
            anchor + "\n" + daily_summary_ddl,
            1  # sadece ilk occurrence
        )

        if new_content == content:
            err("Patch uygulanamadı — içerik değişmedi.")
            return False

        with open(path, "w", encoding="utf-8") as f:
            f.write(new_content)

        ok("database.py — daily_summary DDL eklendi.")
        return True

    except Exception as e:
        err(f"database.py patch hatası: {e}")
        return False


# ── Patch 2: core/ai_decision_engine.py — Ghost stats SQL fix ─────────────────

def patch_ghost_sql():
    info("PATCH 2: core/ai_decision_engine.py — Ghost stats SQL (symbol + simulated_at)")
    path = "core/ai_decision_engine.py"

    try:
        with open(path, "r", encoding="utf-8") as f:
            content = f.read()

        # Zaten düzeltilmiş mi?
        if "JOIN ghost_signals g ON g.id = r.ghost_id" in content and \
           "g.symbol = ?" in content and "r.simulated_at >= ?" in content:
            ok("Ghost SQL zaten düzeltilmiş — atlanıyor.")
            return True

        old_method = '''    def get_symbol_ghost_stats(self, symbol: str, days: int = 14) -> dict:
        """
        Belirli sembol için ghost trade başarı istatistiklerini döner.
        Son N gündeki VETO edilen sinyallerin TP/SL oranı.
        """
        try:
            cutoff = (
                datetime.now(timezone.utc) - timedelta(days=days)
            ).isoformat()
            conn = _open_db(self.db_path, timeout=15)
            try:
                rows = conn.execute(
                    """
                    SELECT virtual_outcome as status, COUNT(*) as cnt
                    FROM ghost_results
                    WHERE symbol = ? AND evaluated_at >= ?
                    AND virtual_outcome IN ('WIN', 'LOSS')
                    GROUP BY virtual_outcome
                    """,
                    (symbol, cutoff),
                ).fetchall()

                tp_hits = 0
                sl_hits = 0
                for r in rows:
                    if r["status"] == "WIN":
                        tp_hits = r["cnt"]
                    elif r["status"] == "LOSS":
                        sl_hits = r["cnt"]

                total = tp_hits + sl_hits
                ghost_wr = round(tp_hits / total * 100, 1) if total > 0 else 0.0

                return {
                    "total": total,
                    "tp_hits": tp_hits,
                    "sl_hits": sl_hits,
                    "ghost_winrate": ghost_wr,
                }
            finally:
                conn.close()
        except Exception as exc:
            logger.debug("Ghost stats alınamadı [%s]: %s", symbol, exc)
            return {"total": 0, "tp_hits": 0, "sl_hits": 0, "ghost_winrate": 0.0}'''

        new_method = '''    def get_symbol_ghost_stats(self, symbol: str, days: int = 14) -> dict:
        """
        Belirli sembol için ghost trade başarı istatistiklerini döner.
        BUG FIX: ghost_results'ta symbol kolonu yok.
        ghost_signals JOIN ghost_results ile sorgu yapılır.
        evaluated_at → simulated_at (doğru kolon adı).
        """
        try:
            cutoff = (
                datetime.now(timezone.utc) - timedelta(days=days)
            ).isoformat()
            conn = _open_db(self.db_path, timeout=15)
            try:
                rows = conn.execute(
                    """
                    SELECT r.virtual_outcome as status, COUNT(*) as cnt
                    FROM ghost_results r
                    JOIN ghost_signals g ON g.id = r.ghost_id
                    WHERE g.symbol = ? AND r.simulated_at >= ?
                    AND r.virtual_outcome IN ('WIN', 'LOSS')
                    GROUP BY r.virtual_outcome
                    """,
                    (symbol, cutoff),
                ).fetchall()

                tp_hits = 0
                sl_hits = 0
                for r in rows:
                    if r["status"] == "WIN":
                        tp_hits = r["cnt"]
                    elif r["status"] == "LOSS":
                        sl_hits = r["cnt"]

                total = tp_hits + sl_hits
                ghost_wr = round(tp_hits / total * 100, 1) if total > 0 else 0.0

                return {
                    "total": total,
                    "tp_hits": tp_hits,
                    "sl_hits": sl_hits,
                    "ghost_winrate": ghost_wr,
                }
            finally:
                conn.close()
        except Exception as exc:
            logger.debug("Ghost stats alınamadı [%s]: %s", symbol, exc)
            return {"total": 0, "tp_hits": 0, "sl_hits": 0, "ghost_winrate": 0.0}'''

        if old_method not in content:
            # Sadece hatalı sorguyu bul ve değiştir (daha esnek)
            bad_query = "WHERE symbol = ? AND evaluated_at >= ?"
            good_query = (
                "FROM ghost_results r\n"
                "                    JOIN ghost_signals g ON g.id = r.ghost_id\n"
                "                    WHERE g.symbol = ? AND r.simulated_at >= ?"
            )
            if bad_query in content:
                # Tam SELECT bloğunu değiştir
                content = content.replace(
                    """                    SELECT virtual_outcome as status, COUNT(*) as cnt
                    FROM ghost_results
                    WHERE symbol = ? AND evaluated_at >= ?
                    AND virtual_outcome IN ('WIN', 'LOSS')
                    GROUP BY virtual_outcome""",
                    """                    SELECT r.virtual_outcome as status, COUNT(*) as cnt
                    FROM ghost_results r
                    JOIN ghost_signals g ON g.id = r.ghost_id
                    WHERE g.symbol = ? AND r.simulated_at >= ?
                    AND r.virtual_outcome IN ('WIN', 'LOSS')
                    GROUP BY r.virtual_outcome""",
                )
                with open(path, "w", encoding="utf-8") as f:
                    f.write(content)
                ok("Ghost SQL sorgusu düzeltildi (minimal patch).")
                return True
            else:
                warn("Ghost SQL metodu bulunamadı — manuel kontrol gerekli.")
                return False

        new_content = content.replace(old_method, new_method, 1)
        if new_content == content:
            err("Ghost patch uygulanamadı.")
            return False

        with open(path, "w", encoding="utf-8") as f:
            f.write(new_content)

        ok("core/ai_decision_engine.py — Ghost SQL düzeltildi.")
        return True

    except Exception as e:
        err(f"Ghost SQL patch hatası: {e}")
        return False


# ── Patch 3: execution_engine.py — Duplicate Telegram çağrısı kaldır ──────────

def patch_execution_engine_telegram():
    info("PATCH 3: execution_engine.py — Duplicate Telegram çağrısı kaldırılıyor")
    path = "execution_engine.py"

    try:
        with open(path, "r", encoding="utf-8") as f:
            content = f.read()

        # Zaten kaldırılmış mı?
        if "send_trade_open as _tg_open" not in content:
            ok("Duplicate Telegram çağrısı zaten yok — atlanıyor.")
            return True

        # open_trade() fonksiyonundaki Telegram bloğunu bul ve kaldır
        # Başlangıç markeri
        start_marker = "            # ── Telegram Trade Açılış Bildirimi"
        end_marker   = "            # ─────────────────────────────────────────────────────────────"

        if start_marker not in content:
            # Alternatif: sadece send_trade_open bloğunu bul
            start_marker = "            try:\n                from telegram_delivery import send_trade_open as _tg_open"
            if start_marker not in content:
                warn("Duplicate Telegram bloğu bulunamadı — zaten temizlenmiş olabilir.")
                return True

        # Bloğu sil
        lines = content.split("\n")
        new_lines = []
        in_block = False
        block_found = False

        for i, line in enumerate(lines):
            if not in_block and "send_trade_open as _tg_open" in line:
                # Birkaç satır öncesinde "# ── Telegram" var mı?
                for j in range(max(0, i-3), i+1):
                    if "Telegram Trade Açılış" in lines[j] or "Telegram bildirim" in lines[j].lower():
                        in_block = True
                        block_found = True
                        # Bu satırı ve önceki yorum satırını atla
                        if new_lines and new_lines[-1].strip().startswith("#"):
                            new_lines.pop()
                        if new_lines and new_lines[-1].strip().startswith("#"):
                            new_lines.pop()
                        break
                if in_block:
                    continue

            if in_block:
                # Bitiş: boş satır + "return trade_id" veya logger.info satırı
                stripped = line.strip()
                if stripped.startswith("return trade_id") or stripped.startswith("logger.info") and "gönderildi" in stripped:
                    in_block = False
                    new_lines.append(line)
                elif stripped == "# ─────────────────────────────────────────────":
                    in_block = False
                    # bu satırı da atla
                else:
                    continue  # bloğu atla
            else:
                new_lines.append(line)

        if not block_found:
            # Daha agresif: sadece send_trade_open çağrısı satırını içeren try bloğunu kaldır
            content_new = re.sub(
                r'            # ─+ Telegram Trade Açılış.*?# ─+\n',
                '',
                content,
                flags=re.DOTALL
            )
            if content_new != content:
                with open(path, "w", encoding="utf-8") as f:
                    f.write(content_new)
                ok("execution_engine.py — Telegram bloğu regex ile kaldırıldı.")
                return True
            warn("Telegram bloğu bulunamadı — execution_engine.py kontrol gerekli.")
            return False

        new_content = "\n".join(new_lines)
        with open(path, "w", encoding="utf-8") as f:
            f.write(new_content)

        # Doğrula
        with open(path, "r") as f:
            verify = f.read()
        if "send_trade_open as _tg_open" in verify:
            warn("Duplicate hala var — manuel kaldırma gerekli.")
            return False

        ok("execution_engine.py — Duplicate Telegram çağrısı kaldırıldı.")
        return True

    except Exception as e:
        err(f"execution_engine.py patch hatası: {e}")
        return False


# ── Patch 4: app.py — /api/diagnostics endpoint ekle ──────────────────────────

def patch_app_diagnostics():
    info("PATCH 4: app.py — /api/diagnostics endpoint ekleniyor")
    path = "app.py"

    try:
        with open(path, "r", encoding="utf-8") as f:
            content = f.read()

        if "/api/diagnostics" in content:
            ok("/api/diagnostics zaten mevcut — atlanıyor.")
            return True

        # /api/learning endpoint'inden sonra ekle
        anchor = "@app.route(\"/api/learning\")"
        if anchor not in content:
            anchor = "@app.route('/api/learning')"

        if anchor not in content:
            warn("app.py'de /api/learning bulunamadı — /api/diagnostics sona ekleniyor.")
            anchor = None

        diagnostics_endpoint = '''
@app.route("/api/diagnostics")
def api_diagnostics():
    """
    Son 24 saatte sinyal neden trade'e dönüşmedi?
    signal_events tablosundan pipeline özeti.
    "Neden trade açılmadı?" sorusunun tek sorgu cevabı.
    """
    try:
        with get_conn() as conn:
            stages = conn.execute("""
                SELECT stage, COUNT(*) as cnt
                FROM signal_events
                WHERE created_at >= datetime('now', '-24 hours')
                GROUP BY stage
                ORDER BY cnt DESC
            """).fetchall()

            top_risk_rejects = conn.execute("""
                SELECT symbol, reject_reason, COUNT(*) as cnt
                FROM signal_events
                WHERE stage = 'RISK_REJECTED'
                  AND created_at >= datetime('now', '-24 hours')
                GROUP BY symbol, reject_reason
                ORDER BY cnt DESC
                LIMIT 10
            """).fetchall()

            top_ai_vetos = conn.execute("""
                SELECT symbol, reject_reason, COUNT(*) as cnt
                FROM signal_events
                WHERE stage = 'AI_VETOED'
                  AND created_at >= datetime('now', '-24 hours')
                GROUP BY symbol, reject_reason
                ORDER BY cnt DESC
                LIMIT 10
            """).fetchall()

            top_execution_rejects = conn.execute("""
                SELECT symbol, reject_reason, COUNT(*) as cnt
                FROM signal_events
                WHERE stage = 'EXECUTION_REJECTED'
                  AND created_at >= datetime('now', '-24 hours')
                GROUP BY reject_reason
                ORDER BY cnt DESC
                LIMIT 5
            """).fetchall()

            last_executed = conn.execute("""
                SELECT signal_id, symbol, created_at
                FROM signal_events
                WHERE stage = 'EXECUTED'
                ORDER BY id DESC
                LIMIT 5
            """).fetchall()

            total_today = conn.execute("""
                SELECT COUNT(*) FROM signal_events
                WHERE created_at >= datetime('now', '-24 hours')
            """).fetchone()[0] or 0

        return _ok({
            "period":          "last_24h",
            "total_events":    total_today,
            "stage_summary":   [{"stage": r[0], "count": r[1]} for r in stages],
            "risk_rejects":    [
                {"symbol": r[0], "reason": r[1], "count": r[2]}
                for r in top_risk_rejects
            ],
            "ai_vetos":        [
                {"symbol": r[0], "reason": r[1], "count": r[2]}
                for r in top_ai_vetos
            ],
            "execution_rejects": [
                {"symbol": r[0], "reason": r[1], "count": r[2]}
                for r in top_execution_rejects
            ],
            "last_executed":   [
                {"signal_id": r[0], "symbol": r[1], "at": r[2]}
                for r in last_executed
            ],
        })
    except Exception as e:
        return _error(str(e))

'''

        if anchor:
            new_content = content.replace(anchor, diagnostics_endpoint + anchor, 1)
        else:
            new_content = content + "\n" + diagnostics_endpoint

        with open(path, "w", encoding="utf-8") as f:
            f.write(new_content)

        ok("app.py — /api/diagnostics eklendi.")
        return True

    except Exception as e:
        err(f"app.py patch hatası: {e}")
        return False


# ── Patch 5: app.py funnel metrikleri — signal_events'ten oku ────────────────

def patch_app_funnel():
    info("PATCH 5: app.py — Funnel metrikleri signal_events'ten okunsun")
    path = "app.py"

    try:
        with open(path, "r", encoding="utf-8") as f:
            content = f.read()

        # Zaten düzeltilmiş mi?
        if "trend_ok" in content and "risk_ok" in content and "ai_veto" in content:
            ok("Funnel metrikleri zaten güncel — atlanıyor.")
            return True

        old_funnel = '''"funnel": {
                "scanned": _get_safe_status("pipeline_scanned"),
                "candidate": _get_safe_status("pipeline_candidate"),'''

        if old_funnel not in content:
            # Alternatif format
            old_funnel = ('"scanned\": _get_safe_status(\"pipeline_scanned\"),'
                          '\n                \"candidate\": _get_safe_status(\"pipeline_candidate\"),')

        new_funnel = '''"funnel": {
                "scanned":   _get_safe_status("pipeline_scanned"),
                "eligible":  _get_safe_status("pipeline_eligible"),
                "trend_ok":  _get_safe_count(
                    "SELECT COUNT(*) FROM signal_events "
                    "WHERE stage='TREND_CHECKED' AND DATE(created_at)=DATE('now')"
                ),
                "trigger_ok": _get_safe_count(
                    "SELECT COUNT(*) FROM signal_events "
                    "WHERE stage='TRIGGER_CHECKED' AND DATE(created_at)=DATE('now')"
                ),
                "risk_ok":   _get_safe_count(
                    "SELECT COUNT(*) FROM signal_events "
                    "WHERE stage='RISK_APPROVED' AND DATE(created_at)=DATE('now')"
                ),
                "risk_reject": _get_safe_count(
                    "SELECT COUNT(*) FROM signal_events "
                    "WHERE stage='RISK_REJECTED' AND DATE(created_at)=DATE('now')"
                ),
                "ai_veto":   _get_safe_count(
                    "SELECT COUNT(*) FROM signal_events "
                    "WHERE stage='AI_VETOED' AND DATE(created_at)=DATE('now')"
                ),
                "executed":  _get_safe_count(
                    "SELECT COUNT(*) FROM signal_events "
                    "WHERE stage='EXECUTED' AND DATE(created_at)=DATE('now')"
                ),'''

        new_content = content.replace(old_funnel, new_funnel, 1)
        if new_content == content:
            warn("Funnel metriği anchor bulunamadı — manuel güncelleme gerekebilir.")
            return False

        with open(path, "w", encoding="utf-8") as f:
            f.write(new_content)

        ok("app.py — Funnel metrikleri signal_events'e bağlandı.")
        return True

    except Exception as e:
        err(f"app.py funnel patch hatası: {e}")
        return False


# ── Patch 6: Yeni servis dosyalarını kopyala ──────────────────────────────────

def copy_new_service_files():
    info("PATCH 6: Yeni servis dosyaları yerine konuluyor")

    # Bu script'in yanında outputs/ klasöründe yeni dosyalar var
    # Claude Code bunu repo root'tan çalıştıracak, dosyalar da root'ta olacak
    files = [
        ("telegram_delivery.py",               "telegram_delivery.py"),
        ("risk_service.py",                    "core/services/risk_service.py"),
        ("scanner_service.py",                 "core/services/scanner_service.py"),
        ("execution_service.py",               "core/services/execution_service.py"),
    ]

    success = True
    for src, dst in files:
        if not os.path.exists(src):
            warn(f"{src} bulunamadı — atlanıyor (zaten yerinde olabilir).")
            continue
        try:
            import shutil
            # Backup
            if os.path.exists(dst):
                backup = dst + ".bak"
                shutil.copy2(dst, backup)
            shutil.copy2(src, dst)
            ok(f"{dst} güncellendi.")
        except Exception as e:
            err(f"{dst} kopyalanamadı: {e}")
            success = False

    return success


# ── Ana akış ──────────────────────────────────────────────────────────────────

if __name__ == "__main__":
    print(f"\n{BOLD}{'='*50}{RESET}")
    print(f"{BOLD}  AurvexAI — Patch Uygulayıcı v6.0{RESET}")
    print(f"{BOLD}{'='*50}{RESET}\n")

    # Doğru dizinde miyiz?
    if not os.path.exists("database.py"):
        print(f"{RED}HATA: Bu script trade-engine/ kök dizininden çalıştırılmalı.{RESET}")
        print(f"Şu an: {os.getcwd()}")
        sys.exit(1)

    results = []
    results.append(("daily_summary DDL",         patch_database_daily_summary()))
    results.append(("Ghost SQL fix",              patch_ghost_sql()))
    results.append(("Execution duplicate TG",     patch_execution_engine_telegram()))
    results.append(("app.py /api/diagnostics",    patch_app_diagnostics()))
    results.append(("app.py funnel metrikleri",   patch_app_funnel()))
    results.append(("Servis dosyaları",           copy_new_service_files()))

    print(f"\n{BOLD}{'='*50}{RESET}")
    print(f"{BOLD}  ÖZET{RESET}")
    print(f"{BOLD}{'='*50}{RESET}")

    all_ok = True
    for name, result in results:
        status = f"{GREEN}BAŞARILI{RESET}" if result else f"{RED}BAŞARISIZ{RESET}"
        print(f"  {name:<35} {status}")
        if not result:
            all_ok = False

    print()
    if all_ok:
        print(f"{GREEN}{BOLD}Tüm patch'ler uygulandı. verify_fixes.py çalıştır.{RESET}")
    else:
        print(f"{YELLOW}Bazı patch'ler başarısız — yukarıdaki hataları incele.{RESET}")
        print(f"{YELLOW}Başarısız olanları manuel uygula ve verify_fixes.py çalıştır.{RESET}")
    print()
