import unittest
import os
import sys
import sqlite3
import json
import asyncio
from unittest.mock import MagicMock, AsyncMock, patch
from datetime import datetime, timezone, timedelta

sys.path.insert(0, '.')

import config
import database
from database import init_db, update_system_state, get_system_state, get_market_regime
from core.data_layer import SignalData, TradeData, TradeStatus
from execution_engine import ExecutionEngine, parse_utc_datetime
from core.trailing_engine import TrailingEngine, TradeExitState
from core.event_bus import event_bus
from core.event_types import Event, EventType
from websocket_events import event_manager

class TestIntegrationFlow(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        init_db()

    def setUp(self):
        # Fresh state database cleanups
        with database.get_conn() as conn:
            conn.execute("DELETE FROM trades")
            conn.execute("DELETE FROM system_state")
            conn.execute("DELETE FROM params WHERE id = 1")
            conn.execute("INSERT OR IGNORE INTO params (id) VALUES (1)")
        
        # Reset websocket event manager mocks
        event_manager.broadcast_trailing_stop_updated = MagicMock()
        event_manager.broadcast_pnl_update = MagicMock()
        event_manager.broadcast_live_update = MagicMock()
        event_manager._publish_event = MagicMock()

    def test_dynamic_risk_sizing_regime_and_score(self):
        """1. Test Dynamic Risk Sizing logic in core/accounting.py based on final score and regime."""
        from core.accounting import build_trade_from_signal
        
        # Setup market regime in system_state table
        update_system_state("market_regime", "CHOPPY")
        self.assertEqual(get_market_regime(), "CHOPPY")

        # Test Case A: Score 50 in CHOPPY market (penalty score + penalty regime)
        sig_choppy = SignalData(
            symbol="BTCUSDT",
            side="LONG",
            entry_price=50000.0,
            stop_loss=49500.0,
            tp1=51000.0,
            risk_pct=1.0,  # 1% base risk
            leverage=10,
            final_score=50.0
        )
        sig_choppy.metadata = {"market_regime": "CHOPPY"}

        with patch('core.market_data.get_book_ticker', return_value=None):
            trade_choppy = build_trade_from_signal(sig_choppy, balance=10000.0)
        self.assertIsNotNone(trade_choppy)
        # Expected:
        # base_risk * (score/75.0) = 1.0 * (50/75) = 0.667
        # Penalty score <= 55: * 0.6 = 0.40
        # Regime CHOPPY: * 0.5 = 0.20
        # Expected risk = 0.2%
        self.assertAlmostEqual(trade_choppy.risk_pct, 0.2)

        # Test Case B: Score 85 in BULLISH market (boost score + boost regime)
        update_system_state("market_regime", "BULLISH")
        sig_bullish = SignalData(
            symbol="BTCUSDT",
            side="LONG",
            entry_price=50000.0,
            stop_loss=49500.0,
            tp1=51000.0,
            risk_pct=1.0,
            leverage=10,
            final_score=85.0
        )
        sig_bullish.metadata = {"market_regime": "BULLISH"}

        with patch('core.market_data.get_book_ticker', return_value=None):
            trade_bullish = build_trade_from_signal(sig_bullish, balance=10000.0)
        self.assertIsNotNone(trade_bullish)
        # Expected:
        # base_risk * (score/75) = 1.0 * (85/75) = 1.133
        # Boost score >= 80: * 1.3 = 1.473
        # Regime BULLISH: * 1.1 = 1.62
        # Expected dynamic_risk ~= 1.62%
        self.assertGreater(trade_bullish.risk_pct, 1.5)
        self.assertLess(trade_bullish.risk_pct, 1.7)

    def test_scalp_take_profit_optimizer(self):
        """2. Test Spread & Fee-Aware Take Profit Optimizer in core/accounting.py."""
        from core.accounting import build_trade_from_signal
        
        # We enable config variables for scalp optimizer
        with patch.object(config, 'HUMAN_MODE', False), \
             patch.object(config, 'SCALP_TP_OPTIMIZER_ENABLED', True), \
             patch.object(config, 'MIN_TP_FEE_SPREAD_RATIO', 2.5), \
             patch.object(config, 'DEFAULT_FEE_RATE', 0.0004):
             
             # Mock book ticker return spread = 10.0 (e.g. ask=50005, bid=49995)
             mock_book = {"askPrice": "50005.0", "bidPrice": "49995.0"}
             
             with patch('core.market_data.get_book_ticker', return_value=mock_book):
                 # Sinyal TP1 is very tight: entry=50000, tp1=50010 (0.02% difference)
                 # Fee: round-trip = 50000 * 0.0004 * 2 = 40.0 USD
                 # Spread: ask - bid = 10.0 USD
                 # Min profit diff required = (40.0 + 10.0) * 2.5 = 125.0 USD
                 # Adjusted TP1 must be at least 50000 + 125 = 50125.0
                 sig = SignalData(
                     symbol="BTCUSDT",
                     side="LONG",
                     entry_price=50000.0,
                     stop_loss=49500.0,
                     tp1=50010.0,  # Tight target
                     tp2=50020.0,
                     risk_pct=1.0,
                     leverage=10,
                     final_score=75.0
                 )
                 
                 trade = build_trade_from_signal(sig, balance=10000.0)
                 self.assertIsNotNone(trade)
                 self.assertGreaterEqual(trade.tp1, 50125.0)
                 self.assertGreater(trade.tp2, trade.tp1)

    def test_standard_time_decay_sl_tightening(self):
        """3. Test standard trade Time Decay Stop Loss tightening."""
        # 1. Open a paper trade in database
        trade_id = 101
        symbol = "BTCUSDT"
        
        # Set trade time to 75 minutes ago (elapsed = 75 minutes)
        # Standard time decay starts at 45m and breakeven is at 105m.
        # Window = 60m. elapsed - 45 = 30m. decay_factor = 30/60 = 0.5.
        # Entry = 100.0, SL = 90.0, Initial SL = 90.0, side = LONG.
        # Decayed SL = 90.0 + (100.0 - 90.0) * 0.5 = 95.0.
        open_time = (datetime.now(timezone.utc) - timedelta(minutes=75)).strftime("%Y-%m-%d %H:%M:%S")
        
        # Write state to metadata
        initial_state = TradeExitState(
            current_sl=90.0,
            highest_price=100.0,
            initial_sl=90.0,
            is_scalp=False
        )
        metadata_json = json.dumps(initial_state.to_dict())

        with database.get_conn() as conn:
            conn.execute("""
                INSERT INTO trades (id, symbol, direction, status, entry, sl, qty, open_time, metadata)
                VALUES (?, ?, 'LONG', 'OPEN', 100.0, 90.0, 1.0, ?, ?)
            """, (trade_id, symbol, open_time, metadata_json))

        # 2. Evaluate trade via execution_engine
        engine = ExecutionEngine()
        
        # Mock get_current_price and calculate_unrealized_pnl
        with patch('execution_engine.get_current_price', return_value=105.0), \
             patch('execution_engine.calculate_unrealized_pnl', return_value=5.0), \
             patch.object(config, 'TIME_DECAY_ENABLED', True):
            
            # Retrieve trade and run update
            trade = database.get_open_trades()[0]
            engine._process_single_trade(trade)
            
            # Assert database SL is updated to 95.0
            updated_trade = database.get_open_trades()[0]
            self.assertAlmostEqual(updated_trade["sl"], 95.0, delta=1.0)
            
            # Assert WebSocket event was broadcast
            event_manager.broadcast_trailing_stop_updated.assert_called_once()
            args = event_manager.broadcast_trailing_stop_updated.call_args[1]
            self.assertEqual(args["symbol"], symbol)
            self.assertEqual(args["trade_id"], trade_id)
            self.assertAlmostEqual(args["old_sl"], 90.0, delta=1.0)
            self.assertAlmostEqual(args["new_sl"], 95.0, delta=1.0)

    def test_scalp_time_decay_sl_tightening(self):
        """4. Test scalp trade Time Decay Stop Loss tightening (shorter limits)."""
        trade_id = 102
        symbol = "ETHUSDT"
        
        # Set trade time to 10 minutes ago (elapsed = 10 minutes)
        # Scalp decay starts at 5m and breakeven is at 15m.
        # Window = 10m. elapsed - 5 = 5m. decay_factor = 5/10 = 0.5.
        # Entry = 3000.0, SL = 2900.0, side = SHORT.
        # Decayed SL = 2900.0 - (2900.0 - 3000.0) * 0.5 = 2950.0.
        open_time = (datetime.now(timezone.utc) - timedelta(minutes=10)).strftime("%Y-%m-%d %H:%M:%S")
        
        initial_state = TradeExitState(
            current_sl=3100.0,
            highest_price=3000.0,
            initial_sl=3100.0,
            is_scalp=True
        )
        metadata_json = json.dumps(initial_state.to_dict())

        with database.get_conn() as conn:
            conn.execute("""
                INSERT INTO trades (id, symbol, direction, status, entry, sl, qty, open_time, metadata)
                VALUES (?, ?, 'SHORT', 'OPEN', 3000.0, 3100.0, 1.0, ?, ?)
            """, (trade_id, symbol, open_time, metadata_json))

        engine = ExecutionEngine()
        
        with patch('execution_engine.get_current_price', return_value=2980.0), \
             patch('execution_engine.calculate_unrealized_pnl', return_value=20.0), \
             patch.object(config, 'TIME_DECAY_ENABLED', True):
            
            # Find the open scalp trade and process it
            open_trades = database.get_open_trades()
            trade = [t for t in open_trades if t["id"] == trade_id][0]
            engine._process_single_trade(trade)
            
            # Assert database SL is updated to 3050.0 (3100 + (3000 - 3100) * 0.5 = 3050.0)
            open_trades_after = database.get_open_trades()
            updated_trade = [t for t in open_trades_after if t["id"] == trade_id][0]
            self.assertAlmostEqual(updated_trade["sl"], 3050.0, delta=1.0)
            
            # Assert WebSocket event was broadcast
            event_manager.broadcast_trailing_stop_updated.assert_called()
            # The last call should be for trade_id 102
            call = event_manager.broadcast_trailing_stop_updated.mock_calls[-1]
            args = call[2]
            self.assertEqual(args["trade_id"], trade_id)
            self.assertAlmostEqual(args["old_sl"], 3100.0, delta=1.0)
            self.assertAlmostEqual(args["new_sl"], 3050.0, delta=1.0)

    def test_end_to_end_event_bus_integration(self):
        """5. Test end-to-end integration with the Event Bus."""
        # Setup event receiver
        received_events = []
        
        async def mock_handler(event: Event):
            received_events.append(event)
            
        event_bus.subscribe(EventType.TRADE_CLOSED, mock_handler)
        
        async def run_test():
            # Start event bus
            await event_bus.start()
            
            # Simulate a trade closing in ExecutionEngine
            trade_id = 103
            symbol = "SOLUSDT"
            
            with database.get_conn() as conn:
                conn.execute("""
                    INSERT INTO trades (id, symbol, direction, status, entry, sl, qty, open_time, metadata, net_pnl, risk_usd)
                    VALUES (?, ?, 'LONG', 'OPEN', 150.0, 140.0, 1.0, '2026-06-01 00:00:00', '{}', 0.0, 10.0)
                """, (trade_id, symbol))
                
            from core.services.execution_service import ExecutionService
            exec_svc = ExecutionService()
            
            # Execute monitoring check
            # We mock ExecutionEngine updates to close the trade
            trade = {"id": trade_id, "symbol": symbol, "direction": "LONG", "entry": 150.0, "qty": 1.0, "status": "OPEN"}
            
            with patch.object(exec_svc.execution_engine, 'update_open_trades') as mock_update:
                def side_effect_close():
                    # Close trade in DB
                    with database.get_conn() as conn:
                        conn.execute("UPDATE trades SET status = 'CLOSED', close_reason = 'STOP_LOSS', net_pnl = -10.0 WHERE id = ?", (trade_id,))
                
                mock_update.side_effect = side_effect_close
                
                # Run monitoring iteration
                # We can call the monitoring method logic manually to avoid infinite loop
                exec_svc.execution_engine.update_open_trades()
                
                # Check for closed trades and publish events
                # Simulate monitoring step of checking difference
                exec_svc_open = {t["id"] for t in database.get_open_trades()}
                # It was open initially, now closed. We simulate closed detection
                closed_ids = {trade_id} - exec_svc_open
                
                for c_id in closed_ids:
                    closed = database.get_trade_by_id(c_id)
                    _pnl = float(closed.get("net_pnl") or 0)
                    await event_bus.publish(Event(
                        type=EventType.TRADE_CLOSED,
                        payload={
                            "trade_id": c_id,
                            "symbol": closed.get("symbol"),
                            "net_pnl": _pnl,
                            "reason": closed.get("close_reason")
                        }
                    ))
            
            # Allow event bus to process the event
            await asyncio.sleep(0.5)
            await event_bus.stop()
            
        asyncio.run(run_test())
        
        # Verify event was published and processed
        self.assertEqual(len(received_events), 1)
        self.assertEqual(received_events[0].type, EventType.TRADE_CLOSED)
        self.assertEqual(received_events[0].payload["trade_id"], 103)
        self.assertEqual(received_events[0].payload["symbol"], "SOLUSDT")
        self.assertEqual(received_events[0].payload["net_pnl"], -10.0)

if __name__ == '__main__':
    unittest.main()
