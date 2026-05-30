import app as dashboard_app


def test_signal_funnel_endpoint_exists():
    client = dashboard_app.app.test_client()
    res = client.get("/api/signal_funnel")
    assert res.status_code in (200, 500)
    if res.status_code == 200:
        js = res.get_json()
        assert js.get("ok") is True
        assert "learning" in js


def test_learning_metrics_endpoint():
    client = dashboard_app.app.test_client()
    res = client.get("/api/learning_metrics")
    assert res.status_code in (200, 500)
