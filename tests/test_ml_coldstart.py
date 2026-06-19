"""P1-6: ML cold-start bands (directive Section 17)."""
from core.ml_coldstart import classify_band, compute_coldstart_gate, COLD_MULT, SOFT_MULT


def test_band_edges():
    assert classify_band(0) == "UNTRAINED"
    assert classify_band(10, trained=False) == "UNTRAINED"
    assert classify_band(29) == "COLD"
    assert classify_band(30) == "WARMING"
    assert classify_band(99) == "WARMING"
    assert classify_band(100) == "MATURE"


def test_untrained_is_passive():
    g = compute_coldstart_gate(0, prob=0.10, min_threshold=0.45, trained=False)
    assert g["band"] == "UNTRAINED"
    assert g["risk_multiplier"] == 1.0 and g["gated"] is False


def test_cold_only_reacts_to_egregious_prob():
    # mildly low prob during COLD -> still full risk
    assert compute_coldstart_gate(10, 0.40, 0.45)["risk_multiplier"] == 1.0
    # egregiously low (< threshold - 0.10) -> mild 0.75x
    g = compute_coldstart_gate(10, 0.30, 0.45)
    assert g["risk_multiplier"] == COLD_MULT and g["gated"] is True


def test_warming_soft_gate():
    assert compute_coldstart_gate(50, 0.40, 0.45)["risk_multiplier"] == SOFT_MULT
    assert compute_coldstart_gate(50, 0.50, 0.45)["risk_multiplier"] == 1.0


def test_mature_soft_gate():
    assert compute_coldstart_gate(200, 0.40, 0.45)["risk_multiplier"] == SOFT_MULT
    assert compute_coldstart_gate(200, 0.60, 0.45)["risk_multiplier"] == 1.0


def test_multiplier_bounds_and_monotonic_conservatism():
    # multiplier always in (0, 1]
    for n in (0, 10, 30, 100, 500):
        for p in (0.0, 0.3, 0.45, 0.6, 1.0):
            m = compute_coldstart_gate(n, p, 0.45)["risk_multiplier"]
            assert 0.0 < m <= 1.0
    # a low prob is never *less* conservative as the model matures
    low = 0.20
    assert (compute_coldstart_gate(10, low, 0.45)["risk_multiplier"]
            >= compute_coldstart_gate(50, low, 0.45)["risk_multiplier"])
