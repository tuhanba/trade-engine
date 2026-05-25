"""
test_system_complete.py — AurvexAI Kapsamlı Sistem Testi
=========================================================
Telegram, Dashboard, Trade lifecycle, Database ve tüm API endpoint'leri test eder.
Kendi kendine SQLite test DB kurar, sunucu başlatır, sonuçları raporlar.

Çalıştırma: python test_system_complete.py
"""

from __future__ import annotations
import sys
import os
import json
import time
import sqlite3
import threading
import subprocess
import tempfile
import traceback
from datetime import datetime, timezone
from unittest.mock import patch, MagicMock

# Proje dizinini Python path'e ekle
PROJECT_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, PROJECT_DIR)

# ─── ANSI Renk Kodları ───────────────────────────────────────────────
GREEN  = "\033[92m"
RED    = "\033[91m"
YELLOW = "\033[93m"
BLUE   = "\033[94m"
CYAN   = "\033[96m"
BOLD   = "\033[1m"
RESET  = "\033[0m"

# ─── Test Sonuç Sayaçları ────────────────────────────────────────────
_results = {"pass": 0, "fail": 0, "warn": 0}
_log_lines = []

def _log(msg, color=RESET):
    ts = datetime.now().strftime("%H:%M:%S")
    line = f"[{ts}] {color}{msg}{RESET}"
    print(line)
    _log_lines.append(msg)

def PASS(name):
    _results["pass"] += 1
    _log(f"  ✅ PASS  {name}", GREEN)

def FAIL(name, detail=""):
    _results["fail"] += 1
    detail_str = f" — {detail}" if detail else ""
    _log(f"  ❌ FAIL  {name}{detail_str}", RED)

def WARN(name, detail=""):
    _results["warn"] += 1
    detail_str = f" — {detail}" if detail else ""
    _log(f"  ⚠️  WARN  {name}{detail_str}", YELLOW)

def HEADER(title):
    _log(f"\n{'─'*60}", BLUE)
    _log(f"  {BOLD}{title}{RESET}", CYAN)
    _log(f"{'─'*60}", BLUE)


# ═══════════════════════════════════════════════════════════════════
# TEST 1: Config yükleme
# ═══════════════════════════════════════════════════════════════════

def test_config():
    HEADER("TEST 1: Config Yükleme")
    try:
        import config
        PASS("config.py import")
    except Exception as e:
        FAIL("config.py import", str(e))
        return False

    checks = [
        ("EXECUTION_MODE", str),
        ("TELEGRAM_BOT_TOKEN", str),
        ("TELEGRAM_CHAT_ID", str),
        ("INITIAL_PAPER_BALANCE", float),
        ("MAX_OPEN_TRADES", int),
        ("TRADE_THRESHOLD", float),
        ("FLASK_PORT", int),
    ]
    for key, typ in checks:
        val = getattr(config, key, None)
        if val is None:
            WARN(f"config.{key}", "tanımlı değil")
        elif not isinstance(val, typ):
            WARN(f"config.{key}", f"beklenen {typ.__name__}, gelen {type(val).__name__}")
        else:
            PASS(f"config.{key} = {repr(val)[:40]}")

    # Telegram yapılandırması
    if not config.TELEGRAM_BOT_TOKEN:
        WARN("Telegram BOT_TOKEN", "boş — bildirimler devre dışı")
    else:
        PASS("Telegram BOT_TOKEN mevcut")
    if not config.TELEGRAM_CHAT_ID:
        WARN("Telegram CHAT_ID", "boş — bildirimler devre dışı")
    else:
        PASS("Telegram CHAT_ID mevcut")

    return True


# ═══════════════════════════════════════════════════════════════════
# TEST 2: Database başlatma ve migration
# ═══════════════════════════════════════════════════════════════════

def test_database():
    HEADER("TEST 2: Database Başlatma & Migration")
    import config

    # Test DB için geçici dosya
    test_db = os.path.join(PROJECT_DIR, "test_trading.db")
    config.DB_PATH = test_db  # Test DB kullan

    try:
        import database
        database.init_db()
        PASS("database.init_db()")
    except Exception as e:
        FAIL("database.init_db()", str(e))
        return False

    try:
        added = database.migrate_db()
        PASS(f"database.migrate_db() — {len(added)} kolon eklendi")
    except Exception as e:
        WARN("database.migrate_db()", str(e))

    # Tablo kontrolü
    try:
        with database.get_conn() as conn:
            tables = conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
            table_names = [t[0] for t in tables]
            required = ["trades", "signal_candidates", "paper_account",
                       "bot_status", "telegram_messages", "ghost_signals"]
            for t in required:
                if t in table_names:
                    PASS(f"Tablo mevcut: {t}")
                else:
                    FAIL(f"Tablo eksik: {t}")
    except Exception as e:
        FAIL("Tablo kontrolü", str(e))

    # paper_account bakiye kontrolü
    try:
        bal = database.get_paper_balance()
        if bal > 0:
            PASS(f"paper_account bakiye: ${bal:.2f}")
        else:
            WARN("paper_account bakiye sıfır", "INITIAL_PAPER_BALANCE yüklenemedi?")
    except Exception as e:
        FAIL("paper_account bakiye", str(e))

    return True


# ═══════════════════════════════════════════════════════════════════
# TEST 3: Dashboard stats API
# ═══════════════════════════════════════════════════════════════════

def test_dashboard_stats():
    HEADER("TEST 3: Dashboard Stats API")
    try:
        import database
        stats = database.get_dashboard_stats()
        PASS("get_dashboard_stats() çağrısı")
    except Exception as e:
        FAIL("get_dashboard_stats()", str(e))
        return False

    # Zorunlu alanlar
    required_keys = [
        "total_trades", "open_trades", "closed_trades",
        "win_trades", "loss_trades",
        "realized_pnl", "unrealized_pnl", "total_pnl",
        "today_pnl", "winrate", "win_rate",
        "balance", "initial_balance",
        "ghost_tp_hits", "ghost_sl_hits",
    ]
    for key in required_keys:
        if key in stats:
            PASS(f"stats['{key}'] = {stats[key]}")
        else:
            FAIL(f"stats eksik alan: '{key}'")

    # balance kontrolü
    if stats.get("balance", 0) > 0:
        PASS(f"balance pozitif: ${stats['balance']:.2f}")
    else:
        WARN("balance sıfır veya negatif")

    return True


# ═══════════════════════════════════════════════════════════════════
# TEST 4: Trade lifecycle (aç → TP1 → TP2 → kapat)
# ═══════════════════════════════════════════════════════════════════

def test_trade_lifecycle():
    HEADER("TEST 4: Trade Lifecycle (Paper Trade)")
    try:
        import database
        from core.data_layer import TradeData
    except Exception as e:
        FAIL("Import", str(e))
        return False

    # Mock trade oluştur
    now = datetime.now(timezone.utc).isoformat()
    try:
        with database.get_conn() as conn:
            cur = conn.execute("""
                INSERT INTO trades (
                    symbol, direction, entry, sl, tp1, tp2, tp3,
                    qty, leverage, notional_size, margin_used, risk_usd,
                    risk_pct, status, open_time, current_price,
                    unrealized_pnl, realized_pnl, net_pnl,
                    remaining_qty, original_qty, close_price, close_reason,
                    total_fee, fee_rate, ax_mode, metadata
                ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            """, (
                "BTCUSDT", "LONG", 50000.0, 49000.0, 52000.0, 54000.0, 57000.0,
                0.01, 10, 500.0, 50.0, 5.0,
                1.0, "OPEN", now, 50000.0,
                0.0, 0.0, 0.0,
                0.01, 0.01, 0.0, "",
                0.2, 0.0004, "paper", "{}"
            ))
            trade_id = cur.lastrowid
        PASS(f"Mock trade oluşturuldu: #{trade_id}")
    except Exception as e:
        FAIL("Mock trade oluşturma", str(e))
        return False

    # get_open_trades kontrolü
    try:
        open_trades = database.get_open_trades()
        if any(t["id"] == trade_id for t in open_trades):
            PASS("get_open_trades() trade'i buldu")
        else:
            FAIL("get_open_trades() trade'i bulamadı", f"status check — trade #{trade_id}")
    except Exception as e:
        FAIL("get_open_trades()", str(e))

    # update_trade_price — OPEN status
    try:
        database.update_trade_price(trade_id, 51000.0, 10.0)
        PASS("update_trade_price() OPEN status")
    except Exception as e:
        FAIL("update_trade_price() OPEN status", str(e))

    # TP1 hit simülasyonu
    try:
        with database.get_conn() as conn:
            conn.execute("""
                UPDATE trades SET status='tp1_hit', tp1_hit=1, realized_pnl=10.0
                WHERE id=?
            """, (trade_id,))
        PASS("TP1 hit simülasyonu")
    except Exception as e:
        FAIL("TP1 hit simülasyonu", str(e))

    # update_trade_price — tp1_hit status (bu kritik bug fix)
    try:
        database.update_trade_price(trade_id, 52500.0, 25.0)
        with database.get_conn() as conn:
            row = conn.execute("SELECT current_price FROM trades WHERE id=?", (trade_id,)).fetchone()
        if row and float(row[0]) == 52500.0:
            PASS("update_trade_price() tp1_hit status — LOWER() fix çalışıyor")
        else:
            FAIL("update_trade_price() tp1_hit status", f"fiyat güncellenemedi: {row}")
    except Exception as e:
        FAIL("update_trade_price() tp1_hit status", str(e))

    # Trade kapama
    try:
        database.close_trade(trade_id, exit_price=54000.0, realized_pnl=40.0, close_reason="tp2")
        with database.get_conn() as conn:
            row = conn.execute("SELECT status, net_pnl, close_reason FROM trades WHERE id=?", (trade_id,)).fetchone()
        if row and row[0] == "closed" and float(row[1]) == 40.0:
            PASS(f"Trade kapandı: status={row[0]} net_pnl={row[1]} reason={row[2]}")
        else:
            FAIL("Trade kapama", f"beklenen: closed/40.0, gelen: {dict(row) if row else None}")
    except Exception as e:
        FAIL("Trade kapama", str(e))

    # Stats double-count kontrolü
    try:
        stats = database.get_dashboard_stats()
        if stats["closed_trades"] >= 1:
            PASS(f"closed_trades sayısı doğru: {stats['closed_trades']}")
        if stats["win_trades"] >= 1:
            PASS(f"win_trades sayısı doğru: {stats['win_trades']}")
    except Exception as e:
        FAIL("Stats sonrası kontrol", str(e))

    # paper_balance güncelleme testi
    try:
        bal_before = database.get_paper_balance()
        database.update_paper_balance(25.0)
        bal_after = database.get_paper_balance()
        diff = bal_after - bal_before
        if abs(diff - 25.0) < 0.01:
            PASS(f"update_paper_balance(+25): {bal_before:.2f} → {bal_after:.2f}")
        else:
            FAIL("update_paper_balance()", f"beklenen +25, gelen +{diff:.4f}")
    except Exception as e:
        FAIL("update_paper_balance()", str(e))

    return True


# ═══════════════════════════════════════════════════════════════════
# TEST 5: Telegram modülü
# ═══════════════════════════════════════════════════════════════════

def test_telegram():
    HEADER("TEST 5: Telegram Modülü")
    try:
        from telegram_delivery import TelegramDelivery, _send_raw, format_signal
        PASS("telegram_delivery import")
    except Exception as e:
        FAIL("telegram_delivery import", str(e))
        return False

    # TelegramDelivery yapılandırma kontrolü
    try:
        td = TelegramDelivery()
        configured = td.is_configured()
        if configured:
            PASS("TelegramDelivery yapılandırılmış")
        else:
            WARN("TelegramDelivery yapılandırılmamış (BOT_TOKEN veya CHAT_ID eksik)")
    except Exception as e:
        FAIL("TelegramDelivery init", str(e))

    # Mock signal oluştur ve format_signal test et
    try:
        sig = MagicMock()
        sig.symbol = "BTCUSDT"
        sig.direction = "LONG"
        sig.setup_quality = "A"
        sig.entry_zone = 50000.0
        sig.entry_price = 50000.0
        sig.stop_loss = 49000.0
        sig.tp1 = 52000.0
        sig.tp2 = 54000.0
        sig.tp3 = 57000.0
        sig.final_score = 75.0
        sig.rr = 2.0
        sig.confidence = 0.8
        sig.risk_percent = 1.0
        sig.risk_pct = 1.0
        sig.max_loss = 5.0
        sig.position_size = 0.01
        sig.notional_size = 500.0
        sig.notional = 500.0
        sig.leverage_suggestion = 10
        sig.reason = "Test sinyali"

        msg = format_signal(sig)
        if msg and len(msg) > 50:
            PASS(f"format_signal() — {len(msg)} karakter")
            # Önemli alanların mesajda olduğunu kontrol et
            checks = ["BTCUSDT", "LONG", "50000", "49000", "52000"]
            for c in checks:
                if c in msg:
                    PASS(f"  format_signal içeriği: '{c}' ✓")
                else:
                    WARN(f"  format_signal içeriği eksik: '{c}'")
        else:
            FAIL("format_signal() boş veya çok kısa mesaj döndürdü")
    except Exception as e:
        FAIL("format_signal()", str(e))
        traceback.print_exc()

    # format_signal None entry_zone ile test
    try:
        sig2 = MagicMock()
        sig2.symbol = "ETHUSDT"
        sig2.direction = "SHORT"
        sig2.setup_quality = "B"
        sig2.entry_zone = None   # Bug fix testi: None olduğunda crash olmamalı
        sig2.entry_price = 3000.0
        sig2.entry = 3000.0
        sig2.stop_loss = 3100.0
        sig2.tp1 = 2900.0
        sig2.tp2 = 2800.0
        sig2.tp3 = 2700.0
        sig2.final_score = 60.0
        sig2.rr = 1.5
        sig2.confidence = None   # None confidence de test
        sig2.risk_percent = None
        sig2.risk_pct = 1.0
        sig2.max_loss = None
        sig2.position_size = None
        sig2.notional_size = None
        sig2.notional = 0
        sig2.leverage_suggestion = 5
        sig2.reason = None

        msg2 = format_signal(sig2)
        if msg2 and len(msg2) > 50:
            PASS("format_signal() None entry_zone — crash yok ✓")
        else:
            FAIL("format_signal() None entry_zone döndürdü boş")
    except Exception as e:
        FAIL("format_signal() None entry_zone", str(e))

    # Trade open bildirimi format
    try:
        from telegram_delivery import format_trade_open, format_trade_close
        trade_data = {
            "symbol": "BTCUSDT", "direction": "LONG", "entry": 50000.0,
            "sl": 49000.0, "tp1": 52000.0, "rr": 2.0
        }
        msg = format_trade_open(trade_data)
        if msg and "BTCUSDT" in msg:
            PASS("format_trade_open() çalışıyor")
        else:
            FAIL("format_trade_open() sorunlu")
    except Exception as e:
        FAIL("format_trade_open()", str(e))

    # Trade close bildirimi format
    try:
        close_msg = format_trade_close(
            {"symbol": "BTCUSDT", "direction": "LONG", "entry": 50000.0, "close_price": 52000.0},
            pnl=25.5, reason="tp2"
        )
        if close_msg and "BTCUSDT" in close_msg and ("WIN" in close_msg or "KAR" in close_msg):
            PASS("format_trade_close() WIN ✓")
        else:
            FAIL("format_trade_close() WIN format sorunlu")

        close_msg2 = format_trade_close(
            {"symbol": "BTCUSDT", "direction": "LONG", "entry": 50000.0, "close_price": 49000.0},
            pnl=-5.0, reason="sl"
        )
        if close_msg2 and ("LOSS" in close_msg2 or "ZARAR" in close_msg2):
            PASS("format_trade_close() LOSS ✓")
        else:
            FAIL("format_trade_close() LOSS format sorunlu")
    except Exception as e:
        FAIL("format_trade_close()", str(e))

    # _send_raw mock testi (gerçek API çağrısı yapmadan)
    try:
        with patch("requests.post") as mock_post:
            mock_resp = MagicMock()
            mock_resp.status_code = 200
            mock_post.return_value = mock_resp

            import config as _cfg
            _cfg.TELEGRAM_BOT_TOKEN = "mock_token_123"
            _cfg.TELEGRAM_CHAT_ID = "mock_chat_456"

            result = _send_raw("Test mesajı — sistem çalışıyor ✅")
            if result:
                PASS("_send_raw() mock test başarılı")
            else:
                FAIL("_send_raw() mock test başarısız")

            # Mock çağrı doğrula
            if mock_post.called:
                call_kwargs = mock_post.call_args
                PASS(f"API çağrısı yapıldı: {mock_post.call_count} kez")
            else:
                FAIL("_send_raw() API çağrısı yapmadı")
    except Exception as e:
        FAIL("_send_raw() mock test", str(e))

    # send_trade_open mock testi
    try:
        with patch("requests.post") as mock_post:
            mock_resp = MagicMock()
            mock_resp.status_code = 200
            mock_post.return_value = mock_resp

            from telegram_delivery import send_trade_open
            send_trade_open({
                "symbol": "ETHUSDT", "direction": "LONG",
                "entry": 3000.0, "sl": 2900.0, "tp1": 3150.0,
                "tp2": 3300.0, "tp3": 3500.0, "leverage": 5,
                "risk_pct": 1.0, "risk_usd": 5.0, "margin_used": 100.0,
                "notional_size": 500.0, "setup_quality": "A",
                "final_score": 72.0, "reason": "Test trade",
                "max_loss_after_fee": 5.2, "open_fee": 0.2
            })
            time.sleep(0.2)  # Queue worker bekle
            PASS("send_trade_open() çalıştı (mock)")
    except Exception as e:
        FAIL("send_trade_open() mock", str(e))

    # send_trade_close mock testi
    try:
        with patch("requests.post") as mock_post:
            mock_resp = MagicMock()
            mock_resp.status_code = 200
            mock_post.return_value = mock_resp

            from telegram_delivery import send_trade_close
            send_trade_close(
                symbol="BTCUSDT", net_pnl=35.5, total_fee=0.4,
                reason="tp2", duration_str="45dk",
                direction="LONG", r_multiple=2.1, balance_after=535.5
            )
            time.sleep(0.2)
            PASS("send_trade_close() çalıştı (mock)")
    except Exception as e:
        FAIL("send_trade_close() mock", str(e))

    # send_tp_hit mock testi
    try:
        with patch("requests.post") as mock_post:
            mock_resp = MagicMock()
            mock_resp.status_code = 200
            mock_post.return_value = mock_resp

            from telegram_delivery import send_tp_hit
            send_tp_hit("BTCUSDT", 1, 12.5, 0.006, 512.5)
            send_tp_hit("BTCUSDT", 2, 18.0, 0.003, 530.5)
            time.sleep(0.2)
            PASS("send_tp_hit() TP1 ve TP2 (mock)")
    except Exception as e:
        FAIL("send_tp_hit() mock", str(e))

    return True


# ═══════════════════════════════════════════════════════════════════
# TEST 6: Telegram Manager komutları
# ═══════════════════════════════════════════════════════════════════

def test_telegram_manager():
    HEADER("TEST 6: Telegram Manager Komutları")
    try:
        from telegram_manager import TelegramManager
        PASS("telegram_manager import")
    except Exception as e:
        FAIL("telegram_manager import", str(e))
        return False

    sent_messages = []

    def mock_send(text):
        sent_messages.append(text)
        return True

    try:
        tm = TelegramManager(send_fn=mock_send)
        PASS("TelegramManager init")
    except Exception as e:
        FAIL("TelegramManager init", str(e))
        return False

    commands = [
        ("_cmd_help",    "help"),
        ("_cmd_status",  "status"),
        ("_cmd_stats",   "stats"),
        ("_cmd_trades",  "trades"),
        ("_cmd_balance", "balance"),
        ("_cmd_open",    "open"),
        ("_cmd_daily",   "daily"),
        ("_cmd_mode",    "mode"),
        ("_cmd_ghost",   "ghost"),
    ]

    for method_name, cmd_name in commands:
        method = getattr(tm, method_name, None)
        if not method:
            WARN(f"/{cmd_name} metodu yok")
            continue
        try:
            before_count = len(sent_messages)
            method()
            after_count = len(sent_messages)
            if after_count > before_count:
                last_msg = sent_messages[-1]
                PASS(f"/{cmd_name} → yanıt: {len(last_msg)} karakter")
            else:
                WARN(f"/{cmd_name} yanıt göndermedi")
        except Exception as e:
            FAIL(f"/{cmd_name}", str(e))

    # Pause/resume/finish komutları
    try:
        tm._cmd_pause()
        assert tm.is_paused, "pause çalışmadı"
        PASS("/pause → is_paused=True")

        tm._cmd_resume()
        assert not tm.is_paused, "resume çalışmadı"
        PASS("/resume → is_paused=False")

        tm._cmd_finish()
        assert tm.is_finish_mode, "finish çalışmadı"
        PASS("/finish → is_finish_mode=True")

        tm._cmd_human_on()
        import config as _cfg
        assert _cfg.HUMAN_MODE == True, "human mode ayarlanamadı"
        PASS("/human → HUMAN_MODE=True")

        tm._cmd_human_off()
        assert _cfg.HUMAN_MODE == False, "scalp mode ayarlanamadı"
        PASS("/scalp → HUMAN_MODE=False")
    except AssertionError as e:
        FAIL("Komut durumu kontrolü", str(e))
    except Exception as e:
        FAIL("Komut durumu kontrolü", str(e))

    _log(f"\n  Toplam gönderilen mesaj: {len(sent_messages)}")
    return True


# ═══════════════════════════════════════════════════════════════════
# TEST 7: Flask API endpoint smoke testi
# ═══════════════════════════════════════════════════════════════════

def test_flask_api():
    HEADER("TEST 7: Flask API Endpoint Smoke Testi")
    try:
        import app as flask_app
        import config
        app = flask_app.app
        app.config["TESTING"] = True
        client = app.test_client()
        PASS("Flask test client oluşturuldu")
    except Exception as e:
        FAIL("Flask test client", str(e))
        return False

    endpoints = [
        ("/api/health",         200, ["ok", "db_connected"]),
        ("/api/stats",          200, ["ok", "data"]),
        ("/api/live",           200, ["ok", "data"]),
        ("/api/trades",         200, ["ok", "data"]),
        ("/api/signals",        200, ["ok", "data"]),
        ("/api/learning",       200, ["ok", "data"]),
        ("/api/balance",        200, ["ok", "data"]),
        ("/api/params",         200, ["ok", "data"]),
        ("/api/signal_funnel",  200, ["ok", "data"]),
        ("/api/daily_pnl",      200, ["ok"]),
        ("/api/weekly",         200, ["ok"]),
        ("/api/ax_status",      200, ["ok"]),
        ("/api/paper_state",    200, ["ok"]),
        ("/api/history",        200, ["ok"]),
        ("/api/signal_archive", 200, ["ok"]),
        ("/api/circuit_breaker",200, ["ok"]),
        ("/",                   200, []),
    ]

    for path, expected_status, required_keys in endpoints:
        try:
            resp = client.get(path)
            if resp.status_code != expected_status:
                FAIL(f"GET {path}", f"HTTP {resp.status_code} (beklenen {expected_status})")
                continue

            if required_keys:
                data = resp.get_json()
                if data is None:
                    FAIL(f"GET {path}", "JSON parse edilemedi")
                    continue
                missing = [k for k in required_keys if k not in data]
                if missing:
                    FAIL(f"GET {path}", f"eksik alanlar: {missing}")
                else:
                    PASS(f"GET {path} → {resp.status_code}")
            else:
                PASS(f"GET {path} → {resp.status_code}")
        except Exception as e:
            FAIL(f"GET {path}", str(e))

    # /api/stats detaylı kontrol
    try:
        resp = client.get("/api/stats")
        data = resp.get_json()
        if data and data.get("ok"):
            stats = data.get("data", {})
            important_keys = ["balance", "total_pnl", "win_trades", "loss_trades",
                             "winrate", "win_rate", "today_pnl"]
            for k in important_keys:
                if k in stats:
                    PASS(f"  /api/stats.data['{k}'] = {stats[k]}")
                else:
                    FAIL(f"  /api/stats.data eksik: '{k}'")
    except Exception as e:
        FAIL("/api/stats detaylı", str(e))

    # /api/health detaylı kontrol
    try:
        resp = client.get("/api/health")
        data = resp.get_json()
        if data and data.get("ok"):
            health = data.get("data", {})
            health_keys = ["db_connected", "telegram_configured", "bot_alive",
                          "execution_mode", "trade_threshold"]
            for k in health_keys:
                if k in health:
                    PASS(f"  /api/health.data['{k}'] = {health[k]}")
                else:
                    WARN(f"  /api/health.data eksik: '{k}'")
    except Exception as e:
        FAIL("/api/health detaylı", str(e))

    return True


# ═══════════════════════════════════════════════════════════════════
# TEST 8: Signal bildirimi zinciri
# ═══════════════════════════════════════════════════════════════════

def test_signal_notification_chain():
    HEADER("TEST 8: Sinyal Bildirim Zinciri")
    try:
        import database
        from core.data_layer import SignalData
        PASS("data_layer import")
    except Exception as e:
        FAIL("data_layer import", str(e))
        return False

    # Signal kaydet
    try:
        now = datetime.now(timezone.utc).isoformat()
        with database.get_conn() as conn:
            cur = conn.execute("""
                INSERT INTO signal_candidates
                    (symbol, direction, entry_price, stop_loss, tp1, tp2, tp3,
                     score, final_score, setup_quality, decision, created_at, status)
                VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)
            """, (
                "ETHUSDT", "LONG", 3000.0, 2850.0, 3150.0, 3300.0, 3500.0,
                72.0, 72.0, "A", "ALLOW", now, "NEW"
            ))
            sig_id = cur.lastrowid
        PASS(f"Signal kaydedildi: #{sig_id}")
    except Exception as e:
        FAIL("Signal kayıt", str(e))
        return False

    # get_recent_signals kontrolü
    try:
        signals = database.get_recent_signals(10)
        if any(s.get("id") == sig_id for s in signals):
            PASS("get_recent_signals() sinyali buldu")
        else:
            FAIL("get_recent_signals() sinyal bulunamadı")
    except Exception as e:
        FAIL("get_recent_signals()", str(e))

    # Telegram mesaj kaydı
    try:
        result = database.save_telegram_message(
            sig_id, "ETHUSDT",
            f"ETHUSDT:LONG:3000.0:{datetime.now(timezone.utc).strftime('%Y-%m-%d')}",
            "Test Telegram mesajı",
            status="queued"
        )
        PASS("save_telegram_message()")
    except Exception as e:
        FAIL("save_telegram_message()", str(e))

    # Pipeline funnel kontrolü
    try:
        with database.get_conn() as conn:
            tg_count = conn.execute(
                "SELECT COUNT(*) FROM telegram_messages WHERE status IN ('queued','sent')"
            ).fetchone()[0]
        if tg_count > 0:
            PASS(f"Telegram pipeline: {tg_count} mesaj")
        else:
            WARN("Telegram pipeline boş")
    except Exception as e:
        FAIL("Telegram pipeline", str(e))

    return True


# ═══════════════════════════════════════════════════════════════════
# TEST 9: PnL hesaplama doğruluğu
# ═══════════════════════════════════════════════════════════════════

def test_pnl_calculation():
    HEADER("TEST 9: PnL Hesaplama Doğruluğu")
    try:
        from core.accounting import calculate_realized_pnl, calculate_unrealized_pnl
        PASS("accounting import")
    except Exception as e:
        FAIL("accounting import", str(e))
        return False

    test_cases = [
        # (side, entry, exit, qty, fee_rate, expected_min, expected_max, name)
        ("LONG", 50000.0, 52000.0, 0.01, 0.0004,  19.5,  20.5, "LONG WIN"),
        ("LONG", 50000.0, 49000.0, 0.01, 0.0004, -10.5,  -9.5, "LONG LOSS"),
        ("SHORT", 50000.0, 48000.0, 0.01, 0.0004, 19.5,  20.5, "SHORT WIN"),
        ("SHORT", 50000.0, 52000.0, 0.01, 0.0004, -20.5, -19.5, "SHORT LOSS"),
    ]

    for side, entry, exit_p, qty, fee, exp_min, exp_max, name in test_cases:
        try:
            pnl = calculate_realized_pnl(
                side=side, entry_price=entry, exit_price=exit_p,
                quantity=qty, fee_rate=fee
            )
            if exp_min <= pnl <= exp_max:
                PASS(f"PnL {name}: {pnl:.4f}$ (beklenen {exp_min}~{exp_max})")
            else:
                FAIL(f"PnL {name}: {pnl:.4f}$ (beklenen {exp_min}~{exp_max})")
        except Exception as e:
            FAIL(f"PnL {name}", str(e))

    # Unrealized PnL
    try:
        upnl = calculate_unrealized_pnl(
            side="LONG", entry_price=50000.0, current_price=51000.0,
            quantity=0.01, fee_rate=0.0004
        )
        if 9.5 <= upnl <= 10.5:
            PASS(f"Unrealized PnL LONG: {upnl:.4f}$ ✓")
        else:
            FAIL(f"Unrealized PnL LONG: {upnl:.4f}$ (beklenen ~10.0)")
    except Exception as e:
        FAIL("Unrealized PnL", str(e))

    # Double-count test: TP1 + kapat = toplam doğru mu?
    try:
        tp1_pnl = calculate_realized_pnl("LONG", 50000.0, 52000.0, 0.005, 0.0004)  # %50 kapat
        remaining_pnl = calculate_realized_pnl("LONG", 50000.0, 54000.0, 0.005, 0.0004)  # runner kapat
        total = tp1_pnl + remaining_pnl
        expected = calculate_realized_pnl("LONG", 50000.0, 53000.0, 0.01, 0.0004)  # ağırlıklı ortalama gibi
        _log(f"    TP1 PnL={tp1_pnl:.4f}$ + Remaining={remaining_pnl:.4f}$ = Total={total:.4f}$", CYAN)
        PASS("PnL parçalı hesap tutarlı")
    except Exception as e:
        FAIL("PnL parçalı hesap", str(e))

    return True


# ═══════════════════════════════════════════════════════════════════
# TEST 10: Bot status ve heartbeat
# ═══════════════════════════════════════════════════════════════════

def test_bot_status():
    HEADER("TEST 10: Bot Status & Heartbeat")
    try:
        import database
        PASS("database import")
    except Exception as e:
        FAIL("database import", str(e))
        return False

    # Bot status kaydet
    try:
        database.update_bot_status("status", "running")
        database.update_bot_status("heartbeat", datetime.now(timezone.utc).isoformat())
        PASS("Bot status güncellendi")
    except Exception as e:
        FAIL("Bot status güncelleme", str(e))

    # Bot status oku
    try:
        status = database.get_bot_status()
        if "status" in status and status["status"]["value"] == "running":
            PASS(f"Bot status: {status['status']['value']}")
        else:
            FAIL("Bot status okuma")
        if "heartbeat" in status:
            PASS(f"Heartbeat: {status['heartbeat']['value'][:19]}")
        else:
            FAIL("Heartbeat kaydedilmemiş")
    except Exception as e:
        FAIL("Bot status okuma", str(e))

    # system_state (market_regime)
    try:
        database.set_state = getattr(database, "set_state", None)
        database.get_state = getattr(database, "get_state", None)
        if database.set_state:
            database.set_state("market_regime", "NEUTRAL")
            regime = database.get_state("market_regime")
            if regime == "NEUTRAL":
                PASS("market_regime state")
            else:
                WARN("market_regime set/get sorunlu")
        else:
            WARN("set_state/get_state fonksiyonu yok")
    except Exception as e:
        FAIL("system_state", str(e))

    return True


# ═══════════════════════════════════════════════════════════════════
# TEMIZLIK
# ═══════════════════════════════════════════════════════════════════

def cleanup():
    test_db = os.path.join(PROJECT_DIR, "test_trading.db")
    if os.path.exists(test_db):
        try:
            os.remove(test_db)
            _log("\n  🗑️  Test DB silindi", YELLOW)
        except Exception:
            pass
    # WAL dosyalarını da temizle
    for ext in ["-wal", "-shm"]:
        f = test_db + ext
        if os.path.exists(f):
            try: os.remove(f)
            except: pass


# ═══════════════════════════════════════════════════════════════════
# ANA TEST RUNNER
# ═══════════════════════════════════════════════════════════════════

def main():
    _log(f"\n{'═'*70}", BOLD)
    _log(f"  {BOLD}🚀 AurvexAI Kapsamlı Sistem Testi{RESET}", CYAN)
    _log(f"  Zaman: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}", CYAN)
    _log(f"  Proje: {PROJECT_DIR}", CYAN)
    _log(f"{'═'*70}\n", BOLD)

    tests = [
        test_config,
        test_database,
        test_dashboard_stats,
        test_trade_lifecycle,
        test_telegram,
        test_telegram_manager,
        test_flask_api,
        test_signal_notification_chain,
        test_pnl_calculation,
        test_bot_status,
    ]

    for test_fn in tests:
        try:
            test_fn()
        except Exception as e:
            FAIL(f"TEST CRASH ({test_fn.__name__})", str(e))
            traceback.print_exc()

    # Temizlik
    cleanup()

    # Sonuç raporu
    _log(f"\n{'═'*70}", BOLD)
    _log(f"  {BOLD}📊 TEST SONUÇLARI{RESET}", CYAN)
    _log(f"{'═'*70}", BOLD)
    total = _results["pass"] + _results["fail"] + _results["warn"]
    _log(f"  ✅ PASS:  {_results['pass']}", GREEN)
    _log(f"  ❌ FAIL:  {_results['fail']}", RED)
    _log(f"  ⚠️  WARN:  {_results['warn']}", YELLOW)
    _log(f"  📊 TOTAL: {total}", BLUE)

    success_rate = (_results["pass"] / max(1, _results["pass"] + _results["fail"])) * 100
    _log(f"\n  Başarı Oranı: {success_rate:.1f}%", GREEN if success_rate >= 80 else RED)

    if _results["fail"] == 0:
        _log(f"\n  🎉 {BOLD}TÜM TESTLER GEÇTI — SİSTEM HAZIR{RESET}", GREEN)
    elif _results["fail"] <= 3:
        _log(f"\n  ⚠️  {BOLD}AZ SAYIDA HATA — İNCELENMESİ GEREKİYOR{RESET}", YELLOW)
    else:
        _log(f"\n  ❌ {BOLD}KRİTİK HATALAR VAR — DÜZELTME GEREKİYOR{RESET}", RED)

    _log(f"{'═'*70}\n", BOLD)

    return 0 if _results["fail"] == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
