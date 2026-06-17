"""
core/backtest_engine.py - C2: Backtest Engine
Reads historical signals, fetches klines, and simulates using TrailingEngine.
"""
from __future__ import annotations

import logging
import json
import requests
from datetime import datetime, timezone, timedelta
from typing import List, Dict, Any

from core.trailing_engine import TrailingEngine, TradeExitState
import database

logger = logging.getLogger("ax.backtest_engine")

class BacktestEngine:
    def __init__(self):
        self.trailing_engine = TrailingEngine()
        self._init_db()

    def _init_db(self):
        """Creates the backtest_results table if it doesn't exist."""
        ddl = """
        CREATE TABLE IF NOT EXISTS backtest_results (
            id SERIAL PRIMARY KEY,
            signal_id INTEGER,
            symbol TEXT,
            direction TEXT,
            entry_price REAL,
            close_price REAL,
            pnl_pct REAL,
            close_reason TEXT,
            duration_bars INTEGER,
            metadata TEXT,
            created_at TEXT DEFAULT (NOW())
        )
        """
        try:
            with database.get_conn() as conn:
                conn.execute(ddl)
        except Exception as e:
            # Fallback to SQLite syntax if Postgres fails
            if "syntax error" in str(e).lower() or "serial" in str(e).lower():
                ddl_sqlite = """
                CREATE TABLE IF NOT EXISTS backtest_results (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    signal_id INTEGER,
                    symbol TEXT,
                    direction TEXT,
                    entry_price REAL,
                    close_price REAL,
                    pnl_pct REAL,
                    close_reason TEXT,
                    duration_bars INTEGER,
                    metadata TEXT,
                    created_at TEXT DEFAULT (CURRENT_TIMESTAMP)
                )
                """
                try:
                    with database.get_conn() as conn2:
                        conn2.execute(ddl_sqlite)
                except Exception as e2:
                    logger.error(f"Error creating backtest_results table (fallback): {e2}")
            else:
                logger.error(f"Error creating backtest_results table: {e}")

    def get_historical_signals(self, limit: int = 50) -> List[Dict[str, Any]]:
        """Reads past signals from signal_candidates."""
        query = """
            SELECT id, symbol, side, direction, entry_price, entry, stop_loss, sl, tp1, tp2, tp3, created_at, atr
            FROM signal_candidates
            WHERE status != 'PENDING'
            ORDER BY id DESC
            LIMIT ?
        """
        try:
            with database.get_conn() as conn:
                rows = conn.execute(query, (limit,)).fetchall()
            return rows
        except Exception as e:
            # Some databases might expect %s instead of ?
            try:
                query_pg = query.replace('?', '%s')
                with database.get_conn() as conn2:
                    rows = conn2.execute(query_pg, (limit,)).fetchall()
                return rows
            except Exception as e2:
                logger.error(f"Error fetching historical signals: {e2}")
                return []

    def fetch_klines(self, symbol: str, start_time_ms: int, limit: int = 500) -> List[List[Any]]:
        """Fetches historical OHLCV data from Binance public API."""
        url = "https://fapi.binance.com/fapi/v1/klines"
        params = {
            "symbol": symbol,
            "interval": "5m",
            "startTime": start_time_ms,
            "limit": limit
        }
        try:
            resp = requests.get(url, params=params, timeout=15)
            resp.raise_for_status()
            return resp.json()
        except Exception as e:
            logger.error(f"Failed to fetch klines for {symbol}: {e}")
            return []

    def run_backtest(self, limit: int = 50):
        """Runs the backtest simulation over the historical signals."""
        signals = self.get_historical_signals(limit)
        logger.info(f"Loaded {len(signals)} signals for backtesting.")
        
        for sig in signals:
            try:
                self._simulate_signal(sig)
            except Exception as e:
                logger.error(f"Error simulating signal {sig.get('id')}: {e}", exc_info=True)
                
    def _simulate_signal(self, sig: Dict[str, Any]):
        sig_id = sig.get("id")
        symbol = sig.get("symbol")
        direction = sig.get("direction") or sig.get("side") or "LONG"
        entry = float(sig.get("entry_price") or sig.get("entry") or 0)
        sl = float(sig.get("stop_loss") or sig.get("sl") or 0)
        tp1 = float(sig.get("tp1") or 0)
        tp2 = float(sig.get("tp2") or 0)
        tp3 = float(sig.get("tp3") or 0)
        atr = float(sig.get("atr") or 0)
        created_at = sig.get("created_at")

        if not symbol or entry <= 0:
            logger.warning(f"Invalid signal data for ID {sig_id}")
            return

        # Determine start time
        try:
            if isinstance(created_at, str):
                clean_time = created_at.replace("Z", "+00:00")
                if "+" not in clean_time and "-" not in clean_time[10:]:
                    clean_time += "+00:00" # assume UTC
                dt = datetime.fromisoformat(clean_time)
                start_time_ms = int(dt.timestamp() * 1000)
            else:
                start_time_ms = int(datetime.now(timezone.utc).timestamp() * 1000) - (24 * 3600 * 1000)
        except Exception:
            start_time_ms = int(datetime.now(timezone.utc).timestamp() * 1000) - (24 * 3600 * 1000)

        klines = self.fetch_klines(symbol, start_time_ms)
        if not klines:
            logger.warning(f"No klines fetched for {symbol} starting at {created_at}")
            return

        # Prepare for simulation
        trade = {
            "id": f"BT_{sig_id}",
            "symbol": symbol,
            "direction": direction,
            "entry": entry,
            "sl": sl,
            "tp1": tp1,
            "tp2": tp2,
            "tp3": tp3,
            "open_time": created_at
        }

        state = TradeExitState(
            initial_sl=sl,
            current_sl=sl,
            highest_price=entry,
            qty_remaining_pct=100.0,
            is_scalp=False
        )
        
        close_reason = "TIMEOUT"
        close_price = entry
        is_closed = False
        bars_held = 0
        realized_pnl_pct = 0.0

        logger.info(f"Starting simulation for Signal {sig_id} ({symbol} {direction}) Entry: {entry}")

        for kline in klines:
            if is_closed:
                break
            
            bars_held += 1
            timestamp = int(kline[0])
            open_p = float(kline[1])
            high_p = float(kline[2])
            low_p  = float(kline[3])
            close_p = float(kline[4])

            # Simulate tick data conservatively by feeding prices in specific order
            if direction.upper() == "LONG":
                prices = [open_p, low_p, high_p, close_p]
            else:
                prices = [open_p, high_p, low_p, close_p]

            # Mock elapsed time so trailing engine time-decay logic works properly
            elapsed_minutes = max(0, (timestamp - start_time_ms) / 60000.0)
            fake_open_dt = datetime.now(timezone.utc) - timedelta(minutes=elapsed_minutes)
            trade["open_time"] = fake_open_dt.isoformat()

            for p in prices:
                if is_closed:
                    break
                
                result = self.trailing_engine.evaluate(trade, p, state, atr)

                if result.should_partial_close:
                    pct_closed = result.close_pct / 100.0
                    price_diff = (result.close_at_price - entry) / entry
                    if direction.upper() == "SHORT":
                        price_diff = -price_diff
                    realized_pnl_pct += pct_closed * price_diff
                    
                    logger.info(f"[BT_{sig_id}] Partial close: {result.reason} at {result.close_at_price}")

                if result.should_full_close:
                    pct_closed = state.qty_remaining_pct / 100.0
                    price_diff = (result.close_at_price - entry) / entry
                    if direction.upper() == "SHORT":
                        price_diff = -price_diff
                    realized_pnl_pct += pct_closed * price_diff
                    
                    close_reason = result.full_close_reason
                    close_price = result.close_at_price
                    state.qty_remaining_pct = 0.0
                    is_closed = True
                    logger.info(f"[BT_{sig_id}] Full close: {close_reason} at {close_price}. PnL: {realized_pnl_pct:.2%}")

        if not is_closed:
            # Force close at the end of the data
            pct_closed = state.qty_remaining_pct / 100.0
            last_close = float(klines[-1][4])
            price_diff = (last_close - entry) / entry
            if direction.upper() == "SHORT":
                price_diff = -price_diff
            realized_pnl_pct += pct_closed * price_diff
            close_price = last_close
            logger.info(f"[BT_{sig_id}] Closed by timeout at {close_price}. PnL: {realized_pnl_pct:.2%}")

        self._save_result(sig_id, symbol, direction, entry, close_price, realized_pnl_pct, close_reason, bars_held, state)
        
    def _save_result(self, sig_id, symbol, direction, entry, close_price, pnl_pct, close_reason, duration_bars, state: TradeExitState):
        metadata = json.dumps(state.to_dict())
        query = """
            INSERT INTO backtest_results 
            (signal_id, symbol, direction, entry_price, close_price, pnl_pct, close_reason, duration_bars, metadata)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """
        try:
            with database.get_conn() as conn:
                conn.execute(query, (sig_id, symbol, direction, entry, close_price, pnl_pct, close_reason, duration_bars, metadata))
        except Exception as e:
            # Fallback for Postgres %s parameterization
            query = query.replace('?', '%s')
            try:
                with database.get_conn() as conn2:
                    conn2.execute(query, (sig_id, symbol, direction, entry, close_price, pnl_pct, close_reason, duration_bars, metadata))
            except Exception as e2:
                logger.error(f"Error saving backtest result: {e2}")

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
    engine = BacktestEngine()
    engine.run_backtest(limit=10)
