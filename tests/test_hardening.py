"""
tests/test_hardening.py — Sertleştirme testleri:
  - Live-readiness Kapı 4: gerçek heartbeat boşluk analizi (proxy yerine)
  - weekly_summary canlandırma (Plan 1.3 son ucu)
"""
from datetime import datetime, timezone, timedelta

import pytest

import core.live_readiness as lr


def _seed_heartbeats(db, timestamps):
    with db.get_conn() as conn:
        for ts in timestamps:
            conn.execute("INSERT INTO heartbeat_history (ts) VALUES (?)", (ts.isoformat(),))


# ── Heartbeat tablosu + kayıt ─────────────────────────────────────────────────

def test_heartbeat_table_and_record(test_db):
    test_db.record_heartbeat_sample()
    samples = test_db.get_heartbeat_samples(30)
    assert len(samples) == 1


def test_heartbeat_prune(test_db):
    # 40 gün eski örnek budanmalı (prune_days=35)
    old = (datetime.now(timezone.utc) - timedelta(days=40)).isoformat()
    with test_db.get_conn() as conn:
        conn.execute("INSERT INTO heartbeat_history (ts) VALUES (?)", (old,))
    test_db.record_heartbeat_sample()  # prune tetikler
    samples = test_db.get_heartbeat_samples(60)
    assert all(datetime.fromisoformat(s) > datetime.now(timezone.utc) - timedelta(days=36) for s in samples)


# ── Uptime boşluk analizi ─────────────────────────────────────────────────────

def test_uptime_from_history_perfect(test_db):
    # Son 2 saat, 5 dk arayla kesintisiz örnek → ~%100 uptime
    now = datetime.now(timezone.utc)
    ts = [now - timedelta(minutes=5 * i) for i in range(24)][::-1]
    _seed_heartbeats(test_db, ts)
    result = lr._uptime_from_history(30)
    assert result is not None
    uptime, detail = result
    assert uptime >= 99.0
    assert "boşluk analizi" in detail


def test_uptime_from_history_with_gap(test_db):
    # 10 saatlik pencere, ortada 5 saatlik boşluk (downtime) → ~%50
    now = datetime.now(timezone.utc)
    first_half = [now - timedelta(hours=10) + timedelta(minutes=5 * i) for i in range(24)]  # ilk ~2h
    # 5 saat boşluk
    second_half = [now - timedelta(hours=3) + timedelta(minutes=5 * i) for i in range(37)]  # son ~3h
    _seed_heartbeats(test_db, first_half + second_half)
    uptime, _ = lr._uptime_from_history(30)
    # 10h pencerede ~5h downtime → uptime belirgin şekilde <99
    assert uptime < 90.0


def test_uptime_falls_back_to_proxy_without_history(test_db):
    # Geçmiş yoksa None değil; _uptime_pct proxy'ye düşer
    assert lr._uptime_from_history(30) is None
    uptime, detail = lr._uptime_pct(30)
    assert "proxy" in detail


def test_uptime_pct_uses_history_when_available(test_db):
    now = datetime.now(timezone.utc)
    ts = [now - timedelta(minutes=5 * i) for i in range(24)][::-1]
    _seed_heartbeats(test_db, ts)
    uptime, detail = lr._uptime_pct(30)
    assert "boşluk analizi" in detail


# ── weekly_summary canlandırma ────────────────────────────────────────────────

def test_weekly_summary_from_daily(test_db):
    # Bu haftanın Pazartesi'si
    now = datetime.now(timezone.utc)
    monday = now - timedelta(days=now.weekday())
    week_start = monday.strftime("%Y-%m-%d")
    # Hafta içine 2 günlük özet yaz
    with test_db.get_conn() as conn:
        for off, (tc, wc, lc, pnl, r) in enumerate([(3, 2, 1, 30.0, 0.5), (2, 1, 1, -10.0, -0.2)]):
            d = (monday + timedelta(days=off)).strftime("%Y-%m-%d")
            conn.execute(
                "INSERT INTO daily_summary (date, trade_count, win_count, loss_count, net_pnl, avg_r, environment) "
                "VALUES (?,?,?,?,?,?,?)",
                (d, tc, wc, lc, pnl, r, "paper"),
            )
    s = test_db.write_weekly_summary(week_start, "paper")
    assert s["trade_count"] == 5
    assert s["win_count"] == 3 and s["loss_count"] == 2
    assert s["net_pnl"] == pytest.approx(20.0)
    # En iyi gün = +30 olan, en kötü = -10 olan
    assert s["best_day"] == (monday + timedelta(days=0)).strftime("%Y-%m-%d")
    assert s["worst_day"] == (monday + timedelta(days=1)).strftime("%Y-%m-%d")

    rows = test_db.get_weekly_summaries(12)
    assert len(rows) == 1 and rows[0]["week_start"] == week_start


def test_weekly_summary_idempotent(test_db):
    now = datetime.now(timezone.utc)
    monday = now - timedelta(days=now.weekday())
    ws = monday.strftime("%Y-%m-%d")
    test_db.write_weekly_summary(ws, "paper")
    test_db.write_weekly_summary(ws, "paper")  # çift kayıt olmamalı
    assert len(test_db.get_weekly_summaries(12)) == 1
