"""
Test fixtures — geçici DB ve mock Binance client.
"""
import os, sys, tempfile, sqlite3, pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


@pytest.fixture(scope="function")
def tmp_db(tmp_path, monkeypatch):
    """Her test için temiz, geçici DB."""
    db_file = str(tmp_path / "test_trading.db")
    monkeypatch.setenv("DB_PATH", db_file)
    # config modülünü reload et ki yeni DB_PATH'i görsün
    import importlib
    import config as cfg
    monkeypatch.setattr(cfg, "DB_PATH", db_file)
    import database as db
    monkeypatch.setattr(db, "DB_PATH", db_file)
    db.init_db()
    return db_file


@pytest.fixture
def mock_client(mocker):
    """Binance client mock'u."""
    client = mocker.MagicMock()
    client.futures_ticker.return_value = []
    client.futures_exchange_info.return_value = {
        "symbols": [
            {
                "symbol": "BTCUSDT",
                "quoteAsset": "USDT",
                "status": "TRADING",
                "filters": [
                    {"filterType": "PRICE_FILTER", "tickSize": "0.10"},
                    {"filterType": "LOT_SIZE",     "stepSize": "0.001", "minQty": "0.001"},
                    {"filterType": "MIN_NOTIONAL",  "notional": "5.0"},
                ],
            }
        ]
    }
    return client


@pytest.fixture
def sample_klines():
    """100 mum verisi — gerçekçi değerler."""
    import pandas as pd, numpy as np
    np.random.seed(42)
    n = 100
    prices = 30000 + np.cumsum(np.random.randn(n) * 200)
    df = pd.DataFrame({
        "time":   range(n),
        "open":   prices - 50,
        "high":   prices + 200,
        "low":    prices - 200,
        "close":  prices,
        "volume": np.abs(np.random.randn(n)) * 1000 + 500,
        "ct": 0, "qav": 0, "nt": 0, "tbbav": 0, "tbqav": 0, "ignore": 0,
    })
    return df
