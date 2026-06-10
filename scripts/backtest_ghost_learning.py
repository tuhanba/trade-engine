#!/usr/bin/env python3
"""
scripts/backtest_ghost_learning.py — Ghost Learning Backtest System
==================================================================
Runs historical backtests while simulating both real trades and ghost
signals/results, evaluates win rates, performs reject funnel analysis,
and applies optimized parameters directly to the production database.
"""

import os
import sys
import json
import time
import sqlite3
import logging
import argparse
import math

import datetime as real_datetime

# Global variable to track current simulation time
current_sim_time = None

class SimulatedDatetime(real_datetime.datetime):
    @classmethod
    def now(cls, tz=None):
        if current_sim_time is None:
            return real_datetime.datetime.now(tz)
        if tz is not None and current_sim_time.tzinfo is None:
            return current_sim_time.replace(tzinfo=timezone.utc).astimezone(tz)
        return current_sim_time
    @classmethod
    def utcnow(cls):
        if current_sim_time is None:
            return real_datetime.datetime.utcnow()
        return current_sim_time

datetime = SimulatedDatetime

# Set DB Path and Redis settings before importing other modules
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import config
PROD_DB_PATH = config.DB_PATH
config.DB_PATH = "backtest_temp.db"
config.REDIS_ENABLED = False
config.EXECUTION_MODE = "paper"

import database
from core.data_layer import SignalData, TradeStatus
from core.trend_engine import TrendEngine
from core.trigger_engine import TriggerEngine
from core.ai_decision_engine import AIDecisionEngine
from execution_engine import ExecutionEngine
import core.ghost_learning

# Early monkeypatch online model path for backtesting isolation
import core.online_learning
core.online_learning.MODEL_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
    "core",
    "backtest_sgd_online_model.pkl"
)
core.online_learning._learner_instance = None

# Monkey patch modules to use simulated datetime
database.datetime = SimulatedDatetime
import core.data_layer
core.data_layer.datetime = SimulatedDatetime
import execution_engine
execution_engine.datetime = SimulatedDatetime
import core.trailing_engine
core.trailing_engine.datetime = SimulatedDatetime
import core.risk_engine
core.risk_engine.datetime = SimulatedDatetime
import core.ai_decision_engine
core.ai_decision_engine.datetime = SimulatedDatetime
import core.trigger_engine
core.trigger_engine.datetime = SimulatedDatetime
import core.ghost_learning
core.ghost_learning.datetime = SimulatedDatetime

# Now we can import standard library types
from datetime import timezone, timedelta
import pandas as pd
import numpy as np

# Disable caching in trend and trigger engines to prevent lookahead / time lag
import core.trend_engine
core.trend_engine._GLOBAL_KLINE_TTL = {k: -1 for k in core.trend_engine._GLOBAL_KLINE_TTL}
core.trigger_engine._GLOBAL_KLINE_TTL = {k: -1 for k in core.trigger_engine._GLOBAL_KLINE_TTL}

# Silence real Telegram delivery
import telegram_delivery
telegram_delivery.TelegramDelivery.send_message = lambda self, text, *args, **kwargs: None
telegram_delivery.send_message = lambda text, *args, **kwargs: None

# Disable web sockets
import websocket_events
websocket_events.event_manager = None

# Mock macro market sentiment to be neutral
try:
    from core.services.macro_service import macro_service
    macro_service.get_market_sentiment = lambda: {"fng_value": 50, "bias": "NEUTRAL"}
except Exception:
    pass

# Mock daily risk governor limits to match simulation date
def mock_check_daily_loss_limit(balance, environment="live"):
    try:
        sim_today_str = current_sim_time.strftime("%Y-%m-%d")
        with database.get_conn() as conn:
            row = conn.execute(
                "SELECT COALESCE(SUM(net_pnl), 0) FROM trades "
                "WHERE DATE(close_time) = ? AND status = 'closed'", (sim_today_str,)
            ).fetchone()
        daily_pnl = float(row[0] or 0)
        limit = balance * (config.DAILY_MAX_LOSS_PCT / 100)
        return daily_pnl > -abs(limit)
    except Exception as e:
        return True

core.risk_engine.check_daily_loss_limit = mock_check_daily_loss_limit

logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)s | %(message)s")
logger = logging.getLogger("ax.backtest_ghost")

# Import DataManager and MockBinanceClient from backtest_system if possible, or define them here
# To make this script fully robust and self-contained, we copy DataManager and MockBinanceClient
from scripts.backtest_system import DataManager, MockBinanceClient

# Active Ghost trades in-memory tracker
active_ghosts = {}

# Custom ghost logging to match simulation timestamp and track in-memory excursions
def save_ghost_signal_sim(signal: dict, reason: str) -> int:
    try:
        confidence = float(signal.get("confidence") or signal.get("final_score", 0) / 100)
        _sym = signal.get("symbol") or signal.get("coin") or ""
        
        # Avoid duplicate ghost entries in the same simulated minute
        for g_id, g in active_ghosts.items():
            if g["symbol"] == _sym and g["created_time"] == current_sim_time and g["pattern_type"] == (signal.get("trigger_type") or signal.get("setup_quality") or "UNKNOWN"):
                return g_id

        ghost_id = database.save_ghost_signal({
            "coin":         _sym,
            "symbol":       _sym,
            "side":         signal.get("direction") or signal.get("side", ""),
            "entry_price":  signal.get("entry") or signal.get("entry_zone") or signal.get("entry_price", 0),
            "stop_loss":    signal.get("sl") or signal.get("stop_loss", 0),
            "take_profit":  signal.get("tp1") or signal.get("take_profit", 0),
            "tp1":          signal.get("tp1", 0),
            "tp2":          signal.get("tp2", 0),
            "confidence":   confidence,
            "reject_reason": reason,
            "trigger_type": signal.get("trigger_type") or signal.get("setup_quality") or signal.get("quality", "unknown"),
            "final_score":  signal.get("final_score", 0),
            "market_regime": signal.get("market_regime", "NEUTRAL"),
        })
        
        # Overwrite created_at to simulated time in SQLite
        with database.get_conn() as conn:
            conn.execute(
                "UPDATE ghost_signals SET created_at = ? WHERE id = ?",
                (current_sim_time.isoformat(), ghost_id)
            )
            
        entry = float(signal.get("entry") or signal.get("entry_zone") or signal.get("entry_price", 0))
        sl = float(signal.get("sl") or signal.get("stop_loss", 0))
        tp = float(signal.get("tp1") or signal.get("take_profit", 0))
        risk = max(abs(entry - sl), 1e-6)
        
        active_ghosts[ghost_id] = {
            "symbol": _sym,
            "direction": signal.get("direction") or signal.get("side", ""),
            "entry": entry,
            "sl": sl,
            "tp": tp,
            "risk": risk,
            "mfe": 0.0,
            "mae": 0.0,
            "created_time": current_sim_time,
            "pattern_type": signal.get("trigger_type") or signal.get("setup_quality") or signal.get("quality", "unknown"),
            "metadata": signal.get("metadata") or {}
        }
        return ghost_id
    except Exception as e:
        logger.warning(f"save_ghost_signal_sim error: {e}")
        return 0

# Replace the original maybe_ghost_log and track_skipped_signal with our simulation-aware custom versions
core.ghost_learning.maybe_ghost_log = save_ghost_signal_sim
core.ai_decision_engine.maybe_ghost_log = save_ghost_signal_sim

# Silence original pending simulation since we run minute-by-minute excursion checks in the loop
core.ghost_learning.simulate_pending_ghosts = lambda *args, **kwargs: 0
core.ghost_learning.process_pending_results = lambda *args, **kwargs: 0

class GhostBacktestRunner:
    def __init__(self, symbols, start_time, end_time, initial_balance=2000.0, proxy=None, offline=False, cooldown_mins=30, max_open=3, live_emulation=False):
        self.symbols = symbols
        self.start_time = start_time
        self.end_time = end_time
        self.initial_balance = initial_balance
        self.cooldown_mins = cooldown_mins
        self.max_open = max_open
        
        # Store original configs to restore later (avoid test leak)
        self._orig_execution_mode = getattr(config, "EXECUTION_MODE", "paper")
        self._orig_bypass_shields = getattr(config, "BYPASS_LIVE_RISK_SHIELDS", False)
        
        # Setup Database
        self.db_path = config.DB_PATH
        for suffix in ["", "-wal", "-shm"]:
            p = self.db_path + suffix
            if os.path.exists(p):
                try:
                    os.remove(p)
                except Exception as e:
                    logger.warning(f"Could not delete {p}: {e}")
                
        # Setup clean online model path for backtest
        import core.online_learning
        core.online_learning.MODEL_PATH = os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
            "core",
            "backtest_sgd_online_model.pkl"
        )
        core.online_learning._learner_instance = None
        if os.path.exists(core.online_learning.MODEL_PATH):
            try:
                os.remove(core.online_learning.MODEL_PATH)
            except Exception as e:
                logger.warning(f"Could not delete temporary online model: {e}")
        
        database.init_db()
        database.init_paper_account()
        
        # Configure execution mode and live shields in DB to prevent caching/leak issues
        if live_emulation:
            database.update_system_state("tg_execution_mode", "live")
            database.update_system_state("bypass_live_risk_shields", "false")
            config.EXECUTION_MODE = "live"
            config.BYPASS_LIVE_RISK_SHIELDS = False
        else:
            database.update_system_state("tg_execution_mode", "paper")
            database.update_system_state("bypass_live_risk_shields", "true")
            config.EXECUTION_MODE = "paper"
            config.BYPASS_LIVE_RISK_SHIELDS = True
        
        # Set Custom Initial Balance
        with database.get_conn() as conn:
            conn.execute("UPDATE paper_account SET balance = ?, initial_balance = ? WHERE id = 1", 
                         (initial_balance, initial_balance))
            conn.commit()

        # Initialize Data
        self.data_manager = DataManager(self.symbols + ["BTCUSDT"], start_time, end_time, proxy=proxy, offline=offline)
        self.data_manager.download_all_data()
        self.data_manager.load_dfs()
        
        # Mock Client
        self.mock_client = MockBinanceClient(self.data_manager)
        
        # Engines
        self.trend_engine = TrendEngine(self.mock_client)
        self.trigger_engine = TriggerEngine(self.mock_client)
        self.risk_engine = core.risk_engine.RiskEngine(self.mock_client)
        self.ai_decision_engine = AIDecisionEngine()
        self.execution_engine = ExecutionEngine()
        
        # Override price dynamic mock functions
        self.current_sim_prices = {}
        execution_engine.get_current_price = lambda symbol: self.current_sim_prices.get(symbol, 0.0)
        core.market_data.get_current_price = lambda symbol: self.current_sim_prices.get(symbol, 0.0)
        
        # Cooldown management
        self.cooldown_until = {} # { symbol: datetime }
        
        # Reason counts
        self.funnel_stats = {
            "VETO": 0,
            "WATCH": 0,
            "SKIPPED_BY_LOW_SCORE": 0,
            "SKIPPED_BY_COOLDOWN": 0,
            "SKIPPED_BY_RISK": 0,
            "SKIPPED_BY_MARGIN_RISK": 0
        }
        
    def is_trade_open(self, trade_id):
        trade = database.get_trade_by_id(trade_id)
        return trade and trade["status"].lower() != "closed"

    def run(self):
        try:
            self._run_internal()
        finally:
            config.EXECUTION_MODE = self._orig_execution_mode
            config.BYPASS_LIVE_RISK_SHIELDS = self._orig_bypass_shields

    def _run_internal(self):
        logger.info(f"Starting Ghost simulation from {self.start_time} to {self.end_time}...")
        
        global current_sim_time
        current_sim_time = self.start_time
        
        import scripts.backtest_system
        scripts.backtest_system.current_sim_time = current_sim_time
        
        last_touch = time.perf_counter()
        
        step_minutes = 0
        while current_sim_time <= self.end_time:
            # Touch database file to update mtime and prevent friday_ceo cleanup during long runs
            if time.perf_counter() - last_touch > 60:
                try:
                    os.utime(self.db_path, None)
                except Exception as e:
                    logger.warning(f"Failed to touch db file {self.db_path}: {e}")
                last_touch = time.perf_counter()
            # 1. Update real open trades using 1m candles
            open_trades = database.get_open_trades()
            for t_dict in open_trades:
                symbol = t_dict["symbol"]
                direction = (t_dict.get("direction") or t_dict.get("side", "LONG")).upper()
                trade_id = t_dict["id"]
                
                candle1m = self.data_manager.get_current_1m_candle(symbol)
                if not candle1m:
                    continue
                
                high, low, close = candle1m["high"], candle1m["low"], candle1m["close"]
                
                if direction == "LONG":
                    self.current_sim_prices[symbol] = low
                    self.execution_engine._process_single_trade(t_dict)
                    if self.is_trade_open(trade_id):
                        self.current_sim_prices[symbol] = high
                        fresh_t = database.get_trade_by_id(trade_id)
                        self.execution_engine._process_single_trade(fresh_t)
                        if self.is_trade_open(trade_id):
                            self.current_sim_prices[symbol] = close
                            fresh_t = database.get_trade_by_id(trade_id)
                            self.execution_engine._process_single_trade(fresh_t)
                else:  # SHORT
                    self.current_sim_prices[symbol] = high
                    self.execution_engine._process_single_trade(t_dict)
                    if self.is_trade_open(trade_id):
                        self.current_sim_prices[symbol] = low
                        fresh_t = database.get_trade_by_id(trade_id)
                        self.execution_engine._process_single_trade(fresh_t)
                        if self.is_trade_open(trade_id):
                            self.current_sim_prices[symbol] = close
                            fresh_t = database.get_trade_by_id(trade_id)
                            self.execution_engine._process_single_trade(fresh_t)

            # 2. Update Simulated prices
            for sym in self.symbols:
                price = self.data_manager.get_current_price(sym)
                self.current_sim_prices[sym] = price

            # 3. Process open ghost trades minute-by-minute
            active_ghost_ids = list(active_ghosts.keys())
            for g_id in active_ghost_ids:
                ghost = active_ghosts[g_id]
                symbol = ghost["symbol"]
                direction = ghost["direction"].upper()
                entry = ghost["entry"]
                sl = ghost["sl"]
                tp = ghost["tp"]
                risk = ghost["risk"]
                created_time = ghost["created_time"]
                
                candle1m = self.data_manager.get_current_1m_candle(symbol)
                if not candle1m:
                    continue
                
                high, low, close = candle1m["high"], candle1m["low"], candle1m["close"]
                
                # Check Expiry (12 Hours)
                elapsed_hours = (current_sim_time - created_time).total_seconds() / 3600.0
                if elapsed_hours > core.ghost_learning.EXPIRY_HOURS:
                    database.save_ghost_result(g_id, {
                        "virtual_outcome": "OPEN",
                        "virtual_pnl_r": 0.0,
                        "virtual_mfe": round(ghost["mfe"], 4),
                        "virtual_mae": round(ghost["mae"], 4),
                        "pattern_type": ghost["pattern_type"]
                    })
                    with database.get_conn() as conn:
                        conn.execute("UPDATE ghost_results SET simulated_at = ? WHERE ghost_id = ?",
                                     (current_sim_time.isoformat(), g_id))
                    active_ghosts.pop(g_id, None)
                    continue

                # Excursion & TP/SL Checks
                if direction == "LONG":
                    # Update excursions
                    ghost["mae"] = max(ghost["mae"], (entry - low) / risk)
                    ghost["mfe"] = max(ghost["mfe"], (high - entry) / risk)
                    
                    if low <= sl:
                        database.save_ghost_result(g_id, {
                            "virtual_outcome": "LOSS",
                            "virtual_pnl_r": -1.0,
                            "virtual_mfe": round(ghost["mfe"], 4),
                            "virtual_mae": round(ghost["mae"], 4),
                            "pattern_type": ghost["pattern_type"]
                        })
                        with database.get_conn() as conn:
                            conn.execute("UPDATE ghost_results SET simulated_at = ? WHERE ghost_id = ?",
                                         (current_sim_time.isoformat(), g_id))
                        active_ghosts.pop(g_id, None)
                        # Train online model
                        metadata = ghost.get("metadata")
                        if metadata:
                            try:
                                from core.online_learning import update_online_model
                                update_online_model(metadata, 0)
                            except Exception as e:
                                logger.warning(f"Failed to update online model for ghost {g_id}: {e}")
                    elif high >= tp:
                        pnl_r = round((tp - entry) / risk, 2)
                        database.save_ghost_result(g_id, {
                            "virtual_outcome": "WIN",
                            "virtual_pnl_r": pnl_r,
                            "virtual_mfe": round(ghost["mfe"], 4),
                            "virtual_mae": round(ghost["mae"], 4),
                            "pattern_type": ghost["pattern_type"]
                        })
                        with database.get_conn() as conn:
                            conn.execute("UPDATE ghost_results SET simulated_at = ? WHERE ghost_id = ?",
                                         (current_sim_time.isoformat(), g_id))
                        active_ghosts.pop(g_id, None)
                        # Train online model
                        metadata = ghost.get("metadata")
                        if metadata:
                            try:
                                from core.online_learning import update_online_model
                                update_online_model(metadata, 1)
                            except Exception as e:
                                logger.warning(f"Failed to update online model for ghost {g_id}: {e}")
                else:  # SHORT
                    ghost["mae"] = max(ghost["mae"], (high - entry) / risk)
                    ghost["mfe"] = max(ghost["mfe"], (entry - low) / risk)
                    
                    if high >= sl:
                        database.save_ghost_result(g_id, {
                            "virtual_outcome": "LOSS",
                            "virtual_pnl_r": -1.0,
                            "virtual_mfe": round(ghost["mfe"], 4),
                            "virtual_mae": round(ghost["mae"], 4),
                            "pattern_type": ghost["pattern_type"]
                        })
                        with database.get_conn() as conn:
                            conn.execute("UPDATE ghost_results SET simulated_at = ? WHERE ghost_id = ?",
                                         (current_sim_time.isoformat(), g_id))
                        active_ghosts.pop(g_id, None)
                        # Train online model
                        metadata = ghost.get("metadata")
                        if metadata:
                            try:
                                from core.online_learning import update_online_model
                                update_online_model(metadata, 0)
                            except Exception as e:
                                logger.warning(f"Failed to update online model for ghost {g_id}: {e}")
                    elif low <= tp:
                        pnl_r = round((entry - tp) / risk, 2)
                        database.save_ghost_result(g_id, {
                            "virtual_outcome": "WIN",
                            "virtual_pnl_r": pnl_r,
                            "virtual_mfe": round(ghost["mfe"], 4),
                            "virtual_mae": round(ghost["mae"], 4),
                            "pattern_type": ghost["pattern_type"]
                        })
                        with database.get_conn() as conn:
                            conn.execute("UPDATE ghost_results SET simulated_at = ? WHERE ghost_id = ?",
                                         (current_sim_time.isoformat(), g_id))
                        active_ghosts.pop(g_id, None)
                        # Train online model
                        metadata = ghost.get("metadata")
                        if metadata:
                            try:
                                from core.online_learning import update_online_model
                                update_online_model(metadata, 1)
                            except Exception as e:
                                logger.warning(f"Failed to update online model for ghost {g_id}: {e}")

            # 4. Check candidates & scanner triggers every 5 minutes
            if step_minutes % 5 == 0:
                btc_trend = self.trend_engine.get_btc_trend()
                regime = "NEUTRAL"
                if btc_trend == "BULLISH":
                    regime = "BULLISH"
                elif btc_trend == "BEARISH":
                    regime = "BEARISH"
                else:
                    t1h = self.trend_engine.get_1h_trend("BTCUSDT")
                    t4h = self.trend_engine.get_4h_trend("BTCUSDT")
                    if t1h != "NEUTRAL" and t4h != "NEUTRAL" and t1h != t4h:
                        regime = "CHOPPY"
                    else:
                        df15 = self.trend_engine.get_candles("BTCUSDT", "15m", 30)
                        if not df15.empty:
                            h, l, c = df15["high"], df15["low"], df15["close"]
                            tr = pd.concat([h - l, (h - c.shift()).abs(), (l - c.shift()).abs()], axis=1).max(axis=1)
                            atr_pct = float(tr.rolling(14).mean().iloc[-1]) / float(c.iloc[-1])
                            if atr_pct > 0.015:
                                regime = "CHOPPY"
                database.set_market_regime(regime)
                
                open_trade_symbols = {t["symbol"] for t in database.get_open_trades()}
                balance = database.get_active_balance()
                
                for symbol in self.symbols:
                    if symbol in open_trade_symbols:
                        continue
                    
                    price = self.data_manager.get_current_price(symbol)
                    if price <= 0:
                        continue
                    
                    # 1. Trend analysis
                    trend_res = self.trend_engine.analyze(symbol)
                    direction = trend_res["direction"]
                    if direction == "NO TRADE":
                        continue
                    
                    # Log cooldown skips
                    if symbol in self.cooldown_until and current_sim_time < self.cooldown_until[symbol]:
                        sig_mock = {
                            "symbol": symbol, "direction": direction, "entry": price,
                            "sl": price * 0.985 if direction == "LONG" else price * 1.015,
                            "tp1": price * 1.03 if direction == "LONG" else price * 0.97,
                            "final_score": 50.0, "trigger_type": "unknown", "market_regime": regime
                        }
                        save_ghost_signal_sim(sig_mock, "SKIPPED_BY_COOLDOWN")
                        self.funnel_stats["SKIPPED_BY_COOLDOWN"] += 1
                        continue

                    # 2. Trigger analysis
                    trigger_res = self.trigger_engine.analyze(
                        symbol, direction, trend_res.get("btc_trend", "NEUTRAL"),
                        trend_confluence=trend_res.get("confluence_raw", 1)
                    )
                    
                    if trigger_res["quality"] == "D":
                        sig_mock = {
                            "symbol": symbol, "direction": direction, "entry": price,
                            "sl": price * 0.985 if direction == "LONG" else price * 1.015,
                            "tp1": price * 1.03 if direction == "LONG" else price * 0.97,
                            "final_score": 30.0, "trigger_type": "UNKNOWN", "market_regime": regime
                        }
                        save_ghost_signal_sim(sig_mock, "SKIPPED_BY_LOW_SCORE")
                        self.funnel_stats["SKIPPED_BY_LOW_SCORE"] += 1
                        continue

                    # 3. Risk calculation
                    risk_res = self.risk_engine.calculate(
                        symbol, direction, trigger_res["entry"], trigger_res["quality"], balance
                    )
                    if not risk_res.get("valid"):
                        sig_mock = {
                            "symbol": symbol, "direction": direction, "entry": trigger_res["entry"],
                            "sl": trigger_res["entry"] * 0.985 if direction == "LONG" else trigger_res["entry"] * 1.015,
                            "tp1": trigger_res["entry"] * 1.03 if direction == "LONG" else trigger_res["entry"] * 0.97,
                            "final_score": trigger_res["score"], "trigger_type": trigger_res["quality"], "market_regime": regime
                        }
                        save_ghost_signal_sim(sig_mock, "SKIPPED_BY_RISK")
                        self.funnel_stats["SKIPPED_BY_RISK"] += 1
                        continue

                    # 4. AI Decision check
                    sig = SignalData()
                    sig.symbol = symbol
                    sig.side = direction
                    sig.entry_price = trigger_res["entry"]
                    sig.stop_loss = risk_res["sl"]
                    sig.tp1 = risk_res["tp1"]
                    sig.tp2 = risk_res["tp2"]
                    sig.tp3 = risk_res["tp3"]
                    sig.score = trigger_res["score"]
                    sig.trigger_score = trigger_res["score"]
                    sig.trend_score = trend_res["score"]
                    sig.risk_score = risk_res["score"]
                    sig.leverage = risk_res["leverage"]
                    sig.risk_pct = risk_res["risk_pct"]
                    sig.setup_quality = trigger_res["quality"]
                    sig.final_score = trigger_res["score"]
                    sig.metadata = {
                        "adx": trigger_res.get("adx", 0),
                        "rv": trigger_res.get("rv", 1.0),
                        "rsi5": trigger_res.get("rsi5", 50),
                        "rsi1": trigger_res.get("rsi1", 50),
                        "btc_trend": trigger_res.get("btc_trend", "NEUTRAL"),
                        "ml_score": trigger_res.get("ml_score", 50),
                    }
                    sig.ml_score = trigger_res.get("ml_score", 50)
                    sig.confluence_score = trigger_res.get("confluence_score", 2)
                    sig.position_size = risk_res["position_size"]
                    sig.notional_size = risk_res["notional"]
                    sig.max_loss = risk_res["max_loss"]
                    
                    decision_res = self.ai_decision_engine.evaluate(sig)
                    sig.final_score = decision_res["final_score"]
                    
                    if decision_res["decision"] in ("VETO", "WATCH"):
                        # Already logged to ghost_signals inside AIDecisionEngine evaluate via our hooked save_ghost_signal_sim!
                        self.funnel_stats[decision_res["decision"]] += 1
                        continue
                    
                    # 5. Execution Criteria check
                    is_scalp = not getattr(config, "HUMAN_MODE", False)
                    trade_thr = (config.HUMAN_TRADE_THRESHOLD if not is_scalp else getattr(config, "TRADE_THRESHOLD", 55.0))
                    
                    ml_score = float(sig.ml_score or 50.0)
                    if is_scalp and ml_score >= 65:
                        trade_thr -= 3.0
                    
                    qualities = getattr(config, "EXECUTABLE_QUALITIES", ("S", "A+", "A", "B", "C"))
                    
                    if sig.final_score < trade_thr:
                        save_ghost_signal_sim({
                            "symbol": symbol, "direction": direction, "entry": sig.entry_price,
                            "sl": sig.stop_loss, "tp1": sig.tp1, "final_score": sig.final_score,
                            "trigger_type": sig.setup_quality, "market_regime": regime
                        }, "SKIPPED_BY_LOW_SCORE")
                        self.funnel_stats["SKIPPED_BY_LOW_SCORE"] += 1
                        continue
                        
                    if sig.setup_quality not in qualities:
                        save_ghost_signal_sim({
                            "symbol": symbol, "direction": direction, "entry": sig.entry_price,
                            "sl": sig.stop_loss, "tp1": sig.tp1, "final_score": sig.final_score,
                            "trigger_type": sig.setup_quality, "market_regime": regime
                        }, "SKIPPED_BY_LOW_SCORE")
                        self.funnel_stats["SKIPPED_BY_LOW_SCORE"] += 1
                        continue

                    # Max Position Limit check
                    if len(open_trade_symbols) >= self.max_open:
                        save_ghost_signal_sim({
                            "symbol": symbol, "direction": direction, "entry": sig.entry_price,
                            "sl": sig.stop_loss, "tp1": sig.tp1, "final_score": sig.final_score,
                            "trigger_type": sig.setup_quality, "market_regime": regime
                        }, "SKIPPED_BY_RISK")
                        self.funnel_stats["SKIPPED_BY_RISK"] += 1
                        continue
                        
                    # Margin limits
                    if balance < 100.0 or sig.notional_size > balance:
                        save_ghost_signal_sim({
                            "symbol": symbol, "direction": direction, "entry": sig.entry_price,
                            "sl": sig.stop_loss, "tp1": sig.tp1, "final_score": sig.final_score,
                            "trigger_type": sig.setup_quality, "market_regime": regime
                        }, "SKIPPED_BY_MARGIN_RISK")
                        self.funnel_stats["SKIPPED_BY_MARGIN_RISK"] += 1
                        continue
                    
                    # Open real paper trade
                    trade_id = self.execution_engine.open_paper_trade(sig)
                    if trade_id:
                        logger.info(f"[{current_sim_time.strftime('%Y-%m-%d %H:%M')}] Real Trade Opened: #{trade_id} {symbol} {sig.side} at {sig.entry_price:.4f}")
                        self.cooldown_until[symbol] = current_sim_time + timedelta(minutes=self.cooldown_mins)
                        open_trade_symbols.add(symbol)
            
            # Step minute
            current_sim_time += timedelta(minutes=1)
            import scripts.backtest_system
            scripts.backtest_system.current_sim_time = current_sim_time
            step_minutes += 1
            
        # Clean up any unresolved ghosts
        for g_id, ghost in list(active_ghosts.items()):
            database.save_ghost_result(g_id, {
                "virtual_outcome": "OPEN",
                "virtual_pnl_r": 0.0,
                "virtual_mfe": round(ghost["mfe"], 4),
                "virtual_mae": round(ghost["mae"], 4),
                "pattern_type": ghost["pattern_type"]
            })
            with database.get_conn() as conn:
                conn.execute("UPDATE ghost_results SET simulated_at = ? WHERE ghost_id = ?",
                             (current_sim_time.isoformat(), g_id))
        active_ghosts.clear()
        
        logger.info("Ghost simulation finished successfully.")

    def compile_report(self, output_path="backtest_ghost_report.md"):
        # Real trades statistics
        with database.get_conn() as conn:
            real_trades = conn.execute("SELECT * FROM trades").fetchall()
            ghosts = conn.execute("""
                SELECT g.*, r.virtual_outcome, r.virtual_pnl_r, r.virtual_mfe, r.virtual_mae
                FROM ghost_signals g
                JOIN ghost_results r ON g.id = r.ghost_id
            """).fetchall()
            
        real_list = [dict(t) for t in real_trades]
        ghost_list = [dict(g) for g in ghosts]
        
        # Real Stats
        tot_real = len(real_list)
        real_wins = len([t for t in real_list if t["net_pnl"] > 0])
        real_losses = len([t for t in real_list if t["net_pnl"] <= 0])
        real_wr = (real_wins / tot_real * 100) if tot_real > 0 else 0.0
        real_pnl = sum(t["net_pnl"] for t in real_list)
        
        # Ghost Stats
        tot_ghost = len(ghost_list)
        ghost_wins = len([g for g in ghost_list if g["virtual_outcome"] == "WIN"])
        ghost_losses = len([g for g in ghost_list if g["virtual_outcome"] == "LOSS"])
        tot_closed_ghost = ghost_wins + ghost_losses
        ghost_wr = (ghost_wins / tot_closed_ghost * 100) if tot_closed_ghost > 0 else 0.0
        ghost_pnl_r = sum(g["virtual_pnl_r"] for g in ghost_list)
        avg_ghost_r = (ghost_pnl_r / tot_closed_ghost) if tot_closed_ghost > 0 else 0.0
        
        # Recommendations
        suggestions = core.ghost_learning.generate_threshold_suggestions()
        
        # Funnel Analysis table
        tot_rejects = sum(self.funnel_stats.values())
        funnel_lines = []
        for reason, count in self.funnel_stats.items():
            pct = (count / tot_rejects * 100) if tot_rejects > 0 else 0.0
            funnel_lines.append(f"| {reason} | {count} | {pct:.1f}% |")
            
        # Recommendations table
        rec_lines = []
        for s in suggestions:
            arrow = "⬇️" if s["action"] == "LOWER_THRESHOLD" else "⬆️"
            rec_lines.append(
                f"| **{s['coin']}** | {s['trigger_type']} | {s['current_val']:.0f} | {s['suggested_val']:.0f} {arrow} | "
                f"{s['virtual_wr']:.1f}% | {s['avg_virtual_r']:.2f}R | {s['sample_count']} | {s['confidence']} |"
            )
            
        md = f"""# Ghost Learning & Optimization Backtest Report
This report displays the details of the simulated backtest containing both execution of real trades and learning from rejected candidate signals (Ghost signals).

## 📊 Performance Comparison

| Metric | Real Trades | Ghost Signals |
| :--- | :---: | :---: |
| **Total Counts** | {tot_real} | {tot_ghost} (Simulated: {tot_closed_ghost}) |
| **Win Rate** | **{real_wr:.1f}%** ({real_wins}W / {real_losses}L) | **{ghost_wr:.1f}%** ({ghost_wins}W / {ghost_losses}L) |
| **Net PnL / Return** | **${real_pnl:+.2f}** | **{ghost_pnl_r:+.2f}R** (Avg: {avg_ghost_r:+.2f}R) |
| **Initial Capital** | ${self.initial_balance:.2f} | N/A |
| **Final Capital** | ${database.get_active_balance():.2f} | N/A |

---

## 🌪️ Rejected Candidates Funnel Analysis
Breakdown of skipped signals and reasons for exclusion from active trading.

| Rejection Reason | Count | Percentage |
| :--- | :---: | :---: |
{chr(10).join(funnel_lines)}

---

## 💡 Dynamic Threshold Optimization Suggestions
The following suggestions have been computed based on pattern success (e.g. WR > 60% and avg R > 0.8) of ghost signals.

| Coin | Pattern / Trigger | Current Thr | Suggested Thr | Virtual WR | Avg R | Sample Count | Confidence |
| :--- | :---: | :---: | :---: | :---: | :---: | :---: | :---: |
{chr(10).join(rec_lines) if rec_lines else "| No optimized threshold overrides suggested for this dataset. | | | | | | | |"}

---

## 📝 Top 20 Ghost Signals Log
| ID | Asset | Direction | Entry | Target (TP) | Stop Loss | Outcome | PnL (R) | Excursions (MFE/MAE) | Skip Reason |
| :---: | :--- | :---: | :---: | :---: | :---: | :---: | :---: | :---: | :--- |
"""
        for g in ghost_list[:20]:
            md += (
                f"| #{g['id']} | {g['symbol']} | {g['direction']} | {g['entry_price']:.4f} | "
                f"{g['take_profit']:.4f} | {g['stop_loss']:.4f} | {g['virtual_outcome']} | "
                f"{g['virtual_pnl_r']:+.2f}R | {g['virtual_mfe']:.2f} / {g['virtual_mae']:.2f} | {g['reject_reason']} |\n"
            )
            
        with open(output_path, "w", encoding="utf-8") as f:
            f.write(md)
            
        print("\n" + "=" * 60)
        print("GHOST LEARNING BACKTEST SUMMARY")
        print("=" * 60)
        print(f"Real Trades:  {tot_real} | Win Rate: {real_wr:.1f}% | Net PnL: ${real_pnl:+.2f}")
        print(f"Ghost Trades: {tot_ghost} | Win Rate: {ghost_wr:.1f}% | Net PnL: {ghost_pnl_r:+.2f}R")
        print(f"Suggestions:  {len(suggestions)} threshold override suggestions generated.")
        print(f"Saved premium report to: {output_path}")
        print("=" * 60)

def apply_to_prod_db(temp_db_path, min_confidence="MEDIUM"):
    import core.ghost_learning
    import core.online_learning
    import shutil
    
    logger.info(f"Syncing suggestions from {temp_db_path} to production DB at: {PROD_DB_PATH}")
    if not os.path.exists(PROD_DB_PATH):
        logger.error(f"Production database not found at {PROD_DB_PATH}!")
        return
        
    temp_conn = sqlite3.connect(temp_db_path)
    prod_conn = sqlite3.connect(PROD_DB_PATH)
    
    # 1. Fetch suggestions from temp DB
    suggestions = temp_conn.execute("""
        SELECT symbol, trigger_type, current_threshold, suggested_threshold, 
               virtual_wr, avg_virtual_r, sample_count, confidence, applied 
        FROM ghost_suggestions
    """).fetchall()
    
    threshold_sugs = temp_conn.execute("""
        SELECT coin, trigger_type, action, current_val, suggested_val, expected_trades, confidence, applied
        FROM ghost_threshold_suggestions
    """).fetchall()
    
    temp_conn.close()
    
    # 2. Insert suggestions into production DB (using init_db first to ensure tables exist)
    config.DB_PATH = PROD_DB_PATH
    database.init_db()
    
    with prod_conn:
        # Clear existing unapplied suggestions to avoid duplicating suggestions
        prod_conn.execute("DELETE FROM ghost_suggestions WHERE applied = 0")
        prod_conn.execute("DELETE FROM ghost_threshold_suggestions WHERE applied = 0")
        
        # Insert
        for row in suggestions:
            prod_conn.execute("""
                INSERT INTO ghost_suggestions 
                (symbol, trigger_type, current_threshold, suggested_threshold, virtual_wr, avg_virtual_r, sample_count, confidence, applied)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, row)
            
        for row in threshold_sugs:
            prod_conn.execute("""
                INSERT INTO ghost_threshold_suggestions 
                (coin, trigger_type, action, current_val, suggested_val, expected_trades, confidence, applied)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """, row)
            
    prod_conn.close()
    logger.info("Successfully copied suggestions to production DB.")
    
    # 3. Apply changes autonomously to coin_configs in production
    logger.info("Applying ghost threshold override suggestions directly to coin configs...")
    applied = core.ghost_learning.apply_ghost_suggestions_v2(min_confidence=min_confidence)
    logger.info(f"Otonom update finished: {len(applied)} overrides applied in production coin configs.")
    for change in applied:
        logger.info(f"  Applied override: {change}")

    # 4. Copy temporary model file to production path
    prod_model_path = os.path.join(
        os.path.dirname(core.online_learning.__file__),
        "sgd_online_model.pkl"
    )
    if os.path.exists(core.online_learning.MODEL_PATH):
        try:
            shutil.copy2(core.online_learning.MODEL_PATH, prod_model_path)
            logger.info(f"Successfully copied online model to production at: {prod_model_path}")
        except Exception as e:
            logger.error(f"Failed to copy online model to production: {e}")

def main():
    parser = argparse.ArgumentParser(description="Ghost Learning Backtesting Engine")
    parser.add_argument("--symbols", type=str, default="SOLUSDT,BTCUSDT,ETHUSDT,XRPUSDT,BNBUSDT", 
                        help="Comma-separated symbols to backtest")
    parser.add_argument("--days", type=int, default=30, help="Number of historical days to backtest")
    parser.add_argument("--balance", type=float, default=2000.0, help="Initial paper balance")
    parser.add_argument("--output", type=str, default="backtest_ghost_report.md", help="Path to save markdown report")
    parser.add_argument("--proxy", type=str, default=None, help="HTTP/HTTPS proxy URL (e.g. http://127.0.0.1:7890)")
    parser.add_argument("--offline", action="store_true", help="Run in offline mode using synthetic data")
    parser.add_argument("--cooldown", type=int, default=30, help="Cooldown minutes after opening a real trade")
    parser.add_argument("--max-open", type=int, default=3, help="Maximum number of simultaneous open real trades")
    parser.add_argument("--apply-to-prod", action="store_true", help="Apply optimization results directly to production database")
    parser.add_argument("--min-confidence", type=str, default="MEDIUM", choices=["LOW", "MEDIUM", "HIGH"], 
                        help="Minimum confidence level of suggestions to apply to production")
    parser.add_argument("--live-emulation", action="store_true", 
                        help="Enable live protection shields (clutch, latency, etc.) during backtest. Default is False (paper backtest mode).")
    parser.add_argument("--persist-db", action="store_true", 
                        help="Do not delete the temporary backtest database after execution")
    args = parser.parse_args()

    symbols = [s.strip().upper() for s in args.symbols.split(",")]
    
    end_time = datetime.now(timezone.utc)
    start_time = end_time - timedelta(days=args.days)
    
    # Trim to clean minute boundary
    start_time = start_time.replace(second=0, microsecond=0)
    end_time = end_time.replace(second=0, microsecond=0)
    
    runner = GhostBacktestRunner(
        symbols=symbols,
        start_time=start_time,
        end_time=end_time,
        initial_balance=args.balance,
        proxy=args.proxy,
        offline=args.offline,
        cooldown_mins=args.cooldown,
        max_open=args.max_open,
        live_emulation=args.live_emulation
    )
    runner.run()
    runner.compile_report(args.output)
    
    temp_db_path = runner.db_path
    if args.apply_to_prod:
        apply_to_prod_db(temp_db_path, min_confidence=args.min_confidence)
        
    # Delete temporary database after use to keep workspace clean
    if args.persist_db:
        logger.info(f"Persisting backtest database as requested. Database path: {temp_db_path}")
    else:
        for suffix in ["", "-wal", "-shm"]:
            p = temp_db_path + suffix
            if os.path.exists(p):
                try:
                    os.remove(p)
                except Exception:
                    pass

if __name__ == "__main__":
    main()
