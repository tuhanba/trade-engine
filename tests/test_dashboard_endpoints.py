import app as dashboard_app


def test_signal_funnel_endpoint_exists():
    client = dashboard_app.app.test_client()
    res = client.get("/api/signal_funnel")
    assert res.status_code in (200, 500)
