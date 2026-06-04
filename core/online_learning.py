"""
core/online_learning.py - Phase G Online Incremental Learning Module
=====================================================================
Uses SGDClassifier and StandardScaler with partial_fit to dynamically
update model weights upon every trade closure.
"""

import os
import pickle
import logging
from datetime import datetime, timezone
import numpy as np

import config
import database

logger = logging.getLogger("ax.online_learning")
MODEL_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "sgd_online_model.pkl")


class SGDOnlineLearner:
    def __init__(self):
        self.model = None
        self.scaler = None
        self.trained = False
        self.n_samples = 0
        self._load_model()

    def _init_new_model(self):
        from sklearn.linear_model import SGDClassifier
        from sklearn.preprocessing import StandardScaler
        
        self.scaler = StandardScaler()
        self.model = SGDClassifier(
            loss="log_loss",  # Equivalent to Logistic Regression (provides predict_proba)
            penalty="l2",
            alpha=0.01,
            random_state=42
        )
        self.trained = False
        self.n_samples = 0

    def _load_model(self):
        try:
            if not os.path.exists(MODEL_PATH):
                self._init_new_model()
                return
            with open(MODEL_PATH, "rb") as f:
                data = pickle.load(f)
            self.model = data["model"]
            self.scaler = data["scaler"]
            self.n_samples = data.get("n_samples", 0)
            self.trained = data.get("trained", False)
            logger.info(f"SGD Online Model loaded. Samples processed: {self.n_samples}")
        except Exception as e:
            logger.warning(f"Failed to load SGD online model, initializing new: {e}")
            self._init_new_model()

    def _save_model(self):
        try:
            with open(MODEL_PATH, "wb") as f:
                pickle.dump({
                    "model": self.model,
                    "scaler": self.scaler,
                    "n_samples": self.n_samples,
                    "trained": self.trained
                }, f)
        except Exception as e:
            logger.warning(f"Failed to save SGD online model: {e}")

    def dict_to_features(self, f: dict) -> list:
        # Category values conversion
        btc_trend = f.get("btc_trend", "NEUTRAL") or "NEUTRAL"
        btc_num = 1 if btc_trend == "BULLISH" else (-1 if btc_trend == "BEARISH" else 0)
        
        direction = f.get("direction") or f.get("side") or "LONG"
        dir_num = 1 if str(direction).upper() == "LONG" else -1
        
        session = f.get("session", "OFF") or "OFF"
        sess_num = {"ASIA": 0, "LONDON": 1, "NEWYORK": 2, "OFF": 3}.get(session, 3)
        
        prev_result = f.get("prev_result", "NONE") or "NONE"
        prev_num = 1 if prev_result == "WIN" else (-1 if prev_result == "LOSS" else 0)
        
        # Get symbol winrate statistics
        sym = f.get("symbol", "")
        coin_wr = 0.5
        try:
            # Dynamically fetch symbol stats from DB if available
            conn = database.get_connection()
            row = conn.execute("""
                SELECT COUNT(*) as total,
                       SUM(CASE WHEN result='WIN' THEN 1 ELSE 0 END) as wins
                FROM pattern_memory
                WHERE symbol = ? AND result IN ('WIN','LOSS')
            """, (sym,)).fetchone()
            if row and row["total"] > 0:
                coin_wr = float(row["wins"] or 0) / float(row["total"])
            conn.close()
        except Exception:
            pass

        features = [
            float(f.get("adx", 0) or f.get("adx15", 0) or 0),
            float(f.get("rv", 1.0) or 1.0),
            float(f.get("rsi5", 50) or 50),
            float(f.get("rsi1", 50) or 50),
            float(f.get("funding_favorable", 1) or 1),
            float(f.get("bb_width_pct", 0) or f.get("bb_width", 0) or 0),
            float(f.get("ob_ratio", 1.0) or 1.0),
            float(f.get("volume_m", 0) or 0),
            float(btc_num),
            float(dir_num),
            float(sess_num),
            float(f.get("hold_minutes", 0) or 0),
            float(f.get("partial_exit", 0) or 0),
            float(coin_wr),
            float(f.get("bb_width_chg", 0) or 0),
            float(f.get("momentum_3c", 0) or 0),
            float(prev_num),
            float(f.get("funding_rate", 0) or 0),
            float(f.get("cvd_value", 0) or 0),
            float(f.get("oi_change_pct", 0) or 0)
        ]
        return features

    def update(self, features_dict: dict, outcome: int):
        """
        Incrementally train the online model using a single sample.
        outcome: 1 for WIN, 0 for LOSS
        """
        try:
            from sklearn.preprocessing import StandardScaler
            from sklearn.linear_model import SGDClassifier
        except ImportError:
            return

        try:
            x_raw = self.dict_to_features(features_dict)
            X = np.array([x_raw], dtype=float)
            y = np.array([outcome], dtype=int)

            # Online Scaling
            self.scaler.partial_fit(X)
            X_scaled = self.scaler.transform(X)

            # Online SGDClassifier update
            self.model.partial_fit(X_scaled, y, classes=[0, 1])
            self.n_samples += 1
            self.trained = True
            
            logger.info(f"[SGD Online] Updated with sample. Total processed: {self.n_samples}. Outcome: {'WIN' if outcome == 1 else 'LOSS'}")
            self._save_model()
        except Exception as e:
            logger.error(f"[SGD Online] Update failed: {e}")

    def predict_proba(self, features_dict: dict) -> float:
        """
        Predict probability of WIN (returns float between 0.0 and 1.0).
        """
        if not self.trained or self.model is None:
            return 0.5
        try:
            x_raw = self.dict_to_features(features_dict)
            X = np.array([x_raw], dtype=float)
            X_scaled = self.scaler.transform(X)
            probas = self.model.predict_proba(X_scaled)[0]
            return float(probas[1]) if len(probas) > 1 else 0.5
        except Exception as e:
            logger.warning(f"[SGD Online] Predict failed: {e}")
            return 0.5

    def backup_model(self) -> str:
        """Saves a timestamped backup of the current online model and returns the file path."""
        try:
            import shutil
            backup_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "backups")
            os.makedirs(backup_dir, exist_ok=True)
            timestamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
            backup_filename = f"sgd_online_model_{timestamp}.pkl"
            backup_path = os.path.join(backup_dir, backup_filename)
            if os.path.exists(MODEL_PATH):
                shutil.copy2(MODEL_PATH, backup_path)
                logger.info(f"Model backup created: {backup_path}")
                return backup_filename
            else:
                with open(backup_path, "wb") as f:
                    pickle.dump({
                        "model": self.model,
                        "scaler": self.scaler,
                        "n_samples": self.n_samples,
                        "trained": self.trained
                    }, f)
                logger.info(f"Model backup created directly: {backup_path}")
                return backup_filename
        except Exception as e:
            logger.error(f"Failed to create model backup: {e}")
            raise e

    def rollback_model(self, backup_filename: str) -> bool:
        """Restores the online model from a specified backup file name."""
        try:
            backup_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "backups")
            backup_path = os.path.join(backup_dir, backup_filename)
            if not os.path.exists(backup_path):
                if os.path.exists(backup_filename):
                    backup_path = backup_filename
                else:
                    logger.warning(f"Backup file not found: {backup_path}")
                    return False
            
            import shutil
            shutil.copy2(backup_path, MODEL_PATH)
            self._load_model()
            logger.info(f"Successfully rolled back model to {backup_filename}")
            return True
        except Exception as e:
            logger.error(f"Failed to rollback model: {e}")
            return False


# Global Instance
_learner_instance = None

def get_learner() -> SGDOnlineLearner:
    global _learner_instance
    if _learner_instance is None:
        _learner_instance = SGDOnlineLearner()
    return _learner_instance

def update_online_model(features_dict: dict, outcome: int):
    get_learner().update(features_dict, outcome)

def predict_online_probability(features_dict: dict) -> float:
    return get_learner().predict_proba(features_dict)

def backup_online_model() -> str:
    return get_learner().backup_model()

def rollback_online_model(backup_filename: str) -> bool:
    return get_learner().rollback_model(backup_filename)
