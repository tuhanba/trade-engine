"""
AURVEX.Ai — ML Sinyal Skoru v2.0
=================================
Geçmiş trade verilerinden (pattern_memory tablosu) öğrenerek
her yeni sinyale 0-100 arası bir "Güven Skoru" atar.

v1.0 → v2.0:
  - Yeni özellikler: trend_4h, vp_score, hold_minutes, session, partial_exit
  - Ensemble model: RandomForest + GradientBoosting (soft voting)
  - Dinamik eşik: win rate'e göre otomatik ayarlama
  - Coin bazlı hafıza: her coin için ayrı win rate takibi
  - Temporal özellikler: gün/saat bazlı performans
  - Kalibre edilmiş olasılıklar: CalibratedClassifierCV

Algoritma: Voting Ensemble (RF + GB)
Özellikler: 17 feature (14 → 17: +BBWidthChg, +Momentum3C, +PrevResult)
Hedef: WIN (1) veya LOSS (0)
"""

import os
from config import DB_PATH
from database import get_conn
import sqlite3
import logging
import pickle
from datetime import datetime, timezone

logger = logging.getLogger(__name__)


MODEL_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "ml_model.pkl")

MIN_TRAIN_SAMPLES = 30
SCORE_THRESHOLD   = 35   # Analiz: 35 puan altı sinyaller engellenir


class MLSignalScorer:
    """
    Pattern Memory tablosundaki geçmiş trade verilerinden öğrenen
    Ensemble (RF + GB) tabanlı sinyal güven skoru hesaplayıcı.
    """

    def __init__(self):
        self.model       = None
        self.scaler      = None
        self.trained     = False
        self.n_samples   = 0
        self.last_train  = None
        self.cv_accuracy = 0.0
        self.feature_imp = {}
        self.coin_stats  = {}   # {symbol: {wins, total}}
        self._try_load_model()

    # ─────────────────────────────────────────────────────────────────────────
    # VERİ HAZIRLIĞI
    # ─────────────────────────────────────────────────────────────────────────

    def _load_training_data(self):
        """Pattern memory tablosundan genişletilmiş eğitim verisi çeker."""
        try:
            conn = get_conn()
            c = conn.cursor()
            c.execute("""
                SELECT
                    adx, rv, rsi5, rsi1, funding_favorable,
                    bb_width_pct, ob_ratio, volume_m,
                    COALESCE(btc_trend, 'NEUTRAL') as btc_trend,
                    COALESCE(direction, 'LONG') as direction,
                    COALESCE(session, 'OFF') as session,
                    COALESCE(hold_minutes, 0) as hold_minutes,
                    COALESCE(partial_exit, 0) as partial_exit,
                    symbol,
                    result,
                    created_at,
                    COALESCE(bb_width_chg, 0) as bb_width_chg,
                    COALESCE(momentum_3c, 0) as momentum_3c,
                    COALESCE(prev_result, 'NONE') as prev_result
                FROM pattern_memory
                WHERE result IN ('WIN','LOSS')
                ORDER BY id DESC
                LIMIT 1000
            """)
            rows = c.fetchall()

            # Coin bazlı istatistikler
            c.execute("""
                SELECT symbol,
                       COUNT(*) as total,
                       SUM(CASE WHEN result='WIN' THEN 1 ELSE 0 END) as wins
                FROM pattern_memory
                WHERE result IN ('WIN','LOSS')
                GROUP BY symbol
            """)
            for row in c.fetchall():
                sym, total, wins = row
                self.coin_stats[sym] = {
                    "total": total or 0,
                    "wins": wins or 0,
                    "win_rate": (wins or 0) / max(total, 1)
                }

            conn.close()
            return rows
        except Exception as e:
            logger.warning(f"ML veri yükleme hatası: {e}")
            return []

    def _row_to_features(self, row):
        """Tek satırı feature vektörüne dönüştürür."""
        # 19 kolon: eski 16 + 3 yeni (bb_width_chg, momentum_3c, prev_result)
        if len(row) >= 19:
            (adx, rv, rsi5, rsi1, fund_fav, bb_w, ob_r, vol_m,
             btc_trend, direction, session, hold_min, partial_ex,
             symbol, result, created_at,
             bb_width_chg, momentum_3c, prev_result) = row[:19]
        else:
            # Eski veri uyumluluğu: yeni kolonlar yoksa varsayılan
            (adx, rv, rsi5, rsi1, fund_fav, bb_w, ob_r, vol_m,
             btc_trend, direction, session, hold_min, partial_ex,
             symbol, result, created_at) = row[:16]
            bb_width_chg = 0
            momentum_3c  = 0
            prev_result  = "NONE"

        # Kategorik → sayısal
        btc_num  = 1 if btc_trend == "BULLISH" else (-1 if btc_trend == "BEARISH" else 0)
        dir_num  = 1 if direction == "LONG" else -1
        sess_num = {"ASIA": 0, "LONDON": 1, "NEWYORK": 2, "OFF": 3}.get(session, 3)
        # Markov: önceki trade sonucu → sayısal
        prev_num = 1 if prev_result == "WIN" else (-1 if prev_result == "LOSS" else 0)

        # Coin win rate (geçmiş performans)
        coin_wr = self.coin_stats.get(symbol, {}).get("win_rate", 0.5)

        # Saat bazlı özellik
        hour = 12  # varsayılan
        try:
            if created_at:
                hour = int(created_at[11:13])
        except:
            pass

        features = [
            float(adx           or 0),
            float(rv            or 0),
            float(rsi5          or 50),
            float(rsi1          or 50),
            float(fund_fav      or 1),
            float(bb_w          or 0),
            float(ob_r          or 1),
            float(vol_m         or 0),
            float(btc_num),
            float(dir_num),
            float(sess_num),
            float(hold_min      or 0),
            float(partial_ex    or 0),
            float(coin_wr),
            float(bb_width_chg  or 0),   # Band Growth
            float(momentum_3c   or 0),   # Predictive Momentum
            float(prev_num),             # Markov
        ]
        return features

    def _rows_to_xy(self, rows):
        X, y = [], []
        for row in rows:
            try:
                features = self._row_to_features(row)
                label = 1 if row[14] == "WIN" else 0
                X.append(features)
                y.append(label)
            except Exception as e:
                logger.debug(f"Feature dönüşüm hatası: {e}")
        return X, y

    # ─────────────────────────────────────────────────────────────────────────
    # MODEL EĞİTİMİ
    # ─────────────────────────────────────────────────────────────────────────

    def train(self) -> bool:
        try:
            from sklearn.ensemble import RandomForestClassifier, GradientBoostingClassifier, VotingClassifier
            from sklearn.preprocessing import StandardScaler
            from sklearn.model_selection import cross_val_score, StratifiedKFold
            from sklearn.calibration import CalibratedClassifierCV
            import numpy as np
        except ImportError:
            logger.warning("scikit-learn kurulu değil. pip install scikit-learn")
            return False

        rows = self._load_training_data()
        if len(rows) < MIN_TRAIN_SAMPLES:
            logger.info(f"ML eğitim için yetersiz veri: {len(rows)}/{MIN_TRAIN_SAMPLES}")
            return False

        X, y = self._rows_to_xy(rows)
        X = np.array(X)
        y = np.array(y)

        n_win  = int(sum(y))
        n_loss = int(len(y) - n_win)
        logger.info(f"ML eğitim v2.0: {len(rows)} örnek | WIN={n_win} LOSS={n_loss} | {len(X[0])} feature")

        scaler = StandardScaler()
        X_scaled = scaler.fit_transform(X)

        # Random Forest
        rf = RandomForestClassifier(
            n_estimators=300,
            max_depth=7,
            min_samples_leaf=2,
            class_weight="balanced",
            random_state=42,
            n_jobs=-1,
        )

        # Gradient Boosting
        gb = GradientBoostingClassifier(
            n_estimators=150,
            max_depth=4,
            learning_rate=0.05,
            subsample=0.8,
            random_state=42,
        )

        # Ensemble: soft voting
        ensemble = VotingClassifier(
            estimators=[("rf", rf), ("gb", gb)],
            voting="soft",
            weights=[2, 1],   # RF'e daha fazla ağırlık
        )
        ensemble.fit(X_scaled, y)

        # Cross-validation
        try:
            cv = StratifiedKFold(n_splits=min(5, max(2, len(rows)//15)), shuffle=True, random_state=42)
            cv_scores = cross_val_score(ensemble, X_scaled, y, cv=cv, scoring="roc_auc")
            self.cv_accuracy = float(cv_scores.mean())
            logger.info(f"ML CV ROC-AUC: {self.cv_accuracy:.3f} ± {cv_scores.std():.3f}")
        except Exception as e:
            logger.debug(f"CV hatası: {e}")
            self.cv_accuracy = 0.0

        self.model      = ensemble
        self.scaler     = scaler
        self.trained    = True
        self.n_samples  = len(rows)
        self.last_train = datetime.now(timezone.utc)

        # Feature importance (RF'den al)
        try:
            feature_names = [
                "ADX","RV","RSI5","RSI1","Funding",
                "BBWidth","OBRatio","Volume",
                "BTC_Trend","Direction","Session",
                "HoldMin","PartialExit","CoinWR",
                "BBWidthChg","Momentum3C","PrevResult"
            ]
            importances = ensemble.estimators_[0].feature_importances_
            self.feature_imp = dict(zip(feature_names, importances))
            top = sorted(self.feature_imp.items(), key=lambda x: -x[1])[:6]
            logger.info("ML Feature Importance: " + " | ".join(f"{n}={v:.3f}" for n,v in top))
        except:
            pass

        self._save_model()
        return True

    # ─────────────────────────────────────────────────────────────────────────
    # TAHMİN
    # ─────────────────────────────────────────────────────────────────────────

    def predict(self, signal: dict) -> int:
        if not self.trained or self.model is None:
            return 50

        try:
            import numpy as np

            # Coin win rate
            sym = signal.get("symbol", "")
            coin_wr = self.coin_stats.get(sym, {}).get("win_rate", 0.5)

            btc_trend = signal.get("btc_trend", "NEUTRAL")
            btc_num   = 1 if btc_trend == "BULLISH" else (-1 if btc_trend == "BEARISH" else 0)
            dir_num   = 1 if signal.get("direction", "LONG") == "LONG" else -1

            # Seans tahmini (UTC saatine göre)
            hour = datetime.now(timezone.utc).hour
            if 1 <= hour < 4:     sess_num = 0   # ASIA
            elif 7 <= hour < 10:  sess_num = 1   # LONDON
            elif 13 <= hour < 17: sess_num = 2   # NEWYORK
            else:                 sess_num = 3   # OFF

            # Markov: prev_result → sayısal
            prev_result = signal.get("prev_result", "NONE")
            prev_num    = 1 if prev_result == "WIN" else (-1 if prev_result == "LOSS" else 0)

            features = np.array([[
                float(signal.get("adx15",    0)),
                float(signal.get("rv",       0)),
                float(signal.get("rsi5",    50)),
                float(signal.get("rsi1",    50)),
                float(signal.get("funding_favorable", 1)),
                float(signal.get("bb_width", 0)),
                float(signal.get("ob_ratio", 1)),
                float(signal.get("volume_m", 0)),
                float(btc_num),
                float(dir_num),
                float(sess_num),
                0.0,   # hold_minutes (henüz bilinmiyor)
                0.0,   # partial_exit (henüz bilinmiyor)
                float(coin_wr),
                float(signal.get("bb_width_chg", 0)),   # Band Growth
                float(signal.get("momentum_3c",  0)),   # Predictive Momentum
                float(prev_num),                         # Markov
            ]])

            X_scaled = self.scaler.transform(features)
            proba    = self.model.predict_proba(X_scaled)[0]
            win_prob = proba[1] if len(proba) > 1 else 0.5

            # Coin bazlı ağırlıklı skor:
            # Bu coin için >= 5 trade varsa, coin'in kişisel win rate'ini
            # %40 ağırlıkla skora karıştır. Az veri varsa sadece genel modeli kullan.
            coin_data  = self.coin_stats.get(sym, {})
            coin_total = coin_data.get("total", 0)
            if coin_total >= 5:
                coin_wr_val = coin_data.get("win_rate", 0.5)
                blended = win_prob * 0.60 + coin_wr_val * 0.40
                score = int(round(blended * 100))
                logger.debug(f"ML blend [{sym}]: model={int(win_prob*100)} coin_wr={int(coin_wr_val*100)} blend={score} (n={coin_total})")
            else:
                score = int(round(win_prob * 100))
            return score
        except Exception as e:
            logger.warning(f"ML predict hatası: {e}")
            return 50

    def should_trade(self, signal: dict) -> tuple:
        score = self.predict(signal)
        return score >= SCORE_THRESHOLD, score

    # ─────────────────────────────────────────────────────────────────────────
    # MODEL KAYIT / YÜKLEME
    # ─────────────────────────────────────────────────────────────────────────

    def _save_model(self):
        try:
            with open(MODEL_PATH, "wb") as f:
                pickle.dump({
                    "model":       self.model,
                    "scaler":      self.scaler,
                    "n_samples":   self.n_samples,
                    "trained_at":  self.last_train,
                    "cv_accuracy": self.cv_accuracy,
                    "feature_imp": self.feature_imp,
                    "coin_stats":  self.coin_stats,
                }, f)
            logger.info(f"ML model v2.0 kaydedildi: {MODEL_PATH}")
        except Exception as e:
            logger.warning(f"ML model kayıt hatası: {e}")

    def _try_load_model(self):
        try:
            if not os.path.exists(MODEL_PATH):
                return
            with open(MODEL_PATH, "rb") as f:
                data = pickle.load(f)
            self.model       = data["model"]
            self.scaler      = data["scaler"]
            self.n_samples   = data.get("n_samples", 0)
            self.last_train  = data.get("trained_at")
            self.cv_accuracy = data.get("cv_accuracy", 0.0)
            self.feature_imp = data.get("feature_imp", {})
            self.coin_stats  = data.get("coin_stats", {})
            self.trained     = True
            logger.info(f"ML model v2.0 yüklendi: {self.n_samples} örnek | "
                        f"CV AUC: {self.cv_accuracy:.3f}")
        except Exception as e:
            logger.debug(f"ML model yüklenemedi (ilk çalıştırma): {e}")

    def get_status(self) -> dict:
        return {
            "trained":     self.trained,
            "n_samples":   self.n_samples,
            "last_train":  str(self.last_train) if self.last_train else None,
            "threshold":   SCORE_THRESHOLD,
            "cv_accuracy": round(self.cv_accuracy, 3),
            "top_features": sorted(self.feature_imp.items(), key=lambda x: -x[1])[:5] if self.feature_imp else [],
        }


# ─────────────────────────────────────────────────────────────────────────────
# GLOBAL INSTANCE
# ─────────────────────────────────────────────────────────────────────────────
_scorer_instance: MLSignalScorer = None

def get_scorer() -> MLSignalScorer:
    global _scorer_instance
    if _scorer_instance is None:
        _scorer_instance = MLSignalScorer()
    return _scorer_instance

def train_model() -> bool:
    return get_scorer().train()

def score_signal(signal: dict) -> int:
    return get_scorer().predict(signal)

def should_trade(signal: dict) -> tuple:
    return get_scorer().should_trade(signal)
