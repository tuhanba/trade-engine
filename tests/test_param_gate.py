"""
tests/test_param_gate.py — Faz 3.2 kabul testleri.

Plan kabul kriteri: onay (approve), red (reject) ve veri-yetersiz
(insufficient data → küçük adım) yollarını test eder. Ayrıca gate kapsamı
dışı key'in serbest geçtiği ve manuel /set'in bypass ettiği doğrulanır.
"""
from datetime import datetime, timezone, timedelta

import pytest

from core import param_gate


def _seed_trades(db, n, win_pnl, loss_pnl, win_ratio=0.6, score=60.0,
                 sl_mult_pct=0.015, mae=0.0, mfe=0.05, close_reason="TP1",
                 environment="paper"):
    """n adet kapanmış trade üretir. win_ratio oranında kazanç, kalanı kayıp."""
    now = datetime.now(timezone.utc)
    with db.get_conn() as conn:
        for i in range(n):
            is_win = (i % 10) < int(win_ratio * 10)
            pnl = win_pnl if is_win else loss_pnl
            risk = 10.0
            r_mult = pnl / risk
            entry = 100.0
            sl = entry * (1 - sl_mult_pct)  # LONG
            ct = (now - timedelta(days=1, hours=i % 20)).isoformat()
            conn.execute(
                """
                INSERT INTO trades (symbol, direction, status, entry, sl, tp1, tp2, tp3,
                                    leverage, net_pnl, realized_pnl, risk_usd, r_multiple,
                                    final_score, mae, mfe, close_reason, metadata,
                                    close_time, open_time, environment, is_valid_for_stats)
                VALUES ('BTCUSDT','LONG','closed',?,?,?,?,?,10,?,?,?,?,?,?,?,?, '{}', ?, ?, ?, 1)
                """,
                (entry, sl, entry * 1.02, entry * 1.04, entry * 1.06,
                 pnl, pnl, risk, r_mult, score, mae, (mfe if is_win else 0.0),
                 (close_reason if is_win else "SL"), ct, ct, environment),
            )


# ── Gate kapsamı dışı ─────────────────────────────────────────────────────────

def test_non_gated_key_passes_freely(test_db):
    approved, report = param_gate.validate_param_change("max_open_trades", 5, 6)
    assert approved is True
    assert report["gated"] is False


def test_no_change_passes(test_db):
    approved, report = param_gate.validate_param_change("trade_threshold", 55.0, 55.0)
    assert approved is True
    assert "değişiklik yok" in report["reason"]


# ── Veri yetersiz yolu ────────────────────────────────────────────────────────

def test_insufficient_data_small_step(test_db):
    # <50 örnek → küçük adım kuralı
    _seed_trades(test_db, 10, win_pnl=20.0, loss_pnl=-10.0)
    approved, report = param_gate.validate_param_change("trade_threshold", 55.0, 70.0)
    assert approved is True
    assert report.get("insufficient_data") is True
    # 55 → max %2 adım = 55*0.02 = 1.1 → applied 56.1
    assert report["applied_value"] == pytest.approx(56.1, abs=0.01)
    assert report["n_samples"] == 10


def test_insufficient_data_small_change_unchanged(test_db):
    _seed_trades(test_db, 5, win_pnl=20.0, loss_pnl=-10.0)
    # Değişim zaten %2'den küçük → değer korunur
    approved, report = param_gate.validate_param_change("trade_threshold", 55.0, 55.5)
    assert approved is True
    assert report["applied_value"] == pytest.approx(55.5, abs=0.01)


# ── Onay yolu ─────────────────────────────────────────────────────────────────

def test_approve_when_new_config_not_worse(test_db):
    """trade_threshold yükseltmek düşük skorlu (kayıp) işlemleri eler → E iyileşir/korunur."""
    # 60 işlem: skoru 60 olan kazançlar + skoru 45 olan kayıplar
    now = datetime.now(timezone.utc)
    with test_db.get_conn() as conn:
        for i in range(60):
            if i % 2 == 0:
                score, pnl = 65.0, 20.0   # yüksek skor, kazanç
            else:
                score, pnl = 48.0, -10.0  # düşük skor, kayıp
            ct = (now - timedelta(days=1, hours=i % 20)).isoformat()
            conn.execute(
                "INSERT INTO trades (symbol,direction,status,entry,sl,tp1,tp2,tp3,leverage,"
                "net_pnl,realized_pnl,risk_usd,r_multiple,final_score,mae,mfe,close_reason,"
                "metadata,close_time,open_time,environment,is_valid_for_stats) "
                "VALUES ('BTCUSDT','LONG','closed',100,98.5,102,104,106,10,?,?,10,?,?,0,0.05,?, '{}',?,?,?,1)",
                (pnl, pnl, pnl / 10.0, score, "TP1" if pnl > 0 else "SL", ct, ct, "paper"),
            )
    # Eşiği 50'ye çekmek skoru 48 olan kayıpları eler → yeni E ≥ eski*0.95
    approved, report = param_gate.validate_param_change("trade_threshold", 45.0, 50.0)
    assert report["gated"] is True
    assert "old_expectancy_r" in report and "new_expectancy_r" in report
    assert approved is True
    assert report["new_expectancy_r"] >= report["old_expectancy_r"] * 0.95


# ── Red yolu ──────────────────────────────────────────────────────────────────

def test_reject_when_new_config_worse(test_db):
    """Eşiği düşürmek (kötü işlemleri içeri almak) E'yi düşürür → RED."""
    now = datetime.now(timezone.utc)
    with test_db.get_conn() as conn:
        for i in range(60):
            if i % 2 == 0:
                score, pnl = 65.0, 20.0
            else:
                score, pnl = 48.0, -15.0
            ct = (now - timedelta(days=1, hours=i % 20)).isoformat()
            conn.execute(
                "INSERT INTO trades (symbol,direction,status,entry,sl,tp1,tp2,tp3,leverage,"
                "net_pnl,realized_pnl,risk_usd,r_multiple,final_score,mae,mfe,close_reason,"
                "metadata,close_time,open_time,environment,is_valid_for_stats) "
                "VALUES ('BTCUSDT','LONG','closed',100,98.5,102,104,106,10,?,?,10,?,?,0,0.05,?, '{}',?,?,?,1)",
                (pnl, pnl, pnl / 10.0, score, "TP1" if pnl > 0 else "SL", ct, ct, "paper"),
            )
    # Başlangıçta eşik 55 (sadece skoru 65 olan kazançlar geçer → E pozitif).
    # Eşiği 45'e düşürünce skoru 48 olan kayıplar da girer → E düşer → RED.
    approved, report = param_gate.validate_param_change("trade_threshold", 55.0, 45.0)
    assert report["gated"] is True
    assert approved is False
    assert "reddedildi" in report["reason"].lower()
    assert report["new_expectancy_r"] < report["old_expectancy_r"]


# ── Friday entegrasyonu: gate=True reddi uygulamayı engeller ───────────────────

def test_friday_apply_param_gate_rejection_keeps_old(test_db, monkeypatch):
    from core.friday_ceo import FridayCeo

    # Eski değeri DB'ye koy
    test_db.set_state("trade_threshold", "55.0")

    # Gate'i RED dönecek şekilde monkeypatch'le (deterministik)
    monkeypatch.setattr(
        "core.param_gate.validate_param_change",
        lambda key, old, new, **kw: (False, {"reason": "test red", "gated": True}),
    )
    ceo = FridayCeo()
    result = ceo._apply_param_with_clamp("trade_threshold", 45.0, actor="friday",
                                         reason="llm", gate=True)
    # Reddedildi → eski değer korunur, uygulanmaz
    assert result == pytest.approx(55.0)
    assert test_db.get_state("trade_threshold") == "55.0"


def test_friday_apply_param_no_gate_applies(test_db, monkeypatch):
    """gate=False (acil koruma yolu) → simülasyon olmadan doğrudan uygulanır."""
    from core.friday_ceo import FridayCeo
    test_db.set_state("trade_threshold", "55.0")
    ceo = FridayCeo()
    result = ceo._apply_param_with_clamp("trade_threshold", 50.0, actor="friday",
                                         reason="choppy_protection", gate=False)
    assert result == pytest.approx(50.0)
    assert test_db.get_state("trade_threshold") == "50.0"
