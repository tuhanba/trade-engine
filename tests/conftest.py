import os
import importlib
import pytest


@pytest.fixture()
def test_db(tmp_path, monkeypatch):
    db_path = tmp_path / "test_trading.db"
    monkeypatch.setenv("DB_PATH", str(db_path))
    import config
    import database
    importlib.reload(config)
    importlib.reload(database)
    database.init_db()
    return database
