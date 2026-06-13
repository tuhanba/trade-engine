"""
core/hyperparameter_tuner.py — Automated Parameter Optimizer using Optuna
========================================================================

Runs an optimization study over the closed trade history to adaptively tune:
1. `sl_atr_mult`
2. `tp_atr_mult`
3. `trade_threshold`
"""

import logging
import sqlite3
import optuna
import config
from database import update_system_state

logger = logging.getLogger("ax.tuner")

def get_closed_trades() -> list:
    """Fetch closed trades from the database for simulation."""
    trades = []
    try:
        # NEDEN (Faz 1.2): WAL/busy_timeout disiplini için database.open_db
        from database import open_db
        with open_db(config.DB_PATH) as conn:
            cursor = conn.execute("""
                SELECT symbol, direction, entry, sl, realized_pnl, net_pnl, qty, final_score, mfe, mae
                FROM trades
                WHERE status = 'closed' AND entry > 0
            """)
            trades = [dict(row) for row in cursor.fetchall()]
    except Exception as e:
        logger.error(f"[Tuner] Error fetching closed trades: {e}")
    return trades

def optimize_parameters():
    """Run Optuna study on closed trade history and save optimized parameters."""
    logger.info("[Tuner] Starting parameter tuning optimization...")
    trades = get_closed_trades()
    
    if len(trades) < 5:
        logger.warning(f"[Tuner] Not enough closed trades to optimize parameters safely (found {len(trades)}/5). Skipping.")
        return

    # Turn off Optuna logs to prevent cluttering stdout
    optuna.logging.set_verbosity(optuna.logging.WARNING)

    # Reconstruct current SL ATR MULT from config
    current_sl_mult = getattr(config, "SL_ATR_MULT", 1.2) or 1.2

    def objective(trial):
        # 1. Define Search Space
        sl_atr_mult = trial.suggest_float("sl_atr_mult", 0.5, 3.0, step=0.1)
        tp_atr_mult = trial.suggest_float("tp_atr_mult", 1.0, 5.0, step=0.1)
        trade_threshold = trial.suggest_float("trade_threshold", 45.0, 75.0, step=1.0)

        simulated_pnl = 0.0

        for t in trades:
            score = float(t.get("final_score") or 50.0)
            if score < trade_threshold:
                # Signal would have been filtered out by the threshold; no trade taken
                continue

            entry = float(t.get("entry") or 0)
            sl = float(t.get("sl") or 0)
            qty = float(t.get("qty") or 0)
            net_pnl = float(t.get("net_pnl") or t.get("realized_pnl") or 0)
            mae = float(t.get("mae") or 0)
            mfe = float(t.get("mfe") or 0)

            # Reconstruct entry ATR percentage
            sl_dist = abs(entry - sl)
            sl_pct = sl_dist / entry if entry > 0 else 0
            if sl_pct == 0:
                sl_pct = 0.015 # default 1.5% SL fallback

            # Reconstruct ATR pct based on current mult
            atr_pct = sl_pct / current_sl_mult

            # Target SL & TP percentages for this trial
            sim_sl_pct = atr_pct * sl_atr_mult
            sim_tp_pct = atr_pct * tp_atr_mult

            # Trade Outcome Simulation
            if mae >= sim_sl_pct:
                # Hit stop loss first
                simulated_pnl -= sim_sl_pct * entry * qty
            elif mfe >= sim_tp_pct:
                # Hit take profit
                simulated_pnl += sim_tp_pct * entry * qty
            else:
                # Neither hit, count as actual closed PnL
                simulated_pnl += net_pnl

        return simulated_pnl

    try:
        study = optuna.create_study(direction="maximize")
        study.optimize(objective, n_trials=50)

        best_params = study.best_params
        best_value = study.best_value
        logger.info(f"[Tuner] Optimization finished. Best simulated PnL: {best_value:.4f} USD. Parameters: {best_params}")

        # NEDEN (Faz 3.2): Optuna sonuçları da backtest gate'inden geçer —
        # her parametre ayrı ayrı doğrulanır; reddedilen mevcut değerinde kalır.
        try:
            from core.param_gate import validate_param_change
            cur_sl = getattr(config, "SL_ATR_MULT", 1.8)
            cur_tp = getattr(config, "TP2_R", 2.5)
            cur_thr = getattr(config, "TRADE_THRESHOLD", 55.0)

            ok_sl, rep_sl = validate_param_change("sl_atr_mult", cur_sl, best_params["sl_atr_mult"])
            ok_tp, rep_tp = validate_param_change("tp_atr_mult", cur_tp, best_params["tp_atr_mult"])
            ok_thr, rep_thr = validate_param_change("trade_threshold", cur_thr, best_params["trade_threshold"])

            if not ok_sl:
                logger.warning("[Tuner/Gate] sl_atr_mult reddedildi: %s", rep_sl.get("reason"))
                best_params["sl_atr_mult"] = cur_sl
            if not ok_tp:
                logger.warning("[Tuner/Gate] tp_atr_mult reddedildi: %s", rep_tp.get("reason"))
                best_params["tp_atr_mult"] = cur_tp
            if not ok_thr:
                logger.warning("[Tuner/Gate] trade_threshold reddedildi: %s", rep_thr.get("reason"))
                best_params["trade_threshold"] = cur_thr
        except Exception as _gate_err:
            logger.debug("[Tuner/Gate] gate kontrolü atlandı: %s", _gate_err)

        # Update in database
        # NEDEN (Faz 1.2): WAL/busy_timeout disiplini için database.open_db
        try:
            from database import open_db
            with open_db(config.DB_PATH) as conn:
                # Update params table (ID = 1)
                conn.execute("""
                    UPDATE params SET
                        sl_atr_mult = ?,
                        tp_atr_mult = ?,
                        updated_at = datetime('now')
                    WHERE id = 1
                """, (best_params["sl_atr_mult"], best_params["tp_atr_mult"]))
                conn.commit()

            # Update system_state for trade_threshold
            update_system_state("trade_threshold", str(round(best_params["trade_threshold"], 1)))

            logger.info("[Tuner] Successfully saved optimized parameters to the database.")

            # Emit WebSocket broadcast to refresh the dashboard
            try:
                from websocket_events import event_manager
                if event_manager:
                    event_manager.broadcast_dashboard_refresh()
                    logger.debug("[Tuner] Emitted dashboard refresh event after optimization.")
            except Exception as _ws_err:
                logger.debug(f"[Tuner] WebSocket emit skipped: {_ws_err}")

        except Exception as db_err:
            logger.error(f"[Tuner] Database write error: {db_err}")

    except Exception as opt_err:
        logger.error(f"[Tuner] Optuna optimization failed: {opt_err}")


def check_win_rate_and_trigger_opt(db_path: str) -> bool:
    """
    Checks the win rate of the last 20 closed trades.
    Returns True if the win rate is below 50%.
    """
    try:
        # NEDEN (Faz 1.2): WAL/busy_timeout disiplini için database.open_db
        from database import open_db
        with open_db(db_path) as conn:
            cursor = conn.execute("""
                SELECT net_pnl, realized_pnl
                FROM trades
                WHERE status = 'closed'
                ORDER BY id DESC
                LIMIT 20
            """)
            rows = [dict(r) for r in cursor.fetchall()]


        if len(rows) < 20:
            logger.debug(f"[Tuner] Not enough closed trades to check win-rate (found {len(rows)}/20).")
            return False
            
        wins = sum(1 for r in rows if float(r.get("net_pnl") or r.get("realized_pnl") or 0.0) > 0.0)
        win_rate = wins / len(rows)
        logger.info(f"[Tuner] Win rate of last 20 closed trades is {win_rate:.2f} ({wins} wins).")
        return win_rate < 0.50
    except Exception as e:
        logger.error(f"[Tuner] Error checking win rate: {e}")
        return False


def get_simulated_ghosts(db_path: str) -> list:
    """Fetch simulated ghosts for optimization."""
    try:
        # NEDEN (Faz 1.2): WAL/busy_timeout disiplini için database.open_db
        from database import open_db
        with open_db(db_path) as conn:
            cursor = conn.execute("""
                SELECT g.direction, g.side, g.rsi, g.cvd_slope, r.virtual_pnl_r
                FROM ghost_signals g
                JOIN ghost_results r ON g.id = r.ghost_id
                WHERE r.virtual_outcome IN ('WIN', 'LOSS')
            """)
            rows = [dict(r) for r in cursor.fetchall()]
        return rows
    except Exception as e:
        logger.error(f"[Tuner] Error fetching simulated ghosts: {e}")
        return []


def optimize_ghost_filters(db_path: str) -> tuple[float, float, float] | None:
    """
    Optimizes RSI_LIMIT and CVD_FILTER_VAL using Optuna based on simulated ghost signals.
    """
    logger.info("[Tuner] Starting otonom ghost filter optimization...")
    ghosts = get_simulated_ghosts(db_path)
    if len(ghosts) < 5:
        logger.warning(f"[Tuner] Not enough simulated ghosts to optimize filters safely (found {len(ghosts)}/5). Skipping.")
        return None

    optuna.logging.set_verbosity(optuna.logging.WARNING)

    def objective(trial):
        # RSI limit parameter space (oversold/overbought boundary)
        rsi_limit = trial.suggest_float("rsi_limit", 20.0, 45.0, step=1.0)
        # CVD slope filter parameter space
        cvd_filter_val = trial.suggest_float("cvd_filter_val", -0.20, 0.10, step=0.01)

        simulated_pnl = 0.0

        for g in ghosts:
            direction = str(g.get("direction") or g.get("side", "LONG")).upper()
            rsi = float(g.get("rsi") or 50.0)
            cvd_slope = float(g.get("cvd_slope") or 0.0)
            pnl_r = float(g.get("virtual_pnl_r") or 0.0)

            # Filter simulation
            is_allowed = False
            if direction == "LONG":
                if rsi >= rsi_limit and cvd_slope >= cvd_filter_val:
                    is_allowed = True
            else:  # SHORT
                if rsi <= (100.0 - rsi_limit) and cvd_slope <= -cvd_filter_val:
                    is_allowed = True

            if is_allowed:
                simulated_pnl += pnl_r

        return simulated_pnl

    try:
        study = optuna.create_study(direction="maximize")
        study.optimize(objective, n_trials=50)

        best_params = study.best_params
        best_value = study.best_value
        logger.info(f"[Tuner] Ghost filter optimization finished. Best simulated PnL: {best_value:.2f}R. RSI_LIMIT={best_params['rsi_limit']:.1f}, CVD_FILTER_VAL={best_params['cvd_filter_val']:.4f}")
        return best_params["rsi_limit"], best_params["cvd_filter_val"], best_value
    except Exception as e:
        logger.error(f"[Tuner] Ghost filter Optuna study failed: {e}")
        return None


if __name__ == "__main__":
    optimize_parameters()
