"""
core/rl_meta_learner.py - Phase M Reinforcement Learning Regime Switcher
=======================================================================
Provides Gaussian Mixture Model (GMM) regime classification and a Q-learning 
agent to dynamically tune key filters (RSI, CVD, Trailing ATR Multiplier) 
based on trade PnL feedback.
"""

import os
import logging
import pickle
import numpy as np
import pandas as pd
from sklearn.mixture import GaussianMixture
from typing import Dict, Any, Optional

import config

logger = logging.getLogger("ax.rl_meta_learner")
MODEL_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "rl_meta_learner.pkl")


class GMMRegimeClassifier:
    def __init__(self, n_components=4):
        self.gmm = GaussianMixture(n_components=n_components, random_state=42)
        self.is_fitted = False
        self.label_mapping = {}  # maps GMM label -> semantic name
        
    def fit(self, X: np.ndarray):
        """Fits the GMM model and maps clusters to semantic names."""
        self.gmm.fit(X)
        self.is_fitted = True
        
        # Sort cluster means by volatility (column 0) to separate high/low vol
        means = self.gmm.means_
        vol_indices = np.argsort(means[:, 0])
        low_vol_indices = vol_indices[:2]
        high_vol_indices = vol_indices[2:]
        
        # For low vol clusters, sort by trend strength (column 1) to label Choppy vs Trending
        low_vol_trend = np.argsort(means[low_vol_indices, 1])
        self.label_mapping[low_vol_indices[low_vol_trend[0]]] = "CHOPPY_LOW_VOL"
        self.label_mapping[low_vol_indices[low_vol_trend[1]]] = "TRENDING_LOW_VOL"
        
        # For high vol clusters, sort by trend strength (column 1)
        high_vol_trend = np.argsort(means[high_vol_indices, 1])
        self.label_mapping[high_vol_indices[high_vol_trend[0]]] = "CHOPPY_HIGH_VOL"
        self.label_mapping[high_vol_indices[high_vol_trend[1]]] = "TRENDING_HIGH_VOL"
        
    def predict(self, x: np.ndarray) -> str:
        if not self.is_fitted:
            return "CHOPPY_LOW_VOL"
        label = self.gmm.predict(x.reshape(1, -1))[0]
        return self.label_mapping.get(label, "CHOPPY_LOW_VOL")


class QLearningMetaLearner:
    def __init__(self, lr=0.1, discount=0.9, epsilon=0.15):
        self.lr = lr
        self.discount = discount
        self.epsilon = epsilon
        self.actions = [0, 1, 2]  # 0: Neutral, 1: Defensive, 2: Aggressive
        self.states = ["TRENDING_HIGH_VOL", "TRENDING_LOW_VOL", "CHOPPY_HIGH_VOL", "CHOPPY_LOW_VOL"]
        self.q_table = {s: {a: 0.0 for a in self.actions} for s in self.states}
        
    def select_action(self, state: str) -> int:
        import random
        if state not in self.q_table:
            return 0
        if random.random() < self.epsilon:
            return random.choice(self.actions)
        # Greedy action choice
        best_val = max(self.q_table[state].values())
        best_actions = [a for a, val in self.q_table[state].items() if val == best_val]
        return random.choice(best_actions)
        
    def update(self, state: str, action: int, reward: float, next_state: str):
        if state not in self.q_table or next_state not in self.q_table:
            return
        max_next_q = max(self.q_table[next_state].values())
        old_q = self.q_table[state][action]
        self.q_table[state][action] = old_q + self.lr * (reward + self.discount * max_next_q - old_q)


class RLMetaLearnerContainer:
    def __init__(self):
        self.gmm_classifier = GMMRegimeClassifier()
        self.q_learner = QLearningMetaLearner()
        self.last_state = "CHOPPY_LOW_VOL"
        self.last_action = 0


def fit_from_market_data(client=None) -> np.ndarray:
    """Fetches BTCUSDT klines to fit the GMM, with a synthetic fallback if offline/mocked."""
    try:
        from core.market_data import get_klines
        klines = get_klines("BTCUSDT", "1h", 110)
        if not klines or len(klines) < 30:
            raise ValueError("Insufficient market data")
            
        closes = np.array([float(k[4]) for k in klines])
        highs = np.array([float(k[2]) for k in klines])
        lows = np.array([float(k[3]) for k in klines])
        
        tr = np.maximum(highs[1:] - lows[1:], np.maximum(np.abs(highs[1:] - closes[:-1]), np.abs(lows[1:] - closes[:-1])))
        atr = pd.Series(tr).rolling(14).mean().dropna().values
        closes_aligned = closes[15:]
        volatility = atr[1:] / closes_aligned
        
        returns = np.diff(closes_aligned) / closes_aligned[:-1]
        trend_strength = pd.Series(returns).rolling(14).std().dropna().values
        
        min_len = min(len(volatility), len(trend_strength))
        volatility = volatility[-min_len:]
        trend_strength = trend_strength[-min_len:]
        
        X = np.column_stack([volatility, trend_strength])
        return X
    except Exception as e:
        logger.warning(f"GMM live fit failed: {e}. Generating dummy training data.")
        np.random.seed(42)
        c0 = np.random.normal(loc=[0.005, 0.001], scale=[0.001, 0.0005], size=(25, 2))
        c1 = np.random.normal(loc=[0.006, 0.005], scale=[0.001, 0.001], size=(25, 2))
        c2 = np.random.normal(loc=[0.02, 0.002], scale=[0.003, 0.0008], size=(25, 2))
        c3 = np.random.normal(loc=[0.025, 0.015], scale=[0.004, 0.003], size=(25, 2))
        return np.vstack([c0, c1, c2, c3])


def load_rl_meta_learner() -> RLMetaLearnerContainer:
    if os.path.exists(MODEL_PATH):
        try:
            with open(MODEL_PATH, "rb") as f:
                container = pickle.load(f)
                if hasattr(container, "gmm_classifier") and container.gmm_classifier.is_fitted:
                    return container
        except Exception as e:
            logger.warning(f"Error loading RL learner model: {e}. Rebuilding...")
            
    container = RLMetaLearnerContainer()
    X = fit_from_market_data()
    container.gmm_classifier.fit(X)
    save_rl_meta_learner(container)
    return container


def save_rl_meta_learner(container: RLMetaLearnerContainer):
    try:
        with open(MODEL_PATH, "wb") as f:
            pickle.dump(container, f)
    except Exception as e:
        logger.error(f"Failed to save RL Meta-Learner model: {e}")


def get_current_regime(symbol: str = "BTCUSDT") -> str:
    try:
        from core.market_data import get_klines
        klines = get_klines(symbol, "1h", 20)
        if not klines or len(klines) < 15:
            return "CHOPPY_LOW_VOL"
            
        closes = np.array([float(k[4]) for k in klines])
        highs = np.array([float(k[2]) for k in klines])
        lows = np.array([float(k[3]) for k in klines])
        
        tr = np.maximum(highs[1:] - lows[1:], np.maximum(np.abs(highs[1:] - closes[:-1]), np.abs(lows[1:] - closes[:-1])))
        atr = pd.Series(tr).rolling(14).mean().dropna().values
        curr_vol = atr[-1] / closes[-1]
        
        returns = np.diff(closes[1:]) / closes[1:-1]
        curr_trend = pd.Series(returns).rolling(14).std().dropna().values[-1]
        
        x = np.array([curr_vol, curr_trend])
        
        container = load_rl_meta_learner()
        return container.gmm_classifier.predict(x)
    except Exception as e:
        logger.warning(f"Error predicting regime: {e}")
        return "CHOPPY_LOW_VOL"


def apply_rl_action_shifts(action: int):
    try:
        from database import update_system_state
        shifts = {
            0: {"rsi": 0.0, "cvd": 0.0, "trail": 0.0},
            1: {"rsi": 4.0, "cvd": 0.05, "trail": 0.2},
            2: {"rsi": -4.0, "cvd": -0.05, "trail": -0.2}
        }.get(action, {"rsi": 0.0, "cvd": 0.0, "trail": 0.0})
        
        update_system_state("rl_rsi_limit_shift", str(shifts["rsi"]))
        update_system_state("rl_cvd_filter_shift", str(shifts["cvd"]))
        update_system_state("rl_trail_mult_shift", str(shifts["trail"]))
    except Exception as e:
        logger.error(f"Failed to apply RL action shifts: {e}")


def handle_rl_trade_opened(trade_payload: dict):
    try:
        trade_id = trade_payload.get("id") or trade_payload.get("trade_id")
        if not trade_id:
            return
        
        container = load_rl_meta_learner()
        state = container.last_state
        action = container.last_action
        
        from database import update_system_state
        update_system_state(f"rl_state_{trade_id}", state)
        update_system_state(f"rl_action_{trade_id}", str(action))
        logger.info(f"[RL Meta-Learner] Stored state={state}, action={action} for trade #{trade_id}")
    except Exception as e:
        logger.error(f"[RL Meta-Learner] Failed to handle trade opened: {e}")


def update_rl_on_trade_closed(trade_payload: dict):
    try:
        trade_id = trade_payload.get("id") or trade_payload.get("trade_id")
        if not trade_id:
            return
            
        from database import get_system_state
        state = get_system_state(f"rl_state_{trade_id}")
        action_str = get_system_state(f"rl_action_{trade_id}")
        
        if not state or action_str is None:
            logger.debug(f"[RL Meta-Learner] No RL state/action found for trade #{trade_id}. Skipping Q-update.")
            return
            
        action = int(action_str)
        reward = float(trade_payload.get("r_multiple") or trade_payload.get("net_pnl") or 0.0)
        
        # Next state
        next_state = get_current_regime()
        
        container = load_rl_meta_learner()
        container.q_learner.update(state, action, reward, next_state)
        
        new_action = container.q_learner.select_action(next_state)
        container.last_state = next_state
        container.last_action = new_action
        
        save_rl_meta_learner(container)
        apply_rl_action_shifts(new_action)
        
        logger.info(f"[RL Q-Update] Trade #{trade_id} closed. State: {state} -> Action: {action} -> Reward: {reward:.2f}. New State: {next_state} -> Next Action: {new_action}")
    except Exception as e:
        logger.error(f"[RL Meta-Learner] Failed to update Q-learner on trade close: {e}")
