"""core/ml_coldstart.py — P1-6 ML cold-start bands (directive Section 17).

The online learner's win-probability is only trustworthy once it has seen enough
labelled outcomes. Instead of the binary "no gate < 30 samples / full gate >= 30",
this graduates trust across sample-count bands and returns a *risk multiplier* —
never an outright block, because the learner still needs trades to learn from.

Pure / dependency-free so it is unit-testable in CI.

    UNTRAINED          -> ML gate passive (1.0x); other shields still apply
    COLD     (n<30)    -> only react to egregiously low prob, and only mildly (0.75x)
    WARMING  (30..99)  -> prob<threshold -> 0.5x
    MATURE   (n>=100)  -> prob<threshold -> 0.5x
"""
from __future__ import annotations

# sample-count band edges
COLD_MAX = 30        # n < COLD_MAX     -> COLD (model immature)
WARMING_MAX = 100    # n < WARMING_MAX  -> WARMING (partially reliable)

# during COLD, only react when prob is this far *below* the threshold
COLD_LOW_MARGIN = 0.10

# risk multipliers per gating decision
COLD_MULT = 0.75
SOFT_MULT = 0.5


def classify_band(n_samples: int, trained: bool = True) -> str:
    if not trained or n_samples <= 0:
        return "UNTRAINED"
    if n_samples < COLD_MAX:
        return "COLD"
    if n_samples < WARMING_MAX:
        return "WARMING"
    return "MATURE"


def compute_coldstart_gate(n_samples: int, prob: float, min_threshold: float,
                           trained: bool = True) -> dict:
    """Return {band, risk_multiplier, gated, reason}.

    risk_multiplier is in (0, 1] — it never blocks, so the learner keeps
    receiving labelled trades. Conservatism increases with model maturity.
    """
    band = classify_band(n_samples, trained)
    mult = 1.0
    gated = False

    if band == "UNTRAINED":
        reason = "model eğitilmedi — ML geçidi pasif (1.0x)"
    elif band == "COLD":
        if prob < (min_threshold - COLD_LOW_MARGIN):
            mult, gated = COLD_MULT, True
            reason = (f"COLD (n={n_samples}<{COLD_MAX}) ve prob {prob:.2f} çok düşük "
                      f"(<{min_threshold - COLD_LOW_MARGIN:.2f}) → {COLD_MULT}x")
        else:
            reason = f"COLD (n={n_samples}<{COLD_MAX}) ısınıyor → tam risk"
    elif band == "WARMING":
        if prob < min_threshold:
            mult, gated = SOFT_MULT, True
            reason = (f"WARMING (n={n_samples}<{WARMING_MAX}) ve prob {prob:.2f} "
                      f"< eşik {min_threshold:.2f} → {SOFT_MULT}x")
        else:
            reason = f"WARMING (n={n_samples}) prob {prob:.2f} ≥ eşik → tam risk"
    else:  # MATURE
        if prob < min_threshold:
            mult, gated = SOFT_MULT, True
            reason = f"MATURE prob {prob:.2f} < eşik {min_threshold:.2f} → {SOFT_MULT}x"
        else:
            reason = f"MATURE prob {prob:.2f} ≥ eşik → tam risk"

    return {"band": band, "risk_multiplier": mult, "gated": gated, "reason": reason}
