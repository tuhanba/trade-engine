import sys
import os
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest
import sqlite3
import config
from core.risk_engine import RiskEngine
from unittest.mock import MagicMock, patch

class MockBinanceClientForCorrelation:
    def __init__(self, corr_coefficient=0.8):
        self.corr_coefficient = corr_coefficient

    def futures_klines(self, symbol, interval, limit):
        # Return mock candlesticks
        rows = []
        price = 100.0
        # If interval is 15m, return correlation-tailored data
        for i in range(limit):
            o = price + i * 0.1
            h = o + 0.5
            l = o - 0.5
            c = o + 0.2
            rows.append([0, str(o), str(h), str(l), str(c), "1000", 0, "0", 0, "0", "0", "0"])
        return rows

@pytest.fixture
def setup_test_db(tmp_path, monkeypatch):
    """Sets up a temporary SQLite database for upgrades calculation tests."""
    db_path = tmp_path / "test_trading.db"
    monkeypatch.setattr(config, "DB_PATH", str(db_path))
    
    import database
    database.init_db()
    
    # Insert required test settings
    with database.get_conn() as conn:
        conn.execute("INSERT OR REPLACE INTO params (id, risk_pct) VALUES (1, 1.0)")
        conn.execute("INSERT INTO balance_ledger (balance_after) VALUES (2000.0)")
        conn.commit()
        
    return str(db_path)

def test_pearson_correlation_block(setup_test_db):
    """Verifies that correlation > 0.90 blocks the trade entirely."""
    engine = RiskEngine(MockBinanceClientForCorrelation(), db_path=setup_test_db)
    
    # Mock get_open_trades to return an active trade on ETHUSDT
    open_trades_mock = [{"symbol": "ETHUSDT", "direction": "LONG", "margin_used": 10.0}]
    
    with patch("database.get_open_trades", return_value=open_trades_mock), \
         patch("core.risk_engine.calculate_historical_correlation", return_value=0.95):
        
        res = engine.calculate("BTCUSDT", "LONG", 100.0, "A+", 2000.0)
        assert res["valid"] is False
        assert res["risk_reject_reason"] == "high_correlation_block"

def test_pearson_correlation_scaling(setup_test_db):
    """Verifies that correlation > 0.75 scales down position size by 50%."""
    engine = RiskEngine(MockBinanceClientForCorrelation(), db_path=setup_test_db)
    open_trades_mock = [{"symbol": "ETHUSDT", "direction": "LONG", "margin_used": 10.0}]
    
    with patch("database.get_open_trades", return_value=open_trades_mock), \
         patch("core.risk_engine.calculate_historical_correlation", return_value=0.80), \
         patch("core.risk_engine.calculate_kelly_risk_pct", return_value=2.0):
        
        # Kelly: 2.0 * quality_mult(A+=1.5) = 3.0%
        # Correlation > 0.75 scales risk by 50% -> 1.5%
        res = engine.calculate("BTCUSDT", "LONG", 100.0, "A+", 2000.0)
        assert res["valid"] is True
        assert res["risk_pct"] == 1.5

def test_slippage_and_latency_guard_scaling(setup_test_db):
    """Verifies that average slippage > 0.15% (30% reduction) and latency > 500ms (20% reduction) applies correctly."""
    # Write 3 closed trades to database with high slippage and latency
    conn = sqlite3.connect(setup_test_db)
    conn.execute("INSERT INTO trades (symbol, direction, status, slippage, latency_ms) VALUES ('BTCUSDT', 'LONG', 'closed', 0.20, 600)")
    conn.execute("INSERT INTO trades (symbol, direction, status, slippage, latency_ms) VALUES ('ETHUSDT', 'LONG', 'closed', 0.25, 550)")
    conn.execute("INSERT INTO trades (symbol, direction, status, slippage, latency_ms) VALUES ('SOLUSDT', 'LONG', 'closed', 0.15, 650)")
    conn.commit()
    conn.close()

    engine = RiskEngine(MockBinanceClientForCorrelation(), db_path=setup_test_db)
    
    with patch("database.get_open_trades", return_value=[]), \
         patch("core.risk_engine.calculate_kelly_risk_pct", return_value=2.0):
        
        # Kelly: 2.0 * quality_mult(A+=1.5) = 3.0%
        # Average slippage = (0.2 + 0.25 + 0.15) / 3 = 0.20% > 0.15% (30% reduction -> 0.7x multiplier)
        # Average latency = (600 + 550 + 650) / 3 = 600ms > 500ms (20% reduction -> 0.8x multiplier)
        # Final risk_pct = 3.0 * 0.7 * 0.8 = 1.68%
        res = engine.calculate("BTCUSDT", "LONG", 100.0, "A+", 2000.0)
        assert res["valid"] is True
        assert abs(res["risk_pct"] - 1.68) < 0.01

def test_kelly_sizing_safety_bounds(setup_test_db):
    """Verifies that final position size risk_pct is clamped to minimum 0.5% and maximum 3.0%."""
    engine = RiskEngine(MockBinanceClientForCorrelation(), db_path=setup_test_db)
    
    # Case 1: Extremely high risk_pct gets clamped to 3.0%
    with patch("database.get_open_trades", return_value=[]), \
         patch("core.risk_engine.calculate_kelly_risk_pct", return_value=5.0):
        # Kelly: 5.0 * quality_mult(A+=1.5) = 7.5% -> Clamped to 3.0%
        res = engine.calculate("BTCUSDT", "LONG", 100.0, "A+", 2000.0)
        assert res["valid"] is True
        assert res["risk_pct"] == 3.0

    # Case 2: Extremely low risk_pct gets clamped to 0.5%
    with patch("database.get_open_trades", return_value=[]), \
         patch("core.risk_engine.calculate_kelly_risk_pct", return_value=0.1):
        # Kelly: 0.1 * quality_mult(B=0.5) = 0.05% -> Clamped to 0.5%
        res = engine.calculate("BTCUSDT", "LONG", 100.0, "B", 2000.0)
        assert res["valid"] is True
        assert res["risk_pct"] == 0.5
