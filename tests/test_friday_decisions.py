"""
tests/test_friday_decisions.py — Faz 2 kabul testleri.

2.1: Karar kaydı, outcome doldurma (24h/72h + clamp), context'e giriş,
     _execute_decisions günlük entegrasyonu.
2.2: Function calling → karar dönüşümü (bilinmeyen key reddi, max 3 limit, NOOP).
2.3: Drawdown eskalasyonu (pause + PAUSE kaydı).
2.4: Sabah brifingi şablonu.
"""

import json
from datetime import datetime, timedelta, timezone

import pytest


def _utcnow():
    return datetime.now(timezone.utc)


def _insert_closed_trade(db, close_dt, net_pnl, risk_usd=10.0, environment="paper"):
    """Belirli kapanış zamanlı, R'si hesaplanabilir kapanmış trade ekler."""
    with db.get_conn() as conn:
        conn.execute(
            """
            INSERT INTO trades (symbol, direction, status, entry, net_pnl, risk_usd,
                                r_multiple, close_time, open_time, environment, is_valid_for_stats)
            VALUES ('TESTUSDT', 'LONG', 'closed', 100.0, ?, ?, 0, ?, ?, ?, 1)
            """,
            (net_pnl, risk_usd, close_dt.isoformat(),
             (close_dt - timedelta(minutes=30)).isoformat(), environment),
        )


# ══════════════════════════════════════════════════════════════════════════════
# 2.1 — Karar kaydı + outcome doldurma + context
# ══════════════════════════════════════════════════════════════════════════════

def test_log_decision_writes_row(test_db):
    from core import friday_decisions as fd

    decision_id = fd.log_decision(
        "SET_PARAM", param_key="trade_threshold", old_value="55", new_value="53",
        reasoning="x" * 600,  # 500 char'a kırpılmalı
        ctx_snapshot={"balance": 1000.0, "regime": "NEUTRAL"},
    )
    assert decision_id is not None

    with test_db.get_conn() as conn:
        row = conn.execute("SELECT * FROM friday_decisions WHERE id=?", (decision_id,)).fetchone()
    assert row["decision_type"] == "SET_PARAM"
    assert row["param_key"] == "trade_threshold"
    assert row["old_value"] == "55" and row["new_value"] == "53"
    assert len(row["reasoning"]) == 500  # plan 2.1: ilk 500 karakter
    snapshot = json.loads(row["ctx_snapshot"])
    assert snapshot["balance"] == 1000.0
    assert row["outcome_24h"] is None and row["outcome_score"] is None


def test_fill_pending_outcomes_24h(test_db):
    from core import friday_decisions as fd
    now = _utcnow()

    # 25 saat önce alınmış karar (24h penceresi dolmuş, 72h dolmamış)
    created = now - timedelta(hours=25)
    with test_db.get_conn() as conn:
        conn.execute(
            "INSERT INTO friday_decisions (created_at, decision_type) VALUES (?, 'SET_PARAM')",
            (created.isoformat(),),
        )

    # Karar SONRASI 24h penceresinde 2 trade: +20 ve -10 (risk 10 → R: +2, -1)
    _insert_closed_trade(test_db, created + timedelta(hours=2), +20.0)
    _insert_closed_trade(test_db, created + timedelta(hours=5), -10.0)
    # Karar ÖNCESİ 24h penceresinde 1 kayıp trade
    _insert_closed_trade(test_db, created - timedelta(hours=3), -10.0)

    filled = fd.fill_pending_outcomes(now=now)
    assert filled == 1  # yalnız 24h dolduruldu

    with test_db.get_conn() as conn:
        row = conn.execute("SELECT outcome_24h, outcome_72h, outcome_score FROM friday_decisions").fetchone()
    outcome = json.loads(row["outcome_24h"])
    assert outcome["pnl_delta"] == 10.0          # +20 - 10
    assert outcome["trades_opened"] == 2          # karar sonrası açılanlar
    assert outcome["n_post"] == 2 and outcome["n_pre"] == 1
    # wr_delta = post 0.5 − pre 0.0 = 0.5
    assert outcome["wr_delta"] == pytest.approx(0.5)
    assert row["outcome_72h"] is None and row["outcome_score"] is None


def test_fill_pending_outcomes_72h_score_clamped(test_db):
    from core import friday_decisions as fd
    now = _utcnow()

    created = now - timedelta(hours=73)
    with test_db.get_conn() as conn:
        conn.execute(
            "INSERT INTO friday_decisions (created_at, decision_type) VALUES (?, 'SET_PARAM')",
            (created.isoformat(),),
        )

    # Öncesi: 2 kayıp (R=-1, E=-1.0) | Sonrası: 2 büyük kazanç (R=+2, E=+2.0)
    _insert_closed_trade(test_db, created - timedelta(hours=10), -10.0)
    _insert_closed_trade(test_db, created - timedelta(hours=20), -10.0)
    _insert_closed_trade(test_db, created + timedelta(hours=10), +20.0)
    _insert_closed_trade(test_db, created + timedelta(hours=20), +20.0)

    filled = fd.fill_pending_outcomes(now=now)
    assert filled == 2  # hem 24h hem 72h dolduruldu

    with test_db.get_conn() as conn:
        row = conn.execute("SELECT outcome_72h, outcome_score FROM friday_decisions").fetchone()
    outcome72 = json.loads(row["outcome_72h"])
    assert outcome72["expectancy_pre"] == pytest.approx(-1.0)
    assert outcome72["expectancy_post"] == pytest.approx(2.0)
    # Ham delta = 3.0 → [-1, +1] aralığına CLAMP edilmeli (plan 2.1 formülü)
    assert row["outcome_score"] == 1.0


def test_recent_decisions_in_system_context(test_db):
    from core import friday_decisions as fd
    from core.friday_ceo import FridayCeo

    fd.log_decision("SET_PARAM", param_key="trade_threshold", old_value="55", new_value="50")
    fd.log_decision("PAUSE", param_key="confirmation_mode", old_value="false", new_value="true")

    ctx = FridayCeo().get_system_context()
    assert "recent_decisions" in ctx
    types = [d["type"] for d in ctx["recent_decisions"]]
    assert "SET_PARAM" in types and "PAUSE" in types
    # Değişim özeti kompakt formatta olmalı
    changes = [d["change"] for d in ctx["recent_decisions"] if d["change"]]
    assert any("trade_threshold" in c for c in changes)


def test_execute_decisions_logs_set_param(test_db, monkeypatch):
    from core.friday_ceo import FridayCeo
    import telegram_delivery

    monkeypatch.setattr(telegram_delivery, "send_message", lambda *a, **k: True)
    ceo = FridayCeo()
    msgs = ceo._execute_decisions(
        {"parameters": {"TRADE_THRESHOLD": "50"}, "actions": []},
        reasoning="test gerekçesi",
    )
    assert any("TRADE_THRESHOLD" in m for m in msgs)

    with test_db.get_conn() as conn:
        row = conn.execute(
            "SELECT * FROM friday_decisions WHERE decision_type='SET_PARAM' ORDER BY id DESC LIMIT 1"
        ).fetchone()
    assert row is not None
    assert row["param_key"] == "trade_threshold"
    assert float(row["new_value"]) == 50.0
    assert "test gerekçesi" in (row["reasoning"] or "")


# ══════════════════════════════════════════════════════════════════════════════
# 2.2 — Function calling → karar dönüşümü
# ══════════════════════════════════════════════════════════════════════════════

def test_tool_calls_unknown_param_rejected(test_db):
    from core.friday_ceo import FridayCeo

    ceo = FridayCeo()
    decisions = ceo._tool_calls_to_decisions([
        {"name": "set_param", "args": {"key": "evil_unknown_key", "value": "1", "reason": "halüsinasyon"}},
        {"name": "set_param", "args": {"key": "trade_threshold", "value": "52", "reason": "geçerli"}},
    ])
    # Bilinmeyen key REDDEDİLDİ, bilinen key kabul edildi
    assert "evil_unknown_key" not in decisions["parameters"]
    assert decisions["parameters"].get("trade_threshold") == "52"


def test_tool_calls_max_three_limit(test_db):
    from core.friday_ceo import FridayCeo

    ceo = FridayCeo()
    calls = [
        {"name": "set_param", "args": {"key": "trade_threshold", "value": "50", "reason": "r1"}},
        {"name": "pause_trading", "args": {"reason": "r2"}},
        {"name": "send_report", "args": {"text": "rapor"}},
        # 4. çağrı LİMİT dışı — düşürülmeli (plan 2.2: tek turda max 3)
        {"name": "set_coin_cooldown", "args": {"symbol": "SOLUSDT", "minutes": 60, "reason": "r4"}},
    ]
    decisions = ceo._tool_calls_to_decisions(calls)
    applied_count = (
        len(decisions["parameters"]) + len(decisions["actions"])
        + len(decisions["reports"]) + len(decisions["cooldowns"]) + len(decisions["restarts"])
    )
    assert applied_count == 3
    assert decisions["cooldowns"] == []  # 4. çağrı düşürüldü


def test_tool_calls_noop_logged(test_db, monkeypatch):
    from core.friday_ceo import FridayCeo
    import telegram_delivery

    monkeypatch.setattr(telegram_delivery, "send_message", lambda *a, **k: True)
    ceo = FridayCeo()
    decisions = ceo._tool_calls_to_decisions([
        {"name": "no_action", "args": {"reason": "her şey yolunda"}},
    ])
    ceo._execute_decisions(decisions)

    with test_db.get_conn() as conn:
        row = conn.execute(
            "SELECT * FROM friday_decisions WHERE decision_type='NOOP' ORDER BY id DESC LIMIT 1"
        ).fetchone()
    assert row is not None
    assert "her şey yolunda" in (row["reasoning"] or "")


def test_tool_calls_restart_only_logged(test_db, monkeypatch):
    """request_restart YALNIZCA günlüğe yazılır — docker restart çağrılmaz (plan 2.3)."""
    from core.friday_ceo import FridayCeo
    import telegram_delivery

    monkeypatch.setattr(telegram_delivery, "send_message", lambda *a, **k: True)
    ceo = FridayCeo()
    decisions = ceo._tool_calls_to_decisions([
        {"name": "request_restart", "args": {"service": "engine", "reason": "ws kopuk"}},
    ])
    msgs = ceo._execute_decisions(decisions)
    assert any("Restart Talebi Kaydedildi" in m for m in msgs)

    with test_db.get_conn() as conn:
        row = conn.execute(
            "SELECT * FROM friday_decisions WHERE decision_type='RESTART' ORDER BY id DESC LIMIT 1"
        ).fetchone()
    assert row is not None and row["param_key"] == "engine"


# ══════════════════════════════════════════════════════════════════════════════
# 2.3 — Otonom SysAdmin: drawdown eskalasyonu
# ══════════════════════════════════════════════════════════════════════════════

def test_drawdown_escalation_pauses_and_logs(test_db, monkeypatch):
    from core.friday_ceo import FridayCeo
    import telegram_delivery

    sent = []
    monkeypatch.setattr(telegram_delivery, "send_message", lambda msg, *a, **k: sent.append(msg) or True)

    # Bugün -100$ kayıp, bakiye 1000$ → gün başı 1100$ → -%9.1 ≤ -%5 eşik
    now = _utcnow()
    _insert_closed_trade(test_db, now - timedelta(hours=1), -100.0, risk_usd=50.0)
    with test_db.get_conn() as conn:
        conn.execute("INSERT OR REPLACE INTO paper_account (id, balance) VALUES (1, 1000.0)")

    ceo = FridayCeo()
    ceo._run_sysadmin_checks()

    # PAUSE: onay modu açıldı
    assert test_db.get_system_state("confirmation_mode") == "true"
    # Telegram uyarısı gitti
    assert any("Drawdown" in m for m in sent)
    # Karar günlüğüne PAUSE yazıldı
    with test_db.get_conn() as conn:
        row = conn.execute(
            "SELECT * FROM friday_decisions WHERE decision_type='PAUSE' ORDER BY id DESC LIMIT 1"
        ).fetchone()
    assert row is not None
    assert "drawdown" in (row["reasoning"] or "").lower()

    # İkinci çağrı AYNI GÜN tekrar tetiklememeli (günde 1 kez kuralı)
    sent.clear()
    ceo._run_sysadmin_checks()
    assert not any("Drawdown" in m for m in sent)


# ══════════════════════════════════════════════════════════════════════════════
# 2.4 — Sabah brifingi
# ══════════════════════════════════════════════════════════════════════════════

def test_morning_briefing_template(test_db):
    from core.friday_ceo import FridayCeo
    from core import friday_decisions as fd

    now = _utcnow()
    # Dün kapanan 1 kazançlı trade + bakiye + bir karar kaydı
    _insert_closed_trade(test_db, now - timedelta(days=1), +25.0, risk_usd=10.0)
    with test_db.get_conn() as conn:
        conn.execute("INSERT OR REPLACE INTO paper_account (id, balance) VALUES (1, 2184.20)")
    fd.log_decision("SET_PARAM", param_key="trade_threshold", old_value="55", new_value="53")

    brief = FridayCeo().generate_morning_briefing()

    assert "GÜNAYDIN BOSS" in brief
    assert "Dün: 1 işlem" in brief
    assert "1W-0L" in brief
    assert "2,184.20" in brief
    assert "🤖 Kararlarım (24h):" in brief
    assert "trade_threshold 55→53" in brief
    assert "👻 Ghost:" in brief
    assert "🎯 Bugünkü plan:" in brief
