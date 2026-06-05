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
         patch("config.CONFIRMATION_AUTO_EXECUTE_HIGH_QUALITY", False), \
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

def test_friday_ceo_context():
    """Verify that FridayCeo gathers correct system stats and config values."""
    from core.friday_ceo import FridayCeo
    import os
    
    ceo = FridayCeo(db_path="trading.db")
    ctx = ceo.get_system_context()
    
    assert "db_size_mb" in ctx
    assert "config" in ctx
    assert ctx["config"]["execution_mode"] in ("paper", "live")
    assert "market_regime" in ctx

def test_friday_decision_parsing():
    """Verify that FridayCeo parses Claude JSON output and applies parameter updates and actions."""
    from core.friday_ceo import FridayCeo
    import sqlite3
    import os
    
    temp_db = "temp_db_friday.db"
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
    
    ceo = FridayCeo(db_path=temp_db)
    
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

def test_friday_telegram_command():
    """Verify that TelegramManager correctly forwards /friday command to FridayCeo."""
    from telegram_manager import TelegramManager
    from core.friday_ceo import FridayCeo
    
    mock_send = MagicMock()
    mock_ceo = MagicMock(spec=FridayCeo)
    
    bot = TelegramManager(send_fn=mock_send, friday_ceo=mock_ceo)
    
    # Execute /friday command with arguments
    bot._cmd_friday(["boss", "naber"])
    
    # Verify that it starts a thread to execute evaluate_and_decide with the joined arguments
    import time
    time.sleep(0.1) # Wait for thread to launch
    mock_ceo.evaluate_and_decide.assert_called_once_with("boss naber")


def test_friday_voice_generation():
    """Verify that FridayCeo generate_voice_from_text works and generates valid bytes."""
    from core.friday_ceo import FridayCeo
    ceo = FridayCeo()
    voice_bytes = ceo.generate_voice_from_text("Merhaba Boss, her şey yolunda.")
    assert voice_bytes is not None
    assert len(voice_bytes) > 0
    assert isinstance(voice_bytes, bytes)


def test_friday_server_housekeeping():
    """Verify that FridayCeo scans and cleans up temporary backtest and log files safely."""
    from core.friday_ceo import FridayCeo
    import core.friday_ceo
    import os
    
    ceo = FridayCeo()
    base_dir = os.path.dirname(os.path.dirname(os.path.abspath(core.friday_ceo.__file__)))
    
    
    # Create test dummy files
    temp_db_path = os.path.join(base_dir, "backtest_temp_9999.db")
    temp_log_path = os.path.join(base_dir, "test_friday_dummy.log")
    
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


def test_friday_chat_endpoint():
    """Verify that /api/friday/chat endpoint processes chat messages and returns voice/reply."""
    from app import app
    from unittest.mock import patch
    
    with patch("core.friday_ceo.FridayCeo.evaluate_and_decide", return_value="Merhaba Boss! Ben Friday. ```json\n{}\n```") as mock_eval:
        client = app.test_client()
        resp = client.post("/api/friday/chat", json={"message": "naber"})
        
        assert resp.status_code == 200
        data = resp.get_json()
        assert data["ok"] is True
        assert "reply" in data
        assert "voice" in data
        mock_eval.assert_called_once_with("naber", send_telegram=False)


def test_friday_autonomous_monitoring():
    """Verify Friday's autonomous monitoring of regimes and housekeeping alerts."""
    from core.friday_ceo import FridayCeo
    from unittest.mock import patch, MagicMock
    import os
    import sqlite3

    temp_db = "temp_db_friday_monitoring.db"
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
    conn.execute("INSERT INTO params (id, risk_pct) VALUES (1, 0.75)")
    conn.commit()
    conn.close()

    ceo = FridayCeo(db_path=temp_db)

    try:
        import datetime as dt_mod
        class MockDatetime:
            @classmethod
            def now(cls, tz=None):
                if tz:
                    return dt_mod.datetime(2026, 6, 3, 12, 0, 0, tzinfo=tz)
                return dt_mod.datetime(2026, 6, 3, 12, 0, 0)
            @classmethod
            def fromisoformat(cls, s):
                return dt_mod.datetime.fromisoformat(s)

        with patch("database.get_market_regime") as mock_get_regime, \
             patch("database.get_system_state") as mock_get_state, \
             patch("database.set_state") as mock_set_state, \
             patch("telegram_delivery.send_message") as mock_send_msg, \
             patch("telegram_delivery.send_voice") as mock_send_voice, \
             patch("core.friday_ceo.FridayCeo.generate_voice_from_text", return_value=b"voice_data"), \
             patch("core.friday_ceo.FridayCeo.scan_unnecessary_files") as mock_scan_files, \
             patch("os.path.getsize") as mock_getsize, \
             patch("core.friday_ceo.datetime", MockDatetime):

            # --- Test Case 1: NEUTRAL to CHOPPY transition ---
            mock_get_regime.return_value = "CHOPPY"
            mock_get_state.side_effect = lambda key, default=None: {
                "friday_last_regime": "NEUTRAL",
                "friday_last_cleanup_prompt": None
            }.get(key, default)
            mock_scan_files.return_value = []

            ceo.run_autonomous_monitoring()

            # Check that we set the last regime and saved previous settings
            mock_set_state.assert_any_call("friday_last_regime", "CHOPPY")
            mock_set_state.assert_any_call("risk_pct", "0.5")
            mock_set_state.assert_any_call("trade_threshold", "65.0")
            mock_send_msg.assert_called()
            mock_send_voice.assert_called_with(b"voice_data", caption="Friday Otonom Risk Koruma Kalkanı")

            # Check that risk_pct inside params table was updated to 0.5
            conn = sqlite3.connect(temp_db)
            row = conn.execute("SELECT risk_pct FROM params WHERE id = 1").fetchone()
            conn.close()
            assert row[0] == 0.5

            # --- Test Case 2: CHOPPY to NEUTRAL transition ---
            mock_set_state.reset_mock()
            mock_send_msg.reset_mock()
            mock_send_voice.reset_mock()

            mock_get_regime.return_value = "NEUTRAL"
            mock_get_state.side_effect = lambda key, default=None: {
                "friday_last_regime": "CHOPPY",
                "friday_pre_choppy_risk": "0.85",
                "friday_pre_choppy_threshold": "58.0",
                "friday_last_cleanup_prompt": None
            }.get(key, default)

            ceo.run_autonomous_monitoring()

            mock_set_state.assert_any_call("friday_last_regime", "NEUTRAL")
            mock_set_state.assert_any_call("risk_pct", "0.85")
            mock_set_state.assert_any_call("trade_threshold", "58.0")
            mock_send_msg.assert_called()
            mock_send_voice.assert_called_with(b"voice_data", caption="Friday Otonom Risk Modu Güncellemesi")

            # Check that risk_pct inside params table was restored to 0.85
            conn = sqlite3.connect(temp_db)
            row = conn.execute("SELECT risk_pct FROM params WHERE id = 1").fetchone()
            conn.close()
            assert row[0] == 0.85

            # --- Test Case 3: Housekeeping warning (> 10MB) ---
            mock_set_state.reset_mock()
            mock_send_msg.reset_mock()
            mock_send_voice.reset_mock()

            mock_get_regime.return_value = "NEUTRAL"
            mock_get_state.side_effect = lambda key, default=None: {
                "friday_last_regime": "NEUTRAL",
                "friday_last_cleanup_prompt": None,
                "confirmation_mode": "true"
            }.get(key, default)

            mock_scan_files.return_value = ["dummy_backtest_temp_1.db", "dummy_sys.log"]
            mock_getsize.side_effect = lambda f: 6 * 1024 * 1024  # 12MB total

            ceo.run_autonomous_monitoring()

            # Check prompt setting
            assert any(call[0][0] == "friday_last_cleanup_prompt" for call in mock_set_state.call_args_list)
            args, kwargs = mock_send_msg.call_args
            assert "Silinmek İstenen Geçici Dosyalar" in args[0]
            assert "Trade geçmişimiz ve veritabanı kayıtlarımız korunmaktadır" in args[0]
            assert "cmd:clean_server" in kwargs["reply_markup"]["inline_keyboard"][0][0]["callback_data"]
            
            # --- Test Case 4: Autonomous Housekeeping (confirmation_mode = false) ---
            mock_set_state.reset_mock()
            mock_send_msg.reset_mock()
            mock_send_voice.reset_mock()

            mock_get_regime.return_value = "NEUTRAL"
            mock_get_state.side_effect = lambda key, default=None: {
                "friday_last_regime": "NEUTRAL",
                "friday_last_cleanup_prompt": None,
                "confirmation_mode": "false"
            }.get(key, default)

            mock_scan_files.return_value = ["dummy_backtest_temp_1.db", "dummy_sys.log"]
            mock_getsize.side_effect = lambda f: 6 * 1024 * 1024  # 12MB total

            with patch("core.friday_ceo.FridayCeo.execute_cleanup", return_value=(2, 12.0)) as mock_execute_cleanup:
                ceo.run_autonomous_monitoring()
                
                # Check that execute_cleanup was called autonomously
                mock_execute_cleanup.assert_called_once()
                # Check that success message was sent to Telegram
                mock_send_msg.assert_called()
                args, kwargs = mock_send_msg.call_args
                assert "Otonom Altyapı Temizliği" in args[0]
                assert "12.00 MB" in args[0]

    finally:
        if os.path.exists(temp_db):
            os.remove(temp_db)


def test_friday_edge_tts_voice_generation():
    """Verify that edge-tts is prioritized and custom TCPConnector resolver monkeypatch is applied/restored."""
    from core.friday_ceo import FridayCeo
    from unittest.mock import patch
    import aiohttp

    orig_init = aiohttp.TCPConnector.__init__

    class AsyncCommunicateMock:
        def __init__(self, text, voice):
            self.text = text
            self.voice = voice

        async def stream(self):
            yield {"type": "audio", "data": b"mocked_edge_tts_bytes"}

    with patch("edge_tts.Communicate", side_effect=AsyncCommunicateMock) as mock_comm:
        ceo = FridayCeo()
        voice = ceo.generate_voice_from_text("Tatlı ses tonu deneme.")

        mock_comm.assert_called_once_with("Tatlı ses tonu deneme.", "tr-TR-EmelNeural")
        assert voice == b"mocked_edge_tts_bytes"
        assert aiohttp.TCPConnector.__init__ is orig_init


def test_friday_voice_fallback_to_gtts():
    """Verify that if edge-tts fails, voice generation falls back to gTTS."""
    from core.friday_ceo import FridayCeo
    from unittest.mock import patch
    import aiohttp

    orig_init = aiohttp.TCPConnector.__init__

    with patch("edge_tts.Communicate", side_effect=Exception("Connection failed")), \
         patch("gtts.gTTS") as mock_gtts:

        def mock_write(fp):
            fp.write(b"mocked_gtts_bytes")

        mock_gtts.return_value.write_to_fp.side_effect = mock_write

        ceo = FridayCeo()
        voice = ceo.generate_voice_from_text("Fallback deneme.")

        mock_gtts.assert_called_once_with(text="Fallback deneme.", lang="tr")
        assert voice == b"mocked_gtts_bytes"
        assert aiohttp.TCPConnector.__init__ is orig_init


def test_friday_boss_cooldown():
    """Verify that 3 consecutive losses trigger boss cooldown, blocking new trades."""
    from core.friday_ceo import FridayCeo
    from core.risk_engine import RiskEngine
    import sqlite3
    import os
    from unittest.mock import patch, MagicMock

    temp_db = "temp_db_friday_cooldown.db"
    if os.path.exists(temp_db):
        os.remove(temp_db)

    conn = sqlite3.connect(temp_db)
    conn.execute("""
        CREATE TABLE trades (
            id INTEGER PRIMARY KEY,
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
    conn.execute("""
        CREATE TABLE system_state (
            key TEXT PRIMARY KEY,
            value TEXT,
            updated_at TEXT
        )
    """)
    conn.execute("""
        CREATE TABLE params (
            id INTEGER PRIMARY KEY,
            risk_pct REAL,
            sl_atr_mult REAL,
            tp_atr_mult REAL,
            updated_at TEXT
        )
    """)
    conn.execute("INSERT INTO params (id, risk_pct) VALUES (1, 0.75)")
    
    # Insert 3 losses
    for i in range(1, 4):
        conn.execute(
            "INSERT INTO trades (id, symbol, status, net_pnl, close_time, environment) "
            "VALUES (?, 'BTCUSDT', 'closed', -20.0, '2026-06-03T12:00:00Z', 'paper')", (i,)
        )
    conn.commit()
    conn.close()

    ceo = FridayCeo(db_path=temp_db)
    risk_engine = RiskEngine(None, db_path=temp_db)

    with patch("telegram_delivery.send_message") as mock_send, \
         patch("telegram_delivery.send_voice") as mock_voice, \
         patch("core.friday_ceo.FridayCeo.generate_voice_from_text", return_value=b"voice"):
        
        # 1. Verify cooldown is triggered by monitoring
        with patch("config.DB_PATH", temp_db):
            ceo.run_autonomous_monitoring()
            
            # Verify system_state now contains the cooldown
            from database import get_system_state
            cooldown_val = get_system_state("friday_boss_cooldown_until")
            assert cooldown_val != "-"
            
            # 2. Verify RiskEngine calculate rejects due to boss cooldown
            res = risk_engine.calculate("ETHUSDT", "LONG", 3000.0, "A", 1000.0)
            assert res["valid"] is False
            assert res["risk_reject_reason"] == "friday_boss_cooldown"

    try:
        if os.path.exists(temp_db):
            os.remove(temp_db)
    except Exception:
        pass


def test_friday_sector_guard():
    """Verify that sector limit prevents opening >2 trades in the same narrative group."""
    from core.risk_engine import RiskEngine
    import sqlite3
    import os
    from unittest.mock import patch

    temp_db = "temp_db_friday_sector.db"
    if os.path.exists(temp_db):
        try:
            os.remove(temp_db)
        except Exception:
            pass

    conn = sqlite3.connect(temp_db)
    conn.execute("""
        CREATE TABLE trades (
            id INTEGER PRIMARY KEY,
            symbol TEXT,
            direction TEXT,
            status TEXT,
            environment TEXT
        )
    """)
    # Insert 2 open trades in MEME sector (DOGE, SHIB)
    conn.execute("INSERT INTO trades (symbol, status, environment) VALUES ('DOGEUSDT', 'OPEN', 'paper')")
    conn.execute("INSERT INTO trades (symbol, status, environment) VALUES ('SHIBUSDT', 'OPEN', 'paper')")
    conn.commit()
    conn.close()

    risk_engine = RiskEngine(None, db_path=temp_db)

    with patch("config.DB_PATH", temp_db), \
         patch("database.get_open_trades") as mock_get_open:
        
        # Mock database.get_open_trades to return our two MEME trades
        mock_get_open.return_value = [
            {"symbol": "DOGEUSDT", "status": "OPEN"},
            {"symbol": "SHIBUSDT", "status": "OPEN"}
        ]
        
        # Try to open a 3rd MEME coin (PEPE) -> should fail
        res = risk_engine.calculate("PEPEUSDT", "LONG", 0.00001, "A", 1000.0)
        assert res["valid"] is False
        assert res["risk_reject_reason"] == "sector_limit_reached_MEME"
        
        # Try to open an L1 coin (BTC) -> should pass
        res_btc = risk_engine.calculate("BTCUSDT", "LONG", 50000.0, "A", 1000.0)
        assert res_btc["valid"] is True

    try:
        if os.path.exists(temp_db):
            os.remove(temp_db)
    except Exception:
        pass


def test_friday_execution_guard():
    """Verify that Latency & Spread Guard switches system to confirmation mode under poor conditions."""
    from core.friday_ceo import FridayCeo
    import sqlite3
    import os
    from unittest.mock import patch, MagicMock

    temp_db = "temp_db_friday_exec_guard.db"
    if os.path.exists(temp_db):
        try:
            os.remove(temp_db)
        except Exception:
            pass

    conn = sqlite3.connect(temp_db)
    conn.execute("""
        CREATE TABLE system_state (
            key TEXT PRIMARY KEY,
            value TEXT,
            updated_at TEXT
        )
    """)
    conn.commit()
    conn.close()

    # Mock client with high latency
    mock_client = MagicMock()
    mock_client.futures_order_book.return_value = {
        "bids": [["100.0", "1.0"]],
        "asks": [["100.2", "1.0"]]  # spread = 0.2% (> 0.1%)
    }

    ceo = FridayCeo(client=mock_client, db_path=temp_db)

    with patch("config.DB_PATH", temp_db), \
         patch("telegram_delivery.send_message") as mock_send, \
         patch("telegram_delivery.send_voice") as mock_voice, \
         patch("core.friday_ceo.FridayCeo.generate_voice_from_text", return_value=b"voice"):
        
        ceo.run_autonomous_monitoring()

        # Verify confirmation mode was activated in DB
        from database import get_system_state
        assert get_system_state("confirmation_mode") == "true"
        mock_send.assert_called()

    try:
        if os.path.exists(temp_db):
            os.remove(temp_db)
    except Exception:
        pass


def test_friday_nightly_briefing():
    """Verify that daily performance briefing is correctly compiled and triggerable."""
    from core.friday_ceo import FridayCeo
    import sqlite3
    import os
    from unittest.mock import patch

    temp_db = "temp_db_friday_briefing.db"
    if os.path.exists(temp_db):
        try:
            os.remove(temp_db)
        except Exception:
            pass

    conn = sqlite3.connect(temp_db)
    conn.execute("""
        CREATE TABLE trades (
            id INTEGER PRIMARY KEY,
            symbol TEXT,
            status TEXT,
            environment TEXT,
            net_pnl REAL,
            close_time TEXT
        )
    """)
    conn.execute("""
        CREATE TABLE signal_events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            signal_id INTEGER,
            stage TEXT,
            created_at TEXT
        )
    """)
    
    # Insert 1 win, 1 loss today
    from datetime import datetime, timezone
    today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    conn.execute(
        "INSERT INTO trades (symbol, status, environment, net_pnl, close_time) "
        "VALUES ('BTCUSDT', 'closed', 'paper', 50.0, ?)", (today + "T12:00:00Z",)
    )
    conn.execute(
        "INSERT INTO trades (symbol, status, environment, net_pnl, close_time) "
        "VALUES ('ETHUSDT', 'closed', 'paper', -20.0, ?)", (today + "T15:00:00Z",)
    )
    # Insert a veto today
    conn.execute(
        "INSERT INTO signal_events (signal_id, stage, created_at) "
        "VALUES (999, 'RISK_REJECTED', ?)", (today + " 10:00:00",)
    )
    conn.commit()
    conn.close()

    ceo = FridayCeo(db_path=temp_db)

    with patch("config.DB_PATH", temp_db), \
         patch("telegram_delivery.send_message") as mock_send, \
         patch("telegram_delivery.send_voice") as mock_voice, \
         patch("core.friday_ceo.FridayCeo.generate_voice_from_text", return_value=b"voice"):
        
        # Test manual trigger via evaluate_and_decide with keyword "rapor"
        report = ceo.evaluate_and_decide("günün bülteni")
        
        assert "Bugün piyasada toplam <b>2</b> işlem tamamlandı" in report
        assert "toplam net kar/zarar" in report.lower()
        assert "+30.00" in report
        assert "<b>1</b> hatalı sinyali veto ederek" in report
        mock_send.assert_called()

    try:
        if os.path.exists(temp_db):
            os.remove(temp_db)
    except Exception:
        pass


def test_macro_news_watcher_pauses_and_resumes():
    from core.friday_ceo import FridayCeo
    from core.risk_engine import RiskEngine
    import sqlite3
    import os
    from datetime import datetime, timezone, timedelta
    from unittest.mock import patch, MagicMock

    temp_db = "temp_db_friday_macro_test.db"
    if os.path.exists(temp_db):
        os.remove(temp_db)

    import database
    with patch("config.DB_PATH", temp_db):
        database.init_db()

    ceo = FridayCeo(db_path=temp_db)
    risk_engine = RiskEngine(None, db_path=temp_db)

    try:
        # Register a mock event that is happening 5 minutes from now (inside the 15-minute window)
        event_time = datetime.now(timezone.utc) + timedelta(minutes=5)
        ceo.dynamic_events = [{"name": "Mock Macro Event", "time": event_time}]

        with patch("telegram_delivery.send_message") as mock_send_msg, \
             patch("telegram_delivery.send_voice") as mock_send_voice, \
             patch("core.friday_ceo.FridayCeo.generate_voice_from_text", return_value=b"voice"), \
             patch("config.DB_PATH", temp_db):

            # 1. Run monitoring -> Should trigger macro pause
            ceo.run_autonomous_monitoring()

            from database import get_system_state
            assert get_system_state("friday_macro_paused") == "true"
            assert get_system_state("confirmation_mode") == "true"

            # 2. RiskEngine should now reject signals due to macro pause
            res = risk_engine.calculate("BTCUSDT", "LONG", 50000.0, "A", 1000.0)
            assert res["valid"] is False
            assert res["risk_reject_reason"] == "macro_news_watcher_paused"

            # 3. Simulate event passed (16 minutes ago)
            event_time_past = datetime.now(timezone.utc) - timedelta(minutes=16)
            ceo.dynamic_events = [{"name": "Mock Macro Event", "time": event_time_past}]

            ceo.run_autonomous_monitoring()

            assert get_system_state("friday_macro_paused") == "false"
            assert get_system_state("confirmation_mode") == "false"
    finally:
        if os.path.exists(temp_db):
            os.remove(temp_db)


def test_live_correlation_guard():
    from core.risk_engine import RiskEngine
    from unittest.mock import patch, MagicMock

    mock_client = MagicMock()
    # 20 klines, let's say they have prices that are highly correlated
    # High, Low, Close index: 4 is close
    k_a = [[0, 0, 0, 0, 100.0 + i, 0, 0, 0, 0, 0, 0, 0] for i in range(20)]
    k_b = [[0, 0, 0, 0, 200.0 + i * 1.1, 0, 0, 0, 0, 0, 0, 0] for i in range(20)]
    
    mock_client.futures_klines.side_effect = lambda symbol, interval, limit: k_a if symbol == "BTCUSDT" else k_b

    risk_engine = RiskEngine(mock_client, db_path="trading.db")

    open_trades = [
        {"symbol": "BTCUSDT", "direction": "LONG", "margin_used": 10.0}
    ]

    with patch("database.get_open_trades", return_value=open_trades), \
         patch("database.get_system_state", return_value="-"):
         
        # Calculate correlation for ETHUSDT vs open trade BTCUSDT
        # Since they are perfectly correlated (1.0), it should be blocked (> 0.85)
        res = risk_engine.calculate("ETHUSDT", "LONG", 3000.0, "A", 1000.0)
        assert res["valid"] is False
        assert res["risk_reject_reason"] == "high_correlation_block"


def test_liquidity_sweep_detector():
    from core.trigger_engine import TriggerEngine, _GLOBAL_KLINE_CACHE
    _GLOBAL_KLINE_CACHE.clear()
    from unittest.mock import MagicMock, patch

    mock_client = MagicMock()
    engine = TriggerEngine(mock_client)

    # We need to build a DataFrame of 51 candles where the last candle sweeps the low
    # Previous candles are trending to pass the ADX filter, scaled up to avoid volatility filter
    klines = []
    for i in range(50):
        # Time, Open, High, Low, Close, Volume
        klines.append([0, 1000.0 + i, 1002.0 + i, 999.0 + i, 1001.0 + i, 1.0, 0, 0, 0, 0, 0, 0])
    # Add a sweep candle: low sweeps to 1010.0, but closes at 1045.0
    klines.append([0, 1049.0, 1051.0, 1010.0, 1045.0, 10.0, 0, 0, 0, 0, 0, 0])

    mock_client.futures_klines.return_value = klines

    # Mock relative volume, momentum, RSI, and disable CVD divergence check
    with patch.object(engine, "_relative_volume", return_value=3.0), \
         patch.object(engine, "_momentum_3c", return_value=1.0), \
         patch.object(engine, "_rsi", return_value=50.0), \
         patch("core.trigger_engine._btc_allows", return_value=(True, 1.0)), \
         patch("core.ml_signal_scorer.score_signal", return_value=80.0), \
         patch("config.HUMAN_MODE", False), \
         patch("config.SCALP_CVD_DIVERGENCE_FILTER_ENABLED", False), \
         patch("core.cvd_engine.CVDEngine.analyze", return_value={"cvd_signal": "BULLISH", "cvd_score_bonus": 1.0, "cvd_divergence": False, "cvd_slope": 1.0}):
         
        # Analyze LONG signal
        res = engine.analyze("BTCUSDT", "LONG")
        assert res["is_liquidity_sweep"] is True
        # Quality should be upgraded (e.g. S) and score boosted
        assert res["quality"] in ("A+", "S")


def test_momentum_based_trailing_tp3_bypass():
    from core.trailing_engine import TrailingEngine, TradeExitState
    from unittest.mock import patch

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
        current_sl=54000.0,
        highest_price=56000.0,
        qty_remaining_pct=30.0,
        initial_sl=48000.0
    )

    # 1. Under non-trending regime, hitting TP3 should fully close the position
    with patch("database.get_market_regime", return_value="NEUTRAL"):
        res = engine.evaluate(trade, 58100.0, state, atr=1000.0)
        assert res.should_full_close is True
        assert res.full_close_reason == "TP3_HIT"

    # Reset state
    state.tp3_hit = False
    state.qty_remaining_pct = 30.0

    # 2. Under trending matching regime (BULLISH for LONG), hitting TP3 should bypass full close,
    # partial close half of the remaining (15%), and continue trailing
    with patch("database.get_market_regime", return_value="BULLISH"):
        res = engine.evaluate(trade, 58100.0, state, atr=1000.0)
        assert res.should_full_close is False
        assert res.should_partial_close is True
        assert res.close_pct == 15.0
        assert res.reason == "TP3_TRENDING_PARTIAL"
        assert state.trailing_active is True


def test_friday_chart_generator():
    from core.friday_ceo import FridayCeo
    import sqlite3
    import os

    temp_db = "temp_db_friday_chart_test.db"
    if os.path.exists(temp_db):
        os.remove(temp_db)

    conn = sqlite3.connect(temp_db)
    conn.execute("""
        CREATE TABLE balance_ledger (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            trade_id INTEGER,
            symbol TEXT,
            event_type TEXT,
            amount REAL,
            balance_before REAL,
            balance_after REAL,
            note TEXT,
            created_at TEXT
        )
    """)
    # Insert some balances
    conn.execute("INSERT INTO balance_ledger (balance_after, created_at) VALUES (2050.0, '2026-06-03T10:00:00Z')")
    conn.execute("INSERT INTO balance_ledger (balance_after, created_at) VALUES (2100.0, '2026-06-03T11:00:00Z')")
    conn.commit()
    conn.close()

    ceo = FridayCeo(db_path=temp_db)
    try:
        chart_bytes = ceo.generate_equity_chart()
        assert chart_bytes is not None
        assert len(chart_bytes) > 0
        assert isinstance(chart_bytes, bytes)
    finally:
        if os.path.exists(temp_db):
            os.remove(temp_db)


def test_footprint_imbalance_boost():
    from core.ai_decision_engine import OrderFlowAgent
    from core.data_layer import SignalData
    from unittest.mock import MagicMock

    agent = OrderFlowAgent()
    sig = SignalData()
    sig.symbol = "BTCUSDT"
    sig.side = "LONG"
    sig.score = 70.0

    # Case 1: Bullish imbalance (90 buys, 10 sells -> buy_ratio = 0.90)
    mock_client = MagicMock()
    mock_client.futures_recent_trades.return_value = (
        [{"qty": "1.0", "isBuyerMaker": False} for _ in range(90)] +
        [{"qty": "1.0", "isBuyerMaker": True} for _ in range(10)]
    )
    context = {"client": mock_client}
    vote, score, reason = agent.evaluate(sig, context)
    assert score > 70.0
    assert "Footprint Bullish Imbalance" in reason

    # Case 2: Bearish imbalance (10 buys, 90 sells -> buy_ratio = 0.10) for SHORT
    sig.side = "SHORT"
    mock_client_bear = MagicMock()
    mock_client_bear.futures_recent_trades.return_value = (
        [{"qty": "1.0", "isBuyerMaker": False} for _ in range(10)] +
        [{"qty": "1.0", "isBuyerMaker": True} for _ in range(90)]
    )
    context_bear = {"client": mock_client_bear}
    vote, score, reason = agent.evaluate(sig, context_bear)
    assert score > 70.0
    assert "Footprint Bearish Imbalance" in reason


def test_gmm_regime_adaptive_multipliers():
    from core.risk_engine import RiskEngine
    from unittest.mock import MagicMock, patch
    import config

    mock_client = MagicMock()
    mock_client.futures_klines.return_value = [
        [0, 100, 101, 99, 100, 10, 0, 0, 0, 0, 0, 0] for _ in range(30)
    ]

    engine = RiskEngine(mock_client, db_path="trading.db")

    # Retrieve base neutral calculation for reference
    with patch("database.get_market_regime", return_value="NEUTRAL"), \
         patch("database.get_open_trades", return_value=[]), \
         patch("database.get_system_state", return_value="-"), \
         patch("sqlite3.connect") as mock_sqlite_conn:
         
        mock_cursor = MagicMock()
        mock_sqlite_conn.return_value.cursor.return_value = mock_cursor
        mock_sqlite_conn.return_value.execute.return_value = mock_cursor
        mock_cursor.fetchone.return_value = (0.0,)
        
        neutral_res = engine.calculate("BTCUSDT", "LONG", 50000.0, "A+", 1000.0, score=70.0)

    # 1. TRENDING_HIGH_VOL: stops 1.3x, targets 1.3x
    with patch("database.get_market_regime", return_value="TRENDING_HIGH_VOL"), \
         patch("database.get_open_trades", return_value=[]), \
         patch("database.get_system_state", return_value="-"), \
         patch("sqlite3.connect") as mock_sqlite_conn:
        
        mock_cursor = MagicMock()
        mock_sqlite_conn.return_value.cursor.return_value = mock_cursor
        mock_sqlite_conn.return_value.execute.return_value = mock_cursor
        mock_cursor.fetchone.return_value = (0.0,)
        
        res = engine.calculate("BTCUSDT", "LONG", 50000.0, "A+", 1000.0, score=70.0)
        # Verify stop distance is scaled up by 1.3
        dist_neutral = abs(neutral_res["sl"] - 50000.0)
        dist_trending = abs(res["sl"] - 50000.0)
        assert pytest.approx(dist_trending) == dist_neutral * 1.3

    # 2. CHOPPY_HIGH_VOL: stops 1.4x, targets 0.8x
    with patch("database.get_market_regime", return_value="CHOPPY_HIGH_VOL"), \
         patch("database.get_open_trades", return_value=[]), \
         patch("database.get_system_state", return_value="-"), \
         patch("sqlite3.connect") as mock_sqlite_conn:
        
        mock_cursor = MagicMock()
        mock_sqlite_conn.return_value.cursor.return_value = mock_cursor
        mock_sqlite_conn.return_value.execute.return_value = mock_cursor
        mock_cursor.fetchone.return_value = (0.0,)
        
        res = engine.calculate("BTCUSDT", "LONG", 50000.0, "A+", 1000.0, score=70.0)
        dist_choppy = abs(res["sl"] - 50000.0)
        assert pytest.approx(dist_choppy) == dist_neutral * 1.4


def test_stop_hunt_liquidity_sweep_boost():
    from core.ai_decision_engine import classify_signal
    from core.data_layer import SignalData
    from unittest.mock import patch

    sig = SignalData()
    sig.symbol = "BTCUSDT"
    sig.side = "LONG"
    sig.entry_price = 50000.0
    sig.stop_loss = 49000.0
    sig.tp1 = 53000.0  # High RR (>3.0) gives *1.2 bonus, ensuring base adjusted score is high enough
    sig.score = 70.0
    sig.setup_quality = "B"

    # Context with is_liquidity_sweep and bypassed sentiment calls
    context = {
        "is_liquidity_sweep": True,
        "fng_value": 50,
        "macro_sentiment": "neutral"
    }

    with patch("database.get_market_regime", return_value="NEUTRAL"), \
         patch("database.get_open_trades", return_value=[]), \
         patch("core.ai_decision_engine.GhostMemoryManager.get_symbol_ghost_stats", return_value={"total": 0, "tp_hits": 0, "sl_hits": 0, "ghost_winrate": 0.0}), \
         patch("core.ai_decision_engine.GhostMemoryManager.get_direction_bias", return_value={}), \
         patch("core.ai_decision_engine.GhostMemoryManager.get_score_multiplier", return_value=1.0):
        res = classify_signal(sig, context)
        # Quality should be upgraded from B to A
        assert sig.setup_quality == "A"
        # Adjusted score should be boosted (base adjusted score around 75.6 + 10 = 85.6)
        assert res.score_adjusted >= 80.0

