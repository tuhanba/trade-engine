import pickle
import os
import sys

# Add project root to path
sys.path.insert(0, r"c:\Users\pc\Desktop\aurvex\trade-engine-main (35)\trade-engine-main")

from core.ml_signal_scorer import get_scorer

scorer = get_scorer()
print("Model trained:", scorer.trained)
print("Number of samples:", scorer.n_samples)
print("CV Accuracy:", scorer.cv_accuracy)
print("Feature Importances:")
for f, imp in sorted(scorer.feature_imp.items(), key=lambda x: -x[1]):
    print(f"  {f}: {imp:.4f}")

# Let's test a dummy signal
signal = {
    "symbol": "BTCUSDT",
    "adx15": 35.0,
    "rv": 2.6,
    "rsi5": 55.0,
    "rsi1": 50.0,
    "funding_favorable": 1,
    "btc_trend": "BULLISH",
    "direction": "LONG",
    "momentum_3c": 2.1,
    "ob_ratio": 1.5,
    "bb_width": 0.05,
    "bb_width_chg": 0.01,
    "session": "LONDON",
    "prev_result": "NONE",
    "volume_m": 50.0,
    "funding_rate": 0.0001,
    "cvd_value": 10000.0,
    "oi_change_pct": 3.0,
}

score = scorer.predict(signal)
print(f"Dummy signal prediction score: {score}")

# Let's check what features make the score go up/down
for feature in ["adx15", "rv", "rsi5", "rsi1", "momentum_3c", "bb_width"]:
    print(f"\nTuning {feature}:")
    for val in [0.0, 0.5, 1.0, 5.0, 10.0, 20.0, 50.0, 80.0]:
        sig = signal.copy()
        sig[feature] = val
        s = scorer.predict(sig)
        print(f"  val={val} => score={s}")
