"""P1-5: dashboard profit-readiness panel + stale-data warnings."""


def test_profit_readiness_panel_shape(test_db):
    import dashboard_service
    p = dashboard_service.get_profit_readiness_panel("paper")
    assert {"ready", "summary", "metrics", "gates"}.issubset(p)
    assert isinstance(p["gates"], list)
    assert p["ready"] in (True, False)


def test_stale_warnings_shape(test_db):
    import dashboard_service
    s = dashboard_service.get_stale_warnings()
    assert "stale" in s and isinstance(s.get("warnings"), list)
    assert s["stale"] in (True, False)


def test_command_center_exposes_panels(test_db):
    import dashboard_service
    cc = dashboard_service.get_command_center("paper")
    assert "profit_readiness" in cc
    assert "stale" in cc
