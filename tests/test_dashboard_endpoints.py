"""test_dashboard_endpoints.py — Dashboard endpoint'ler crash etmemeli."""
import pytest


@pytest.fixture
def app_client(tmp_db, monkeypatch):
    import os
    monkeypatch.setenv("DB_PATH", tmp_db)
    monkeypatch.setenv("BINANCE_API_KEY", "test")
    monkeypatch.setenv("BINANCE_API_SECRET", "test")

    # Binance client mock
    from unittest.mock import MagicMock
    import sys
    mock_binance = MagicMock()
    mock_binance.futures_ticker.return_value = []
    mock_binance.futures_symbol_ticker.return_value = {"price": "0"}
    sys.modules["binance.client"] = MagicMock(Client=MagicMock(return_value=mock_binance))

    import importlib
    import app as application
    importlib.reload(application)
    application.app.config["TESTING"] = True
    with application.app.test_client() as c:
        yield c


def test_health_endpoint(app_client):
    resp = app_client.get("/api/health")
    assert resp.status_code == 200
    data = resp.get_json()
    assert data["ok"] or "error" in data


def test_stats_endpoint_no_crash(app_client):
    resp = app_client.get("/api/stats")
    assert resp.status_code in (200, 500)
    data = resp.get_json()
    assert "ok" in data


def test_funnel_endpoint(app_client):
    resp = app_client.get("/api/funnel")
    assert resp.status_code in (200, 500)
    data = resp.get_json()
    assert "ok" in data
    if data["ok"]:
        assert "funnel" in data["data"]


def test_candidates_endpoint(app_client):
    resp = app_client.get("/api/candidates?limit=10")
    assert resp.status_code in (200, 500)
    data = resp.get_json()
    assert "ok" in data


def test_scanned_coins_endpoint(app_client):
    resp = app_client.get("/api/scanned_coins?limit=20")
    assert resp.status_code in (200, 500)
    data = resp.get_json()
    assert "ok" in data


def test_paper_results_endpoint(app_client):
    resp = app_client.get("/api/paper_results")
    assert resp.status_code in (200, 500)
    data = resp.get_json()
    assert "ok" in data


def test_missed_opportunities_endpoint(app_client):
    resp = app_client.get("/api/missed_opportunities")
    assert resp.status_code in (200, 500)
    data = resp.get_json()
    assert "ok" in data


def test_scalp_signals_empty_ok(app_client):
    """Veri yokken endpoint crash etmemeli."""
    resp = app_client.get("/api/scalp_signals")
    assert resp.status_code == 200
    data = resp.get_json()
    assert data["ok"]
    assert isinstance(data["data"], list)
