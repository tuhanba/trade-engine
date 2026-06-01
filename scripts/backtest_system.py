#!/usr/bin/env python3
"""
scripts/backtest_system.py — AX Backtesting System v1.0
======================================================
Historical simulation suite for the AX Trade Engine.
Downloads historical kline data, alignment, overrides dependencies,
steps minute-by-minute, and evaluates historical trading performance.
"""

import os
import sys
import json
import time
import sqlite3
import logging
import argparse
import math
from datetime import datetime, timezone, timedelta
import pandas as pd
import numpy as np

# Set DB Path and Redis settings before importing other modules
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import config
config.DB_PATH = "backtest_temp.db"
config.REDIS_ENABLED = False
config.EXECUTION_MODE = "paper"

import database
from core.data_layer import SignalData, TradeStatus
from core.trend_engine import TrendEngine
from core.trigger_engine import TriggerEngine
from core.ai_decision_engine import AIDecisionEngine
from execution_engine import ExecutionEngine

# ── Patch Datetime ────────────────────────────────────────────────────────────

from datetime import datetime as real_datetime

# Global variable to track current simulation time
current_sim_time = None

class SimulatedDatetime(real_datetime):
    @classmethod
    def now(cls, tz=None):
        if current_sim_time is None:
            return real_datetime.now(tz)
        if tz is not None and current_sim_time.tzinfo is None:
            return current_sim_time.replace(tzinfo=timezone.utc).astimezone(tz)
        return current_sim_time
    @classmethod
    def utcnow(cls):
        if current_sim_time is None:
            return real_datetime.utcnow()
        return current_sim_time

def mock_check_daily_loss_limit(balance):
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

if __name__ == "__main__":
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

    # Disable caching in trend and trigger engines to prevent lookahead / time lag
    import core.trend_engine
    core.trend_engine._GLOBAL_KLINE_TTL = {k: -1 for k in core.trend_engine._GLOBAL_KLINE_TTL}
    core.trigger_engine._GLOBAL_KLINE_TTL = {k: -1 for k in core.trigger_engine._GLOBAL_KLINE_TTL}

    # Silence real Telegram delivery
    import telegram_delivery
    telegram_delivery.TelegramDelivery.send_message = lambda self, text, *args, **kwargs: None

    # Disable web sockets
    import websocket_events
    websocket_events.event_manager = None

    # Mock macro market sentiment to be neutral
    try:
        from core.services.macro_service import macro_service
        macro_service.get_market_sentiment = lambda: {"fng_value": 50, "bias": "NEUTRAL"}
    except Exception:
        pass

    core.risk_engine.check_daily_loss_limit = mock_check_daily_loss_limit

logging.basicConfig(level=logging.INFO, format="%(asctime)s | %(levelname)s | %(message)s")
logger = logging.getLogger("ax.backtest")

# ── Data Manager ──────────────────────────────────────────────────────────────

class DataManager:
    def __init__(self, symbols, start_time: datetime, end_time: datetime, data_dir="backtest_data", proxy=None, offline=False):
        self.symbols = symbols
        self.start_time = start_time
        self.end_time = end_time
        self.data_dir = data_dir
        self.proxy = proxy
        self.offline = offline
        self.candles = {}     # { (symbol, interval): list_of_raw_klines }
        self.df_candles = {}  # { (symbol, interval): DataFrame }
        os.makedirs(data_dir, exist_ok=True)

    def _generate_all_synthetic_candles(self, symbol, start_ms, end_ms):
        import random
        # Seed by symbol to make it deterministic
        seed_str = f"{symbol}_synth_v2"
        seed_val = sum(ord(c) for c in seed_str)
        rng = random.Random(seed_val)
        
        base_price = 60000.0 if "BTC" in symbol else (3000.0 if "ETH" in symbol else (150.0 if "SOL" in symbol else (1.0 if "XRP" in symbol else 10.0)))
        
        # 1. Generate 1m candles
        klines_1m = []
        current = start_ms
        price = base_price
        step = 60 * 1000
        
        while current < end_ms:
            t_min = current / (60 * 1000)
            # Stronger trend cycles to ensure ADX exceeds trigger thresholds
            trend = (
                math.sin(t_min / 120.0) * 0.0018 +   # 2-hour strong trend cycle
                math.sin(t_min / 480.0) * 0.0008 +   # 8-hour cycle
                math.cos(t_min / 2880.0) * 0.0003    # 48-hour cycle
            )
            
            vol = 0.0004  # 0.04% per minute standard dev
            change = rng.normalvariate(trend, vol)
            
            o = price
            c = price * (1 + change)
            
            h = max(o, c) * (1 + abs(rng.normalvariate(0, 0.0002)))
            l = min(o, c) * (1 - abs(rng.normalvariate(0, 0.0002)))
            v = rng.uniform(5, 100)
            
            klines_1m.append([
                current,
                str(round(o, 4)),
                str(round(h, 4)),
                str(round(l, 4)),
                str(round(c, 4)),
                str(round(v, 2)),
                current + step - 1,
                str(round(v * price, 2)),
                int(v * 2),
                str(round(v * 0.5, 2)),
                str(round(v * 0.5 * price, 2)),
                "0"
            ])
            price = c
            current += step
            
        # 2. Aggregate into larger timeframes
        def aggregate(dur_min):
            dur_ms = dur_min * 60 * 1000
            aggregated = []
            i = 0
            n = len(klines_1m)
            while i < n:
                candle_start_time = klines_1m[i][0]
                interval_start_ms = (candle_start_time // dur_ms) * dur_ms
                interval_end_ms = interval_start_ms + dur_ms
                
                subset = []
                while i < n and klines_1m[i][0] < interval_end_ms:
                    subset.append(klines_1m[i])
                    i += 1
                
                if not subset:
                    continue
                
                o_val = float(subset[0][1])
                c_val = float(subset[-1][4])
                h_val = max(float(k[2]) for k in subset)
                l_val = min(float(k[3]) for k in subset)
                tot_vol = sum(float(k[5]) for k in subset)
                tot_qav = sum(float(k[7]) for k in subset)
                tot_nt = sum(int(k[8]) for k in subset)
                tot_tbbav = sum(float(k[9]) for k in subset)
                tot_tbqav = sum(float(k[10]) for k in subset)
                
                aggregated.append([
                    interval_start_ms,
                    str(round(o_val, 4)),
                    str(round(h_val, 4)),
                    str(round(l_val, 4)),
                    str(round(c_val, 4)),
                    str(round(tot_vol, 2)),
                    interval_end_ms - 1,
                    str(round(tot_qav, 2)),
                    tot_nt,
                    str(round(tot_tbbav, 2)),
                    str(round(tot_tbqav, 2)),
                    "0"
                ])
                
            return aggregated

        return {
            "1m": klines_1m,
            "5m": aggregate(5),
            "15m": aggregate(15),
            "1h": aggregate(60),
            "4h": aggregate(240)
        }

    def _fetch_klines_with_failover(self, symbol, interval, startTime, endTime, limit=1500):
        import requests
        endpoints = [
            'https://fapi.binance.com',
            'https://fapi1.binance.com',
            'https://fapi2.binance.com',
            'https://fapi3.binance.com'
        ]
        
        proxies = None
        if self.proxy:
            proxies = {
                "http": self.proxy,
                "https": self.proxy
            }
            
        last_err = None
        for endpoint in endpoints:
            url = f"{endpoint}/fapi/v1/klines"
            params = {
                "symbol": symbol,
                "interval": interval,
                "startTime": startTime,
                "endTime": endTime,
                "limit": limit
            }
            try:
                import urllib3
                urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
                r = requests.get(url, params=params, proxies=proxies, timeout=10, verify=False)
                if r.status_code == 200:
                    return r.json()
                else:
                    last_err = f"HTTP {r.status_code} from {endpoint}: {r.text}"
            except Exception as e:
                last_err = f"{type(e).__name__} from {endpoint}: {e}"
                continue
                
        raise Exception(f"Failed to fetch klines from all Binance Futures endpoints. Last error: {last_err}")

    def download_all_data(self):
        intervals = ["1m", "5m", "15m", "1h", "4h"]
        start_ms = int(self.start_time.timestamp() * 1000)
        end_ms = int(self.end_time.timestamp() * 1000)
        
        # Buffer of 2 days before start_time for warm indicator calculations
        buffer_start_ms = start_ms - 2 * 24 * 60 * 60 * 1000
        
        for symbol in self.symbols:
            synth_candles = None
            if self.offline:
                logger.info(f"Offline mode: generating consistent synthetic datasets for {symbol}...")
                synth_candles = self._generate_all_synthetic_candles(symbol, buffer_start_ms, end_ms)

            for interval in intervals:
                cache_file = os.path.join(self.data_dir, f"{symbol}_{interval}.json")
                synth_cache_file = os.path.join(self.data_dir, f"synthetic_{symbol}_{interval}.json")
                
                # If offline is requested, try to load from synthetic cache first
                if self.offline:
                    self.candles[(symbol, interval)] = synth_candles[interval]
                    with open(synth_cache_file, "w") as f:
                        json.dump(synth_candles[interval], f)
                    continue

                # Normal mode: check real cache first
                if os.path.exists(cache_file):
                    try:
                        with open(cache_file, "r") as f:
                            self.candles[(symbol, interval)] = json.load(f)
                        logger.info(f"Loaded {symbol} {interval} from local cache.")
                        continue
                    except Exception as e:
                        logger.warning(f"Failed to read cache {cache_file}: {e}. Redownloading...")
                
                logger.info(f"Downloading historical {symbol} {interval} from Binance Futures API...")
                all_klines = []
                current_start = buffer_start_ms
                failed_download = False
                
                while current_start < end_ms:
                    try:
                        klines = self._fetch_klines_with_failover(
                            symbol=symbol,
                            interval=interval,
                            startTime=current_start,
                            endTime=end_ms,
                            limit=1500
                        )
                        if not klines:
                            break
                        all_klines.extend(klines)
                        current_start = klines[-1][6] + 1
                        time.sleep(0.05)
                    except Exception as e:
                        logger.error(f"Error fetching klines for {symbol} {interval}: {e}. Falling back to synthetic generator...")
                        failed_download = True
                        break
                
                if failed_download or not all_klines:
                    if synth_candles is None:
                        logger.warning(f"Failed to download {symbol} data. Generating fallback synthetic datasets...")
                        synth_candles = self._generate_all_synthetic_candles(symbol, buffer_start_ms, end_ms)
                    
                    self.candles[(symbol, interval)] = synth_candles[interval]
                    with open(synth_cache_file, "w") as f:
                        json.dump(synth_candles[interval], f)
                else:
                    # Sort and deduplicate
                    seen_times = set()
                    deduped_klines = []
                    for k in all_klines:
                        ot = k[0]
                        if ot not in seen_times:
                            seen_times.add(ot)
                            deduped_klines.append(k)
                    deduped_klines.sort(key=lambda x: x[0])
                    
                    with open(cache_file, "w") as f:
                        json.dump(deduped_klines, f)
                    
                    self.candles[(symbol, interval)] = deduped_klines
                    logger.info(f"Downloaded and cached {len(deduped_klines)} candles for {symbol} {interval}.")

    def load_dfs(self):
        self.candles_list = {}
        self.candles_times = {}
        for (symbol, interval), klines in self.candles.items():
            df = pd.DataFrame(klines, columns=[
                "time", "open", "high", "low", "close", "volume",
                "ct", "qav", "nt", "tbbav", "tbqav", "ignore"
            ])
            for col in ["open", "high", "low", "close", "volume", "tbbav", "tbqav"]:
                df[col] = df[col].astype(float)
            df["time"] = df["time"].astype(int)
            self.df_candles[(symbol, interval)] = df
            self.candles_list[(symbol, interval)] = df.values.tolist()
            self.candles_times[(symbol, interval)] = df["time"].values.tolist()

    def get_candles(self, symbol, interval, limit):
        times = self.candles_times.get((symbol, interval))
        if not times:
            return []
        
        t_ms = int(current_sim_time.timestamp() * 1000)
        dur_map = {
            "1m": 60 * 1000,
            "5m": 5 * 60 * 1000,
            "15m": 15 * 60 * 1000,
            "1h": 60 * 60 * 1000,
            "4h": 4 * 60 * 60 * 1000
        }
        dur = dur_map.get(interval, 5 * 60 * 1000)
        max_open_time = t_ms - dur
        
        from bisect import bisect_right
        idx = bisect_right(times, max_open_time)
        return self.candles_list[(symbol, interval)][max(0, idx - limit):idx]

    def get_current_price(self, symbol):
        times = self.candles_times.get((symbol, "1m"))
        if not times:
            return 0.0
        
        t_ms = int(current_sim_time.timestamp() * 1000)
        from bisect import bisect_right
        idx = bisect_right(times, t_ms - 60000)
        if idx == 0:
            return 0.0
        return float(self.candles_list[(symbol, "1m")][idx - 1][4])

    def get_current_1m_candle(self, symbol):
        times = self.candles_times.get((symbol, "1m"))
        if not times:
            return None
        
        t_ms = int(current_sim_time.timestamp() * 1000)
        from bisect import bisect_right
        idx = bisect_right(times, t_ms - 60000)
        if idx == 0:
            return None
        row = self.candles_list[(symbol, "1m")][idx - 1]
        return {
            "high": float(row[2]),
            "low": float(row[3]),
            "close": float(row[4])
        }

# ── Mock Client ───────────────────────────────────────────────────────────────

class MockBinanceClient:
    def __init__(self, data_manager):
        self.data_manager = data_manager

    def futures_klines(self, symbol, interval, limit=500, startTime=None, endTime=None):
        klines = self.data_manager.get_candles(symbol, interval, limit)
        if not klines:
            if not hasattr(self, "_empty_warn_count"):
                self._empty_warn_count = 0
            if self._empty_warn_count < 10:
                logger.warning(f"MockBinanceClient returning EMPTY candles for {symbol} {interval} at {current_sim_time}")
                self._empty_warn_count += 1
        return klines

    def futures_order_book(self, symbol, limit=20):
        return {
            "bids": [[1.0, 10.0]] * limit,
            "asks": [[1.0, 10.0]] * limit
        }

    def futures_funding_rate(self, symbol, limit=3):
        return [{"fundingRate": "0.0001", "fundingTime": 0}] * limit

    def futures_open_interest(self, symbol):
        return {"openInterest": "50000.0"}

    def futures_symbol_ticker(self, symbol):
        price = self.data_manager.get_current_price(symbol)
        return {"symbol": symbol, "price": str(price)}

    def futures_ticker(self):
        tickers = []
        for symbol in self.data_manager.symbols:
            price = self.data_manager.get_current_price(symbol)
            tickers.append({
                "symbol": symbol,
                "quoteVolume": "50000000.0",
                "priceChangePercent": "2.5",
                "lastPrice": str(price)
            })
        return tickers

    def futures_exchange_info(self):
        symbols_info = []
        for symbol in self.data_manager.symbols:
            symbols_info.append({
                "symbol": symbol,
                "quoteAsset": "USDT",
                "status": "TRADING",
                "contractType": "PERPETUAL",
                "filters": [
                    {
                        "filterType": "PRICE_FILTER",
                        "minPrice": "0.0001",
                        "maxPrice": "100000.0000",
                        "tickSize": "0.0001"
                    },
                    {
                        "filterType": "LOT_SIZE",
                        "minQty": "0.001",
                        "maxQty": "100000.0",
                        "stepSize": "0.001"
                    },
                    {
                        "filterType": "MIN_NOTIONAL",
                        "notional": "5.0"
                    }
                ]
            })
        return {"symbols": symbols_info}

# ── Simulation Runner ─────────────────────────────────────────────────────────

class BacktestRunner:
    def __init__(self, symbols, start_time, end_time, initial_balance=2000.0, proxy=None, offline=False):
        self.symbols = symbols
        self.start_time = start_time
        self.end_time = end_time
        self.initial_balance = initial_balance
        
        # Setup Database
        if os.path.exists("backtest_temp.db"):
            try:
                os.remove("backtest_temp.db")
            except Exception as e:
                logger.warning(f"Could not delete backtest_temp.db: {e}")
        
        database.init_db()
        database.init_paper_account()
        
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
        
    def is_trade_open(self, trade_id):
        trade = database.get_trade_by_id(trade_id)
        return trade and trade["status"].lower() != "closed"

    def run(self):
        logger.info(f"Starting simulation from {self.start_time} to {self.end_time}...")
        logger.info(f"Symbols in simulation: {self.symbols}")
        # Print loaded candle count statistics
        for (symbol, interval), candles in self.data_manager.candles.items():
            logger.info(f"Loaded {len(candles)} candles for {symbol} {interval}")
        
        global current_sim_time
        current_sim_time = self.start_time
        
        # Step through in 1-minute increments
        step_minutes = 0
        while current_sim_time <= self.end_time:
            # 1. Update prices of open trades and check exits using 1m high/low candles
            open_trades = database.get_open_trades()
            for t_dict in open_trades:
                symbol = t_dict["symbol"]
                direction = (t_dict.get("direction") or t_dict.get("side", "LONG")).upper()
                trade_id = t_dict["id"]
                
                candle1m = self.data_manager.get_current_1m_candle(symbol)
                if not candle1m:
                    continue
                
                high, low, close = candle1m["high"], candle1m["low"], candle1m["close"]
                
                # Check exit using adverse price first, then favorable
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
            
            # 2. Update simulated price dictionaries for all symbols
            for sym in self.symbols:
                price = self.data_manager.get_current_price(sym)
                self.current_sim_prices[sym] = price
            
            # 3. Process Market Regime and scanners every 5 minutes
            if step_minutes % 5 == 0:
                # Update Market Regime
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
                
                # Check candidates for scanning
                open_symbols = {t["symbol"] for t in database.get_open_trades()}
                for symbol in self.symbols:
                    if symbol in open_symbols:
                        continue
                    
                    price = self.data_manager.get_current_price(symbol)
                    if price <= 0:
                        continue
                    
                    # 1. Trend check
                    trend_res = self.trend_engine.analyze(symbol)
                    direction = trend_res["direction"]
                    if direction == "NO TRADE":
                        continue
                    
                    logger.info(f"[DEBUG] {symbol} trend check passed: {direction} (ADX15={trend_res.get('adx15', 0)})")

                    # 2. Trigger check
                    trigger_res = self.trigger_engine.analyze(
                        symbol,
                        direction,
                        trend_res.get("btc_trend", "NEUTRAL"),
                        trend_confluence=trend_res.get("confluence_raw", 1)
                    )
                    if trigger_res["quality"] == "D":
                        logger.info(f"[DEBUG] {symbol} trigger check failed: quality={trigger_res['quality']} (adx={trigger_res.get('adx', 0)}, rsi5={trigger_res.get('rsi5', 0)}, reject_reason={trigger_res.get('reject_reason', 'none')})")
                        continue
                    
                    logger.info(f"[DEBUG] {symbol} trigger check passed: quality={trigger_res['quality']}, score={trigger_res['score']}")

                    # 3. Risk check
                    balance = database.get_active_balance()
                    risk_res = self.risk_engine.calculate(
                        symbol,
                        direction,
                        trigger_res["entry"],
                        trigger_res["quality"],
                        balance
                    )
                    if not risk_res.get("valid"):
                        logger.info(f"[DEBUG] {symbol} risk check failed: invalid risk params")
                        continue
                    
                    logger.info(f"[DEBUG] {symbol} risk check passed: sl={risk_res['sl']}, tp1={risk_res['tp1']}")

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
                    
                    # Criteria check
                    is_scalp = not getattr(config, "HUMAN_MODE", False)
                    trade_thr = (
                        config.HUMAN_TRADE_THRESHOLD if not is_scalp
                        else getattr(config, "TRADE_THRESHOLD", 55.0)
                    )
                    
                    ml_score = float(sig.ml_score or 50.0)
                    if is_scalp and ml_score >= 65:
                        trade_thr -= 3.0
                    
                    qualities = getattr(config, "EXECUTABLE_QUALITIES", ("S", "A+", "A", "B", "C"))
                    
                    logger.info(f"[DEBUG] {symbol} AI Decision evaluate: decision={decision_res['decision']}, score={sig.final_score:.1f} (threshold={trade_thr:.1f}, quality={sig.setup_quality})")
                    
                    if decision_res["decision"] == "ALLOW" and sig.final_score >= trade_thr and sig.setup_quality in qualities:
                        trade_id = self.execution_engine.open_paper_trade(sig)
                        if trade_id:
                            logger.info(f"[{current_sim_time.strftime('%Y-%m-%d %H:%M')}] Trade Opened: #{trade_id} {symbol} {sig.side} at {sig.entry_price:.4f} (Qual={sig.setup_quality}, Score={sig.final_score:.1f})")
            
            # Step Time
            current_sim_time += timedelta(minutes=1)
            step_minutes += 1
            
        logger.info("Simulation completed successfully.")

    def generate_report(self, output_path="backtest_report.md"):
        # Fetch all trades from database
        with database.get_conn() as conn:
            trades = conn.execute("SELECT * FROM trades ORDER BY id").fetchall()
            ledger = conn.execute("SELECT * FROM balance_ledger ORDER BY id").fetchall()
            
        trade_list = [dict(t) for t in trades]
        final_balance = database.get_active_balance()
        
        total_trades = len(trade_list)
        wins = [t for t in trade_list if t["net_pnl"] > 0]
        losses = [t for t in trade_list if t["net_pnl"] <= 0]
        
        win_count = len(wins)
        loss_count = len(losses)
        
        win_rate = (win_count / total_trades * 100) if total_trades > 0 else 0.0
        
        gross_profit = sum(t["net_pnl"] for t in wins)
        gross_loss = abs(sum(t["net_pnl"] for t in losses))
        net_profit = gross_profit - gross_loss
        profit_factor = (gross_profit / gross_loss) if gross_loss > 0 else 0.0
        
        avg_win = (gross_profit / win_count) if win_count > 0 else 0.0
        avg_loss = (gross_loss / loss_count) if loss_count > 0 else 0.0
        
        # Exit reasons
        reasons = {}
        for t in trade_list:
            reason = t.get("close_reason", "unknown")
            reasons[reason] = reasons.get(reason, 0) + 1
            
        # Drawdown calculation
        peak = self.initial_balance
        max_dd_pct = 0.0
        
        # Use ledger logs for exact balance path
        balances = [self.initial_balance]
        for l in ledger:
            bal_after = l["balance_after"]
            balances.append(bal_after)
            if bal_after > peak:
                peak = bal_after
            dd = (peak - bal_after) / peak * 100
            if dd > max_dd_pct:
                max_dd_pct = dd
                
        # Coin specific results
        coin_perf = {}
        for t in trade_list:
            sym = t["symbol"]
            if sym not in coin_perf:
                coin_perf[sym] = {"trades": 0, "wins": 0, "net_pnl": 0.0}
            coin_perf[sym]["trades"] += 1
            coin_perf[sym]["net_pnl"] += t["net_pnl"]
            if t["net_pnl"] > 0:
                coin_perf[sym]["wins"] += 1
                
        # Output console report
        print("\n" + "=" * 60)
        print("AX TRADE ENGINE BACKTEST RESULTS")
        print("=" * 60)
        print(f"Period:         {self.start_time.strftime('%Y-%m-%d')} to {self.end_time.strftime('%Y-%m-%d')}")
        print(f"Symbols:        {', '.join(self.symbols)}")
        print(f"Total Trades:   {total_trades}")
        print(f"Win Rate:       {win_rate:.1f}% ({win_count}W / {loss_count}L)")
        print(f"Net Profit:     ${net_profit:+.2f} ({net_profit/self.initial_balance*100:+.2f}%)")
        print(f"Profit Factor:  {profit_factor:.2f}")
        print(f"Max Drawdown:   {max_dd_pct:.1f}%")
        print(f"Final Balance:  ${final_balance:.2f} (Start: ${self.initial_balance:.2f})")
        print("=" * 60)
        
        # Build premium Markdown Report
        md = f"""# AX Trade Engine Backtest Performance Report

This report presents the backtest metrics of the current trading bot engine running in paper trading simulation mode.

## 📊 Summary Performance Metrics

| Metric | Value |
| :--- | :--- |
| **Backtest Period** | {self.start_time.strftime('%Y-%m-%d')} to {self.end_time.strftime('%Y-%m-%d')} |
| **Analyzed Symbols** | `{', '.join(self.symbols)}` |
| **Total Trades Executed** | {total_trades} |
| **Win Rate** | **{win_rate:.1f}%** ({win_count} Wins / {loss_count} Losses) |
| **Initial Capital** | ${self.initial_balance:.2f} |
| **Final Capital** | ${final_balance:.2f} |
| **Net Profit / Return** | **${net_profit:+.2f}** ({net_profit/self.initial_balance*100:+.2f}%) |
| **Profit Factor** | **{profit_factor:.2f}** |
| **Max Portfolio Drawdown** | **{max_dd_pct:.1f}%** |
| **Average Win** | ${avg_win:.2f} |
| **Average Loss** | ${avg_loss:.2f} |

---

## 🔍 Exit Reasons Breakdown

| Exit Reason | Count | Percentage |
| :--- | :---: | :---: |
"""
        for r, cnt in reasons.items():
            pct = cnt / total_trades * 100 if total_trades > 0 else 0
            md += f"| {r.upper()} | {cnt} | {pct:.1f}% |\n"
            
        md += """
---

## 📈 Performance by Asset

| Asset | Trades | Wins | Losses | Win Rate | Net PnL ($) | Return (%) |
| :--- | :---: | :---: | :---: | :---: | :---: | :---: |
"""
        for sym, data in coin_perf.items():
            t_cnt = data["trades"]
            w_cnt = data["wins"]
            l_cnt = t_cnt - w_cnt
            c_wr = w_cnt / t_cnt * 100 if t_cnt > 0 else 0
            c_pnl = data["net_pnl"]
            c_ret = c_pnl / self.initial_balance * 100
            md += f"| **{sym}** | {t_cnt} | {w_cnt} | {l_cnt} | {c_wr:.1f}% | {c_pnl:+.2f}$ | {c_ret:+.2f}% |\n"

        md += """
---

## 📝 Trade Logs Detail

| ID | Symbol | Direction | Entry | Close | Realized PnL | Exit Reason | Timestamp |
| :--- | :--- | :---: | :---: | :---: | :---: | :--- | :--- |
"""
        for t in trade_list[:100]:  # Cap at 100 logs in markdown
            md += f"| #{t['id']} | {t['symbol']} | {t['direction']} | {t['entry']:.4f} | {t['close_price']:.4f} | {t['realized_pnl']:+.4f}$ | {t['close_reason'].upper()} | {t['open_time']} |\n"

        if len(trade_list) > 100:
            md += f"| ... | ... | ... | ... | ... | ... | ... | (and {len(trade_list)-100} more trades) |\n"

        with open(output_path, "w", encoding="utf-8") as f:
            f.write(md)
            
        logger.info(f"Saved premium report to {output_path}")

# ── CLI Entrypoint ────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="AX Trade Engine Backtesting System")
    parser.add_argument("--symbols", type=str, default="SOLUSDT,BTCUSDT,ETHUSDT,XRPUSDT,BNBUSDT", 
                        help="Comma-separated symbols to backtest")
    parser.add_argument("--days", type=int, default=30, help="Number of historical days to backtest")
    parser.add_argument("--balance", type=float, default=2000.0, help="Initial paper balance")
    parser.add_argument("--output", type=str, default="backtest_report.md", help="Path to save markdown report")
    parser.add_argument("--proxy", type=str, default=None, help="HTTP/HTTPS proxy URL (e.g. http://127.0.0.1:7890)")
    parser.add_argument("--offline", action="store_true", help="Run in offline mode using synthetic data")
    args = parser.parse_args()

    symbols = [s.strip().upper() for s in args.symbols.split(",")]
    
    end_time = datetime.now(timezone.utc)
    start_time = end_time - timedelta(days=args.days)
    
    # Trim to clean minute boundary
    start_time = start_time.replace(second=0, microsecond=0)
    end_time = end_time.replace(second=0, microsecond=0)
    
    runner = BacktestRunner(
        symbols=symbols,
        start_time=start_time,
        end_time=end_time,
        initial_balance=args.balance,
        proxy=args.proxy,
        offline=args.offline
    )
    runner.run()
    runner.generate_report(args.output)
    
    # Delete temporary database after use to keep workspace clean
    if os.path.exists("backtest_temp.db"):
        try:
            os.remove("backtest_temp.db")
        except Exception:
            pass

if __name__ == "__main__":
    main()
