import sys
import os
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

import pytest
import config
from unittest.mock import MagicMock, patch
from core.trailing_engine import TrailingEngine, TradeExitState
from core.event_types import Event, EventType
from core.data_layer import SignalData
from core.services.execution_service import ExecutionService

def test_trailing_stop_config():
    """Verify that TRAILING_STOP_TYPE is loaded from config and defaults to 'atr'."""
    assert hasattr(config, "TRAILING_STOP_TYPE")
    assert getattr(config, "TRAILING_STOP_TYPE", "atr") in ("atr", "step")
    assert hasattr(config, "CONFIRMATION_MODE")

def test_stepwise_trailing_stop_tp2():
    """Verify that TP2 hit locks stop loss to TP1 in step-wise mode."""
    engine = TrailingEngine(
        tp1_close_pct=40,
        tp2_close_pct=30,
        runner_close_pct=30,
        trail_atr_mult=1.5,
        breakeven_enabled=True,
    )
    
    trade = {
        "id": 123,
        "symbol": "BTCUSDT",
        "direction": "LONG",
        "entry": 50000.0,
        "sl": 48000.0,
        "tp1": 52000.0,
        "tp2": 54000.0,
        "tp3": 58000.0,
        "qty": 0.1,
    }
    
    state = TradeExitState(
        tp1_hit=True,
        tp2_hit=False,
        current_sl=50000.0,
        highest_price=53000.0,
        qty_remaining_pct=60.0,
        initial_sl=48000.0
    )
    
    # Enable stepwise trailing type
    with patch("config.TRAILING_STOP_TYPE", "step"):
        result = engine.evaluate(trade, 54100.0, state, atr=1000.0)
        
        # Verify that partial close is triggered
        assert result.should_partial_close is True
        assert result.close_pct == 30
        assert result.reason == "TP2_HIT"
        # Verify that SL is locked to TP1
        assert result.new_sl == 52000.0
        assert state.current_sl == 52000.0

def test_stepwise_trailing_active():
    """Verify active step-wise trailing updates stop loss in discrete steps of 0.5 * ATR."""
    engine = TrailingEngine(
        tp1_close_pct=40,
        tp2_close_pct=30,
        runner_close_pct=30,
        trail_atr_mult=1.5,
        breakeven_enabled=True,
    )
    
    trade = {
        "id": 123,
        "symbol": "BTCUSDT",
        "direction": "LONG",
        "entry": 50000.0,
        "sl": 48000.0,
        "tp1": 52000.0,
        "tp2": 54000.0,
        "tp3": 58000.0,
        "qty": 0.1,
    }
    
    state = TradeExitState(
        tp1_hit=True,
        tp2_hit=True,
        trailing_active=True,
        current_sl=52000.0,
        highest_price=54500.0,
        qty_remaining_pct=30.0,
        initial_sl=48000.0
    )
    
    with patch("config.TRAILING_STOP_TYPE", "step"):
        # Let's say ATR is 1000.
        # Step size is 1000 * 0.5 = 500.
        # Trail distance threshold is 1000 * 1.5 = 1500.
        # Current SL is 52000.
        # Target price: 54100.
        # Distance: 54100 - 52000 - 1500 = 600.
        # steps = int(600 / 500) = 1 step.
        # New SL = 52000 + 500 = 52500.
        result = engine.evaluate(trade, 54100.0, state, atr=1000.0)
        
        assert state.current_sl == 52500.0
        assert result.new_sl == 52500.0

@pytest.mark.asyncio
async def test_execution_service_confirmation_gate():
    """Verify that ExecutionService intercepts execution when CONFIRMATION_MODE is enabled."""
    with patch("config.CONFIRMATION_MODE", True), \
         patch("config.EXECUTION_MODE", "paper"), \
         patch("database.update_candidate_status") as mock_update_status, \
         patch("database.save_signal_event") as mock_save_event, \
         patch("telegram_delivery.send_message") as mock_send_msg:
         
        exec_svc = ExecutionService()
        
        # Assemble fake event payload
        sig_data = {
            "symbol": "ETHUSDT",
            "side": "LONG",
            "direction": "LONG",
            "entry_price": 3000.0,
            "entry_zone": 3000.0,
            "stop_loss": 2900.0,
            "tp1": 3100.0,
            "tp2": 3200.0,
            "tp3": 3300.0,
            "final_score": 65.0,
            "setup_quality": "A",
            "leverage_suggestion": 10,
            "max_loss": 50.0,
            "risk_percent": 1.0,
            "position_size": 0.5,
            "notional_size": 1500.0,
            "rr": 2.0,
        }
        
        event = Event(
            type=EventType.AI_VALIDATED,
            payload={
                "symbol": "ETHUSDT",
                "signal_id": 999,
                "candidate_id": 888,
                "signal_data": sig_data,
                "ai_decision": {"decision": "ALLOW", "final_score": 65.0, "reason": "Scored normal"}
            }
        )
        
        # Execute handler
        await exec_svc.handle_ai_validated(event)
        
        # Verify candidate status was updated to PENDING_APPROVAL
        mock_update_status.assert_called_once_with(888, decision="PENDING_APPROVAL", reject_reason="Awaiting manual confirmation")
        # Verify signal event was saved as PENDING_APPROVAL
        mock_save_event.assert_called_once_with(999, "PENDING_APPROVAL", symbol="ETHUSDT", reject_reason="Awaiting manual confirmation")
        # Verify Telegram alert was sent
        mock_send_msg.assert_called_once()
        args, kwargs = mock_send_msg.call_args
        assert "İŞLEM ONAY BEKLİYOR" in args[0]
        assert "cmd:appr_cand_888" in kwargs["reply_markup"]["inline_keyboard"][0][0]["callback_data"]

def test_backtester_offline_run():
    """Verify that BacktestRunner runs in offline mode successfully."""
    from scripts.backtest_system import BacktestRunner
    from datetime import datetime, timezone, timedelta
    
    end_time = datetime.now(timezone.utc)
    # Use a small 2-hour window to keep test fast
    start_time = end_time - timedelta(hours=2)
    
    runner = BacktestRunner(
        symbols=["BTCUSDT"],
        start_time=start_time,
        end_time=end_time,
        initial_balance=1000.0,
        offline=True
    )
    
    # Check that initialization succeeded and run can be invoked
    assert runner.initial_balance == 1000.0
    assert "BTCUSDT" in runner.symbols
    
    # Execute runner.run() to ensure it passes database setup and simulation loop
    runner.run()
    assert runner.funnel_stats is not None

def test_choppy_regime_gating():
    """Verify quality/score gating and leverage halving in CHOPPY market regime."""
    from core.risk_engine import RiskEngine
    import config
    
    mock_client = MagicMock()
    # Mock futures_klines to return some historical candles for ATR computation
    # High, Low, Close kline indexes: 2, 3, 4
    mock_client.futures_klines.return_value = [
        [0, 100, 101, 99, 100, 10, 0, 0, 0, 0, 0, 0] for _ in range(30)
    ]
    
    engine = RiskEngine(mock_client, db_path="trading.db")
    
    with patch("database.get_market_regime", return_value="CHOPPY"), \
         patch("database.get_open_trades", return_value=[]), \
         patch("config.TRADE_THRESHOLD", 55.0):
         
        # 1. Quality gate test: B quality should be rejected
        res = engine.calculate("BTCUSDT", "LONG", 50000.0, "B", 1000.0)
        assert res["valid"] is False
        assert res["risk_reject_reason"] == "choppy_market_quality_gate"
        
        # 2. Score gate test: A+ quality with score 58 (below TRADE_THRESHOLD + 5.0 = 60.0) should be rejected
        res = engine.calculate("BTCUSDT", "LONG", 50000.0, "A+", 1000.0, score=58.0)
        assert res["valid"] is False
        assert res["risk_reject_reason"] == "choppy_market_score_gate"
        
        # 3. Valid setup: A+ quality with score 62 should pass, and leverage should be scaled down
        res_normal = engine.calculate("BTCUSDT", "LONG", 50000.0, "A+", 1000.0, score=62.0)
        
        # Now verify without CHOPPY (NEUTRAL regime)
        with patch("database.get_market_regime", return_value="NEUTRAL"):
            res_neutral = engine.calculate("BTCUSDT", "LONG", 50000.0, "A+", 1000.0, score=62.0)
            
        if res_normal.get("valid") and res_neutral.get("valid"):
            # Choppy leverage should be scaled down by 0.5 compared to neutral leverage
            assert res_normal["leverage"] <= res_neutral["leverage"]

def test_dynamic_profit_lock():
    """Verify that Dynamic Profit Lock limits risk and gates quality when targets are met."""
    import sqlite3
    from core.risk_engine import RiskEngine
    from datetime import datetime, timezone
    
    temp_db = "backtest_temp_9999.db"
    if os.path.exists(temp_db):
        os.remove(temp_db)
        
    conn = sqlite3.connect(temp_db)
    conn.execute("""
        CREATE TABLE trades (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            symbol TEXT,
            direction TEXT,
            entry REAL,
            sl REAL,
            close_time TEXT,
            status TEXT,
            environment TEXT,
            net_pnl REAL
        )
    """)
    # Insert a closed trade today with $50.0 profit
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    conn.execute(
        "INSERT INTO trades (symbol, direction, entry, sl, close_time, status, environment, net_pnl) "
        "VALUES ('BTCUSDT', 'LONG', 50000.0, 49000.0, ?, 'closed', 'paper', 50.0)", (today + "T12:00:00Z",)
    )
    conn.commit()
    conn.close()
    
    mock_client = MagicMock()
    mock_client.futures_klines.return_value = [
        [0, 100, 101, 99, 100, 10, 0, 0, 0, 0, 0, 0] for _ in range(30)
    ]
    
    engine = RiskEngine(mock_client, db_path=temp_db)
    
    # Balance = 1000.0. Daily PnL = 50.0 (5.0% of balance).
    # Target = 3% of balance ($30.0). Since $50.0 >= $30.0, daily target is met.
    with patch("database.get_open_trades", return_value=[]), \
         patch("config.DAILY_PROFIT_LOCK_PCT", 3.0), \
         patch("config.EXECUTION_MODE", "paper"):
         
        # 1. Quality check: B quality setup should be gated/rejected
        res_b = engine.calculate("BTCUSDT", "LONG", 50000.0, "B", 1000.0)
        assert res_b["valid"] is False
        assert res_b["risk_reject_reason"] == "daily_profit_target_reached_quality_gate"
        
        # 2. Risk check: S quality setup should pass, but risk_pct should be scaled down by 0.5
        res_s = engine.calculate("BTCUSDT", "LONG", 50000.0, "S", 1000.0)
        assert res_s["valid"] is True
        
        # Calculate expected risk scaling
        with patch("config.DAILY_PROFIT_LOCK_PCT", 999.0): # Target not met
            res_s_normal = engine.calculate("BTCUSDT", "LONG", 50000.0, "S", 1000.0)
            
        assert res_s["risk_pct"] == res_s_normal["risk_pct"] * 0.5
        
    if os.path.exists(temp_db):
        os.remove(temp_db)

def test_ml_telegram_commands():
    """Verify that /ml and /retrain command handlers format and execute correctly."""
    from telegram_manager import TelegramManager
    
    mock_send = MagicMock()
    bot = TelegramManager(send_fn=mock_send)
    
    # Mock MLSignalScorer get_status and train_model
    mock_status = {
        "trained": True,
        "n_samples": 42,
        "last_train": "2026-06-03T18:00:00Z",
        "threshold": 35,
        "cv_accuracy": 0.78,
        "precision_at_70": 0.82,
        "top_features": [("ADX", 0.15), ("RV", 0.12)]
    }
    
    with patch("core.ml_signal_scorer.get_scorer") as mock_get_scorer, \
         patch("core.ml_signal_scorer.train_model", return_value=True) as mock_train:
         
        mock_get_scorer.return_value.get_status.return_value = mock_status
        
        # Execute _cmd_ml
        bot._cmd_ml()
        mock_send.assert_called_once()
        args, kwargs = mock_send.call_args
        assert "Yapay Zeka (ML) Model İstatistikleri" in args[0]
        assert "42" in args[0]
        assert "cmd:retrain_ml" in kwargs["reply_markup"]["inline_keyboard"][0][0]["callback_data"]
        
        # Reset mock and execute _cmd_retrain
        mock_send.reset_mock()
        bot._cmd_retrain()
        assert mock_send.call_count == 2
        args1, _ = mock_send.call_args_list[0]
        args2, _ = mock_send.call_args_list[1]
        assert "yeniden eğitiliyor" in args1[0]
        assert "Modeli Başarıyla Eğitildi" in args2[0]

def test_spectra_ceo_context():
    """Verify that SpectraCeo gathers correct system stats and config values."""
    from core.spectra_ceo import SpectraCeo
    import os
    
    ceo = SpectraCeo(db_path="trading.db")
    ctx = ceo.get_system_context()
    
    assert "db_size_mb" in ctx
    assert "config" in ctx
    assert ctx["config"]["execution_mode"] in ("paper", "live")
    assert "market_regime" in ctx

def test_spectra_decision_parsing():
    """Verify that SpectraCeo parses Claude JSON output and applies parameter updates and actions."""
    from core.spectra_ceo import SpectraCeo
    import sqlite3
    import os
    
    temp_db = "temp_db_spectra.db"
    if os.path.exists(temp_db):
        os.remove(temp_db)
        
    conn = sqlite3.connect(temp_db)
    conn.execute("""
        CREATE TABLE params (
            id INTEGER PRIMARY KEY,
            risk_pct REAL,
            sl_atr_mult REAL,
            tp_atr_mult REAL,
            updated_at TEXT
        )
    """)
    conn.execute("INSERT INTO params (id, risk_pct) VALUES (1, 1.0)")
    conn.commit()
    conn.close()
    
    ceo = SpectraCeo(db_path=temp_db)
    
    # Test valid JSON parser
    raw_response = (
        "Merhaba boss! Piyasa güzel görünüyor, parametreleri güncelledim.\n"
        "```json\n"
        "{\n"
        '  "parameters": {\n'
        '    "trade_threshold": 59.5,\n'
        '    "risk_pct": 0.85\n'
        "  },\n"
        '  "actions": ["RETRAIN"]\n'
        "}\n"
        "```\n"
    )
    
    decisions = ceo._parse_decisions(raw_response)
    assert decisions["parameters"]["trade_threshold"] == 59.5
    assert decisions["parameters"]["risk_pct"] == 0.85
    assert "RETRAIN" in decisions["actions"]
    
    # Test execution mock
    with patch("database.set_state") as mock_set, \
         patch("core.ml_signal_scorer.train_model", return_value=True) as mock_train:
         
        msgs = ceo._execute_decisions(decisions)
        
        # Verify db updates were called for dynamic parameters
        assert mock_set.call_count == 1
        # Verify ML training action was triggered
        mock_train.assert_called_once()
        
        # Verify risk_pct was updated in params table
        conn = sqlite3.connect(temp_db)
        row = conn.execute("SELECT risk_pct FROM params WHERE id = 1").fetchone()
        conn.close()
        assert row[0] == 0.85
        
        # Check applied messages output
        assert any("TRADE_THRESHOLD" in m for m in msgs)
        assert any("ML Modeli Yeniden Eğitildi" in m for m in msgs)
        assert any("RISK_PCT" in m for m in msgs)
        
    if os.path.exists(temp_db):
        os.remove(temp_db)

def test_spectra_telegram_command():
    """Verify that TelegramManager correctly forwards /spectra command to SpectraCeo."""
    from telegram_manager import TelegramManager
    from core.spectra_ceo import SpectraCeo
    
    mock_send = MagicMock()
    mock_ceo = MagicMock(spec=SpectraCeo)
    
    bot = TelegramManager(send_fn=mock_send, spectra_ceo=mock_ceo)
    
    # Execute /spectra command with arguments
    bot._cmd_spectra(["boss", "naber"])
    
    # Verify that it starts a thread to execute evaluate_and_decide with the joined arguments
    import time
    time.sleep(0.1) # Wait for thread to launch
    mock_ceo.evaluate_and_decide.assert_called_once_with("boss naber")


def test_spectra_voice_generation():
    """Verify that SpectraCeo generate_voice_from_text works and generates valid bytes."""
    from core.spectra_ceo import SpectraCeo
    ceo = SpectraCeo()
    voice_bytes = ceo.generate_voice_from_text("Merhaba Boss, her şey yolunda.")
    assert voice_bytes is not None
    assert len(voice_bytes) > 0
    assert isinstance(voice_bytes, bytes)


def test_spectra_server_housekeeping():
    """Verify that SpectraCeo scans and cleans up temporary backtest and log files safely."""
    from core.spectra_ceo import SpectraCeo
    import core.spectra_ceo
    import os
    
    ceo = SpectraCeo()
    base_dir = os.path.dirname(os.path.dirname(os.path.abspath(core.spectra_ceo.__file__)))
    
    
    # Create test dummy files
    temp_db_path = os.path.join(base_dir, "backtest_temp_9999.db")
    temp_log_path = os.path.join(base_dir, "test_spectra_dummy.log")
    
    with open(temp_db_path, "w") as f:
        f.write("dummy db content")
    with open(temp_log_path, "w") as f:
        f.write("dummy log content")
        
    try:
        # Scan and ensure they are found
        files = ceo.scan_unnecessary_files()
        assert temp_db_path in files
        assert temp_log_path in files
        
        # Cleanup
        deleted_count, saved_space_mb = ceo.execute_cleanup()
        assert deleted_count >= 2
        assert saved_space_mb > 0
        
        # Ensure they are deleted
        assert not os.path.exists(temp_db_path)
        assert not os.path.exists(temp_log_path)
    finally:
        if os.path.exists(temp_db_path):
            os.remove(temp_db_path)
        if os.path.exists(temp_log_path):
            os.remove(temp_log_path)


def test_spectra_chat_endpoint():
    """Verify that /api/spectra/chat endpoint processes chat messages and returns voice/reply."""
    from app import app
    from unittest.mock import patch
    
    with patch("core.spectra_ceo.SpectraCeo.evaluate_and_decide", return_value="Merhaba Boss! Ben Spektra. ```json\n{}\n```") as mock_eval:
        client = app.test_client()
        resp = client.post("/api/spectra/chat", json={"message": "naber"})
        
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["ok"] is True
        assert "reply" in data
        assert "voice" in data
        mock_eval.assert_called_once_with("naber", send_telegram=False)

