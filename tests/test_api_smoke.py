"""
tests/test_api_smoke.py – Flask API smoke testleri.
"""

import sys
import os
import json
import tempfile
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import config
import database


def test_app_import():
    """app modülü import edilebilmeli."""
    import app
    assert hasattr(app, "app")
    assert hasattr(app, "api_health")


def test_endpoints():
    """Tüm API endpoint'leri 200 dönmeli ve JSON formatında olmalı."""
    original = config.DB_PATH
    tmp = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
    config.DB_PATH = tmp.name
    tmp.close()

    try:
        database.init_db()

        from app import app
        client = app.test_client()

        endpoints = [
            "/api/health",
            "/api/live",
            "/api/stats",
            "/api/trades",
            "/api/signals",
        ]

        for ep in endpoints:
            resp = client.get(ep)
            assert resp.status_code == 200, f"{ep} → {resp.status_code}"
            data = resp.get_json()
            assert data is not None, f"{ep} → JSON parse hatası"
            assert "ok" in data, f"{ep} → 'ok' alanı yok"
            assert data["ok"] is True, f"{ep} → ok=False: {data.get('error')}"

    finally:
        config.DB_PATH = original
        os.unlink(tmp.name)


if __name__ == "__main__":
    tests = [test_app_import, test_endpoints]
    passed = 0
    failed = 0
    for t in tests:
        try:
            t()
            print(f"  ✓ {t.__name__}")
            passed += 1
        except Exception as exc:
            print(f"  ✗ {t.__name__}: {exc}")
            failed += 1
    print(f"\n{passed} passed, {failed} failed")
    sys.exit(1 if failed else 0)
