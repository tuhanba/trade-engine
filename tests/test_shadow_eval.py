"""
tests/test_shadow_eval.py — Faz 6.4 Shadow-mode parametre A/B testleri.
"""
from datetime import datetime, timezone, timedelta
from unittest.mock import patch

import pytest

from core import shadow_eval as sh


def test_shadow_table_exists(test_db):
    with test_db.get_conn() as conn:
        row = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='shadow_evaluations'"
        ).fetchone()
    assert row is not None


def test_record_shadow(test_db):
    sid = sh.record_shadow("trade_threshold", 55.0, 45.0, 0.30, 0.10)
    assert sid is not None
    with test_db.get_conn() as conn:
        row = conn.execute("SELECT * FROM shadow_evaluations WHERE id=?", (sid,)).fetchone()
    assert row["param_key"] == "trade_threshold"
    assert row["kept_value"] == "55.0" and row["rejected_value"] == "45.0"
    assert row["verdict"] is None  # henüz değerlendirilmedi


def test_evaluate_pending_correct(test_db):
    # 73h önce reddedilmiş öneri ekle
    created = (datetime.now(timezone.utc) - timedelta(hours=73)).isoformat()
    with test_db.get_conn() as conn:
        conn.execute(
            "INSERT INTO shadow_evaluations (created_at,param_key,kept_value,rejected_value,sim_old_e,sim_new_e) "
            "VALUES (?, 'trade_threshold','55.0','45.0',0.30,0.10)",
            (created,),
        )
    # Güncel veride re-sim: reddedilen HÂLÂ daha kötü → approved=False → rejection_correct
    with patch("core.param_gate.validate_param_change",
               return_value=(False, {"old_expectancy_r": 0.30, "new_expectancy_r": 0.08})):
        n = sh.evaluate_pending_shadows()
    assert n == 1
    with test_db.get_conn() as conn:
        row = conn.execute("SELECT verdict, eval_new_e FROM shadow_evaluations").fetchone()
    assert row["verdict"] == "rejection_correct"
    assert row["eval_new_e"] == 0.08


def test_evaluate_pending_wrong(test_db):
    created = (datetime.now(timezone.utc) - timedelta(hours=73)).isoformat()
    with test_db.get_conn() as conn:
        conn.execute(
            "INSERT INTO shadow_evaluations (created_at,param_key,kept_value,rejected_value,sim_old_e,sim_new_e) "
            "VALUES (?, 'risk_pct','0.75','1.0',0.20,0.18)",
            (created,),
        )
    # Güncel veride reddedilen ARTIK daha iyi → approved=True → rejection_wrong
    with patch("core.param_gate.validate_param_change",
               return_value=(True, {"old_expectancy_r": 0.20, "new_expectancy_r": 0.35})):
        sh.evaluate_pending_shadows()
    with test_db.get_conn() as conn:
        row = conn.execute("SELECT verdict FROM shadow_evaluations").fetchone()
    assert row["verdict"] == "rejection_wrong"


def test_evaluate_skips_fresh(test_db):
    # 10h önce — 72h dolmadı, değerlendirilmemeli
    created = (datetime.now(timezone.utc) - timedelta(hours=10)).isoformat()
    with test_db.get_conn() as conn:
        conn.execute(
            "INSERT INTO shadow_evaluations (created_at,param_key,kept_value,rejected_value,sim_old_e,sim_new_e) "
            "VALUES (?, 'trade_threshold','55.0','45.0',0.30,0.10)",
            (created,),
        )
    n = sh.evaluate_pending_shadows()
    assert n == 0


def test_summarize_and_gate_accuracy(test_db):
    sh.record_shadow("a", 1, 2, 0.1, 0.05)  # pending
    with test_db.get_conn() as conn:
        conn.execute("UPDATE shadow_evaluations SET verdict='rejection_correct'")
        conn.execute("INSERT INTO shadow_evaluations (created_at,param_key,verdict) VALUES ('x','b','rejection_wrong')")
    s = sh.summarize_shadows()
    assert s["total"] == 2
    assert s["correct"] == 1 and s["wrong"] == 1
    assert s["gate_accuracy"] == 50.0  # 1 doğru / 2 karar
