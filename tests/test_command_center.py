"""
tests/test_command_center.py — Faz 4 Komuta Merkezi servis sözleşmesi.

dashboard_service.get_command_center()'in 4 katmanın da beklenen anahtarları
döndürdüğünü ve alt-bileşenlerin (pulse, funnel rejects, açık pozisyon) doğru
hesaplandığını doğrular. UI render'ı bu sözleşmeye bağlı.
"""
from datetime import datetime, timezone, timedelta

import pytest

import dashboard_service


def _seed_closed(db, specs, environment="paper", regime="TRENDING_HIGH_VOL"):
    now = datetime.now(timezone.utc)
    with db.get_conn() as conn:
        for i, (pnl, risk) in enumerate(specs):
            ct = (now - timedelta(hours=i + 1)).isoformat()
            conn.execute(
                "INSERT INTO trades (symbol,direction,status,entry,sl,net_pnl,risk_usd,"
                "r_multiple,close_time,open_time,environment,market_regime,is_valid_for_stats) "
                "VALUES ('SOLUSDT','LONG','closed',100,99,?,?,0,?,?,?,?,1)",
                (pnl, risk, ct, ct, environment, regime),
            )


def test_command_center_contract(test_db):
    cc = dashboard_service.get_command_center("paper")
    # 4 katmanın tüm üst anahtarları
    for key in ("pulse", "expectancy", "sparkline", "wallet", "funnel",
                "open_trades", "friday", "ghost", "regime", "readiness"):
        assert key in cc, f"eksik anahtar: {key}"
    # readiness 5 kapı
    assert len(cc["readiness"]["gates"]) == 5
    # pulse bileşenleri
    assert set(cc["pulse"]["components"].keys()) >= {"db", "engine", "redis", "telegram", "websocket"}


def test_pulse_status_levels(test_db, monkeypatch):
    # heartbeat taze değil → engine down → SORUN
    pulse = dashboard_service.get_system_pulse()
    assert pulse["status"] in ("down", "degrade", "ok")
    assert pulse["components"]["db"] == "ok"  # test DB bağlı

    # Taze heartbeat → engine ok
    test_db.update_bot_status("heartbeat", datetime.now(timezone.utc).isoformat())
    test_db.update_bot_status("ws_heartbeat", datetime.now(timezone.utc).isoformat())
    pulse2 = dashboard_service.get_system_pulse()
    assert pulse2["components"]["engine"] == "ok"
    assert pulse2["components"]["websocket"] == "ok"


def test_funnel_top3_rejects(test_db):
    now = datetime.now(timezone.utc)
    with test_db.get_conn() as conn:
        for reason in ["weak_trend", "weak_trend", "weak_trend", "low_vol", "low_vol", "spread", "x4"]:
            conn.execute(
                "INSERT INTO signal_events (signal_id,stage,symbol,reject_reason,created_at) "
                "VALUES ('s','REJECTED','X',?,?)",
                (reason, now.isoformat()),
            )
    funnel = dashboard_service.get_funnel_with_rejects(24)
    rejects = funnel["rejects"]
    assert len(rejects) == 3  # yalnız top 3
    assert rejects[0]["reason"] == "weak_trend" and rejects[0]["count"] == 3


def test_expectancy_and_sparkline_populate(test_db):
    _seed_closed(test_db, [(20, 10), (20, 10), (-10, 10), (30, 10), (-10, 10)])
    now = datetime.now(timezone.utc)
    # bugünün özetini yaz (sparkline kaynağı)
    test_db.write_daily_summary(now.strftime("%Y-%m-%d"), "paper")
    cc = dashboard_service.get_command_center("paper")
    assert cc["expectancy"]["n"] == 5
    assert cc["expectancy"]["expectancy_r"] == pytest.approx(1.0)
    assert len(cc["sparkline"]) >= 1


def test_open_trade_in_command_center(test_db):
    with test_db.get_conn() as conn:
        conn.execute(
            "INSERT INTO trades (symbol,direction,status,entry,sl,tp1,current_price,leverage,"
            "unrealized_pnl,qty,remaining_qty,environment,tp1_hit) "
            "VALUES ('BTCUSDT','LONG','open',60000,59000,61000,60500,10,12.5,0.1,0.1,'paper',0)"
        )
    cc = dashboard_service.get_command_center("paper")
    assert cc["wallet"]["open_count"] == 1
    assert any(t["symbol"] == "BTCUSDT" for t in cc["open_trades"])
