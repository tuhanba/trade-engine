"""
tests/test_database.py – Database init/migration testleri.
"""

import sys
import os
import tempfile
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import config
import database
from core.data_layer import SignalData, TradeData


def test_init_db():
    """DB init çalışmalı ve tablolar oluşmalı."""
    original = config.DB_PATH
    tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
    config.DB_PATH = tmp.name
    tmp.close()

    try:
        database.init_db()

        conn = database.get_connection()
        tables = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()
        table_names = {r[0] for r in tables}
        conn.close()

        assert "trades" in table_names
        assert "signal_candidates" in table_names
        assert "balance_ledger" in table_names
        assert "bot_status" in table_names
    finally:
        config.DB_PATH = original
        os.unlink(tmp.name)


def test_migrate_db():
    """Migration eksik kolon eklememeli (temiz DB'de)."""
    original = config.DB_PATH
    tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
    config.DB_PATH = tmp.name
    tmp.close()

    try:
        database.init_db()
        added = database.migrate_db()
        assert isinstance(added, list)
    finally:
        config.DB_PATH = original
        os.unlink(tmp.name)


def test_save_signal_candidate():
    """Signal candidate kaydedilmeli."""
    original = config.DB_PATH
    tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
    config.DB_PATH = tmp.name
    tmp.close()

    try:
        database.init_db()
        sig = SignalData(
            symbol="BTCUSDT", side="LONG",
            entry_price=50000.0, stop_loss=49000.0,
            tp1=52000.0, score=45.0,
        )
        result = database.save_signal_candidate(sig, "ALLOW", "test")
        assert result is not None

        signals = database.get_recent_signals(10)
        assert len(signals) >= 1
        assert signals[0]["symbol"] == "BTCUSDT"
    finally:
        config.DB_PATH = original
        os.unlink(tmp.name)


def test_crud_trade():
    """Trade CRUD çalışmalı."""
    original = config.DB_PATH
    tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
    config.DB_PATH = tmp.name
    tmp.close()

    try:
        database.init_db()

        trade = TradeData(
            symbol="TESTUSDT", side="LONG",
            entry_price=100.0, stop_loss=95.0,
            tp1=110.0, quantity=1.0, leverage=5,
        )
        tid = database.create_trade(trade)
        assert tid is not None

        opens = database.get_open_trades()
        assert len(opens) >= 1

        database.update_trade_price(tid, 105.0, 5.0)
        database.close_trade(tid, 110.0, 10.0, "TEST_TP")

        recent = database.get_recent_trades(10)
        assert len(recent) >= 1
        assert recent[0]["status"] == "CLOSED"
    finally:
        config.DB_PATH = original
        os.unlink(tmp.name)


def test_bot_status():
    """Bot status upsert çalışmalı."""
    original = config.DB_PATH
    tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
    config.DB_PATH = tmp.name
    tmp.close()

    try:
        database.init_db()
        database.update_bot_status("test_key", "test_value")

        status = database.get_bot_status()
        assert "test_key" in status
        assert status["test_key"]["value"] == "test_value"

        # Key parametresiyle de çalışmalı
        single = database.get_bot_status("test_key")
        assert single["value"] == "test_value"
    finally:
        config.DB_PATH = original
        os.unlink(tmp.name)


if __name__ == "__main__":
    tests = [
        test_init_db, test_migrate_db,
        test_save_signal_candidate, test_crud_trade,
        test_bot_status,
    ]
    passed = 0
    failed = 0
    for t in tests:
        try:
            t()
            print(f"  ✓ {t.__name__}")
            passed += 1
        except Exception as exc:
            print(f"  ✗ {t.__name__}: {exc}")
            failed += 1
    print(f"\n{passed} passed, {failed} failed")
    sys.exit(1 if failed else 0)
