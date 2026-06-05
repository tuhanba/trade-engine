"""
conftest.py – Global Pytest fixtures for root tests.
"""

import os
import sys
import importlib
import pytest

# Globally disable dashboard security PIN, IP whitelisting, and Redis cache for all tests
# This must happen before any tests import app or config.
os.environ["DASHBOARD_PIN"] = ""
os.environ["ALLOWED_IPS"] = ""
os.environ["REDIS_ENABLED"] = "False"
os.environ["EXECUTION_MODE"] = "paper"

@pytest.fixture(autouse=True, scope="session")
def setup_test_environment():
    """Sets up global test configurations to ensure security checks and IP restrictions don't block tests."""
    # Force empty dashboard pin for all tests by default
    import config
    config.DASHBOARD_PIN = ""
    config.REDIS_ENABLED = False
    
    # Disable IP whitelisting for app tests by clearing _ALLOWED_IPS
    try:
        import app
        app._ALLOWED_IPS.clear()
    except Exception:
        pass

@pytest.fixture(autouse=True)
def mock_coin_guards(monkeypatch):
    """Globally mock coin cooldown and mute guards to return False during testing to prevent cross-test contamination."""
    import database
    monkeypatch.setattr(database, "is_coin_in_cooldown", lambda symbol: False)
    monkeypatch.setattr(database, "is_coin_muted", lambda symbol: False)

@pytest.fixture()
def test_db(tmp_path, monkeypatch):
    """Geçici DB ile test ortamı oluşturur."""
    db_path = tmp_path / "test_trading.db"
    monkeypatch.setenv("DB_PATH", str(db_path))
    import config
    import database
    importlib.reload(config)
    importlib.reload(database)
    database.init_db()
    return database
