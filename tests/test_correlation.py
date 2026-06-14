"""
tests/test_correlation.py — Faz 6.3 korelasyon matrisi testleri.
"""
import numpy as np
from unittest.mock import patch

import pytest

import core.portfolio_risk as pr
import dashboard_service


_BASE = np.array([0.01, -0.02, 0.03, -0.01, 0.02, 0.01, -0.03, 0.02, -0.01, 0.01, 0.02])


def _fake_returns(sym, interval="1h", limit=50):
    return {"AUSDT": _BASE, "BUSDT": _BASE * 1.05, "CUSDT": -_BASE}.get(sym)


def test_correlation_matrix_values():
    with patch.object(pr, "_get_returns", _fake_returns):
        m = pr.calculate_correlation_matrix(["AUSDT", "BUSDT", "CUSDT"])
    assert m["symbols"] == ["AUSDT", "BUSDT", "CUSDT"]
    # Köşegen 1.0
    assert m["matrix"][0][0] == 1.0
    # A↔B pozitif (~+1), A↔C ters (~-1)
    assert m["matrix"][0][1] > 0.9
    assert m["matrix"][0][2] < -0.9
    # En yüksek mutlak korelasyon A↔B
    assert m["max_pair"]["corr"] > 0.9


def test_correlation_matrix_missing_data():
    with patch.object(pr, "_get_returns", lambda s, *a, **k: None):
        m = pr.calculate_correlation_matrix(["XUSDT", "YUSDT"])
    # Getiri yoksa off-diagonal None, köşegen yine 1.0
    assert m["matrix"][0][0] == 1.0
    assert m["matrix"][0][1] is None
    assert m["max_pair"] is None


def test_correlation_matrix_empty():
    m = pr.calculate_correlation_matrix([])
    assert m["symbols"] == [] and m["matrix"] == []


def test_dashboard_correlation_needs_two_positions(test_db, monkeypatch):
    # 0-1 açık pozisyonda korelasyon yok → açıklayıcı not
    monkeypatch.setattr(dashboard_service, "get_live_trades", lambda env=None: [{"symbol": "BTCUSDT"}])
    out = dashboard_service.get_correlation_matrix("paper")
    assert out["matrix"] == []
    assert "en az 2" in out.get("note", "").lower()


def test_dashboard_correlation_with_positions(test_db, monkeypatch):
    monkeypatch.setattr(dashboard_service, "get_live_trades",
                        lambda env=None: [{"symbol": "AUSDT"}, {"symbol": "BUSDT"}])
    with patch.object(pr, "_get_returns", _fake_returns):
        out = dashboard_service.get_correlation_matrix("paper")
    assert set(out["symbols"]) == {"AUSDT", "BUSDT"}
    assert len(out["matrix"]) == 2
