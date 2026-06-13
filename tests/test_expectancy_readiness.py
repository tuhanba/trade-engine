"""
tests/test_expectancy_readiness.py — Faz 3.1 (expectancy + daily_summary)
ve Faz 3.3 (live-readiness) testleri.
"""
from datetime import datetime, timezone, timedelta

import pytest


def _seed(db, specs, environment="paper", days_ago=1):
    """specs: [(net_pnl, risk_usd), ...] kapanmış trade üretir."""
    now = datetime.now(timezone.utc)
    with db.get_conn() as conn:
        for i, (pnl, risk) in enumerate(specs):
            ct = (now - timedelta(days=days_ago, hours=i % 12)).isoformat()
            conn.execute(
                "INSERT INTO trades (symbol,direction,status,entry,sl,net_pnl,risk_usd,"
                "r_multiple,close_time,open_time,environment,is_valid_for_stats) "
                "VALUES ('BTCUSDT','LONG','closed',100,99,?,?,0,?,?,?,1)",
                (pnl, risk, ct, ct, environment),
            )


# ── Faz 3.1: calculate_expectancy ─────────────────────────────────────────────

def test_expectancy_formula(test_db):
    from core.accounting import calculate_expectancy
    # 3 kazanç (+2R), 2 kayıp (-1R): WR=0.6, E = 0.6*2 - 0.4*1 = 0.8
    _seed(test_db, [(20, 10), (20, 10), (20, 10), (-10, 10), (-10, 10)])
    exp = calculate_expectancy(days=30, environment="paper")
    assert exp["n"] == 5
    assert exp["win_rate"] == pytest.approx(0.6)
    assert exp["avg_win_r"] == pytest.approx(2.0)
    assert exp["avg_loss_r"] == pytest.approx(1.0)
    assert exp["expectancy_r"] == pytest.approx(0.8)


def test_expectancy_empty(test_db):
    from core.accounting import calculate_expectancy
    exp = calculate_expectancy(days=30, environment="paper")
    assert exp["n"] == 0
    assert exp["expectancy_r"] == 0.0


def test_expectancy_excludes_old_and_invalid(test_db):
    from core.accounting import calculate_expectancy
    # 30 günden eski trade sayılmamalı
    _seed(test_db, [(20, 10)], days_ago=40)
    _seed(test_db, [(20, 10), (-10, 10)], days_ago=1)
    exp = calculate_expectancy(days=30, environment="paper")
    assert exp["n"] == 2


def test_daily_summary_write_and_read(test_db):
    now = datetime.now(timezone.utc)
    yday = (now - timedelta(days=1)).strftime("%Y-%m-%d")
    _seed(test_db, [(20, 10), (20, 10), (-10, 10)], days_ago=1)
    summary = test_db.write_daily_summary(yday, "paper")
    assert summary["trade_count"] == 3
    assert summary["win_count"] == 2
    assert summary["loss_count"] == 1
    # E = (2/3)*2 - (1/3)*1 = 1.333 - 0.333 = 1.0
    assert summary["expectancy_r"] == pytest.approx(1.0, abs=0.01)

    rows = test_db.get_daily_summaries(30, "paper")
    assert len(rows) == 1
    assert rows[0]["date"] == yday


def test_daily_summary_idempotent(test_db):
    now = datetime.now(timezone.utc)
    yday = (now - timedelta(days=1)).strftime("%Y-%m-%d")
    _seed(test_db, [(20, 10)], days_ago=1)
    test_db.write_daily_summary(yday, "paper")
    test_db.write_daily_summary(yday, "paper")  # ikinci yazım çift kayıt YARATMAMALI
    rows = test_db.get_daily_summaries(30, "paper")
    assert len(rows) == 1


# ── Faz 3.3: live_readiness ───────────────────────────────────────────────────

def test_readiness_all_gates_fail_empty(test_db):
    from core import live_readiness
    result = live_readiness.check()
    assert result["ready"] is False
    gate_names = {g["name"] for g in result["gates"]}
    assert gate_names == {"min_trades", "positive_expectancy", "drawdown", "uptime", "no_critical_bugs"}
    # Boş sistemde min_trades ve expectancy kapıları kırmızı
    by_name = {g["name"]: g for g in result["gates"]}
    assert by_name["min_trades"]["passed"] is False
    assert by_name["positive_expectancy"]["passed"] is False


def test_readiness_min_trades_gate(test_db):
    from core import live_readiness
    # 120 kazançlı işlem → min_trades + expectancy yeşil
    _seed(test_db, [(20, 10)] * 120, days_ago=1)
    result = live_readiness.check()
    by_name = {g["name"]: g for g in result["gates"]}
    assert by_name["min_trades"]["passed"] is True
    assert by_name["positive_expectancy"]["passed"] is True


def test_readiness_manual_ok_gate(test_db):
    from core import live_readiness
    by_name = {g["name"]: g for g in live_readiness.check()["gates"]}
    assert by_name["no_critical_bugs"]["passed"] is False
    # Manuel onay state key set edilince yeşile döner
    test_db.set_state("live_readiness_manual_ok", "true")
    by_name = {g["name"]: g for g in live_readiness.check()["gates"]}
    assert by_name["no_critical_bugs"]["passed"] is True


def test_readiness_drawdown_gate(test_db):
    from core import live_readiness
    now = datetime.now(timezone.utc)
    # balance_ledger: 1000 → 900 (%10 drawdown > %8 eşiği → kırmızı)
    with test_db.get_conn() as conn:
        for i, bal in enumerate([1000, 1050, 950, 900]):
            ct = (now - timedelta(days=2, hours=i)).isoformat()
            conn.execute(
                "INSERT INTO balance_ledger (balance_after, created_at) VALUES (?, ?)",
                (bal, ct),
            )
    by_name = {g["name"]: g for g in live_readiness.check()["gates"]}
    # peak 1050 → trough 900 = %14.3 drawdown
    assert by_name["drawdown"]["passed"] is False
    assert by_name["drawdown"]["value"] > 8.0
