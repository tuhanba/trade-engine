"""
tests/test_trade_journal.py — Faz 6.1 Haftalık Trade Journal testleri.
"""
import json
from datetime import datetime, timezone, timedelta

import pytest

from core import trade_journal as tj


def _seed_trade(db, symbol, side, pnl, r, reason, score, meta, environment="paper", days_ago=1):
    now = datetime.now(timezone.utc)
    ct = (now - timedelta(days=days_ago)).isoformat()
    with db.get_conn() as conn:
        conn.execute(
            "INSERT INTO trades (symbol,direction,status,entry,close_price,net_pnl,risk_usd,"
            "r_multiple,close_reason,final_score,market_regime,hold_minutes,metadata,"
            "close_time,open_time,environment,is_valid_for_stats) "
            "VALUES (?,?,?,100,102,?,10,?,?,?,'TRENDING_HIGH_VOL',45,?,?,?,?,1)",
            (symbol, side, "closed", pnl, r, reason, score, json.dumps(meta), ct, ct, environment),
        )


def test_collect_week_data(test_db):
    _seed_trade(test_db, "SOLUSDT", "LONG", 31.4, 1.57, "tp2", 68, {"reason": "trend"})
    _seed_trade(test_db, "BTCUSDT", "SHORT", -10, -1.0, "sl", 55, {"adx": 22.5})
    data = tj.collect_week_data(7, "paper")
    assert data["stats"]["n"] == 2
    assert data["stats"]["wins"] == 1 and data["stats"]["losses"] == 1
    assert data["stats"]["net_pnl"] == pytest.approx(21.4)
    assert data["stats"]["total_r"] == pytest.approx(0.57)


def test_entry_rationale_extraction():
    assert tj._entry_rationale(json.dumps({"reason": "CVD divergence"})) == "CVD divergence"
    assert tj._entry_rationale(json.dumps({"trigger_type": "breakout"})) == "breakout"
    # İndikatör fallback
    out = tj._entry_rationale(json.dumps({"adx": 22.5, "rsi5": 71.0}))
    assert "ADX 22.5" in out and "RSI 71.0" in out
    assert tj._entry_rationale(None) == "—"
    assert tj._entry_rationale("{}") == "—"


def test_generate_markdown_structure(test_db):
    _seed_trade(test_db, "SOLUSDT", "LONG", 31.4, 1.57, "tp2", 68, {"reason": "trend align"})
    with test_db.get_conn() as conn:
        conn.execute(
            "INSERT INTO friday_decisions (created_at,decision_type,param_key,old_value,new_value,"
            "reasoning,outcome_score) VALUES (?,?,?,?,?,?,?)",
            (datetime.now(timezone.utc).isoformat(), "SET_PARAM", "trade_threshold", "55", "53", "x", 0.4),
        )
    md = tj.generate_markdown(7, "paper")
    assert "# 📓 AurvexAI Trade Journal" in md
    assert "## İşlemler" in md
    assert "SOLUSDT" in md and "trend align" in md
    assert "## Friday Kararları" in md
    assert "trade_threshold: 55→53" in md
    assert "## 🎓 Ders Çıkarımları" in md


def test_generate_markdown_empty(test_db):
    md = tj.generate_markdown(7, "paper")
    assert "Bu hafta kapanmış işlem yok" in md


def test_write_journal_file(test_db, tmp_path):
    _seed_trade(test_db, "ETHUSDT", "LONG", 18, 0.9, "tp1", 61, {"reason": "breakout"})
    path = tj.write_journal_file(7, "paper", out_dir=str(tmp_path))
    assert path.endswith(".md")
    import os
    assert os.path.exists(path)
    with open(path, encoding="utf-8") as f:
        content = f.read()
    assert "ETHUSDT" in content


def test_send_document_no_token(monkeypatch, tmp_path):
    import telegram_delivery
    monkeypatch.setattr(telegram_delivery.config, "TELEGRAM_BOT_TOKEN", "")
    p = tmp_path / "x.md"
    p.write_text("test")
    # Token yoksa graceful False döner, çökmesin
    assert telegram_delivery.send_document(str(p), "cap") is False
