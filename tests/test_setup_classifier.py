"""P1-1 regression: setup_type taxonomy classifier (directive Section 4/9).

Every signal must carry a setup_type; unclassifiable => UNKNOWN (safe default,
never live-executed). Classifier must never raise.
"""
from types import SimpleNamespace
import core.setup_classifier as sc


def _sig(metadata=None, **attrs):
    base = {"is_liquidity_sweep": False, "is_sfp": False, "market_regime": ""}
    base.update(attrs)
    base["metadata"] = metadata or {}
    return SimpleNamespace(**base)


def test_liquidity_sweep_flag():
    assert sc.classify(_sig(is_liquidity_sweep=True))[0] == sc.LIQUIDITY_SWEEP_SFP_REVERSAL


def test_sfp_flag():
    assert sc.classify(_sig(is_sfp=True))[0] == sc.LIQUIDITY_SWEEP_SFP_REVERSAL


def test_sweep_via_metadata_key():
    assert sc.classify(_sig(metadata={"liquidity_sweep": True}))[0] == sc.LIQUIDITY_SWEEP_SFP_REVERSAL


def test_funding_squeeze():
    assert sc.classify(_sig(metadata={"funding_rate": 0.0012}))[0] == sc.FUNDING_SQUEEZE_MEAN_REVERSION


def test_news_volatility():
    assert sc.classify(_sig(metadata={"news_event": True}))[0] == sc.NEWS_VOLATILITY_OPPORTUNITY


def test_orderflow_imbalance():
    assert sc.classify(_sig(metadata={"ob_imbalance": 0.8}))[0] == sc.ORDERFLOW_IMBALANCE_SCALP


def test_ema_vwap_pullback_requires_trend():
    assert sc.classify(_sig(market_regime="BULLISH", metadata={"ema_pullback": True}))[0] == sc.EMA_VWAP_PULLBACK_SCALP
    # pullback without a trend regime must NOT be classified as pullback
    assert sc.classify(_sig(market_regime="SIDEWAYS", metadata={"ema_pullback": True}))[0] != sc.EMA_VWAP_PULLBACK_SCALP


def test_momentum_expansion():
    assert sc.classify(_sig(market_regime="BULLISH", metadata={"oi_change_pct": 3.5, "cvd": 1200}))[0] == sc.MOMENTUM_EXPANSION_SCALP
    assert sc.classify(_sig(market_regime="BULLISH", metadata={"breakout": True, "cvd_delta": 5}))[0] == sc.MOMENTUM_EXPANSION_SCALP


def test_unknown_when_no_features():
    assert sc.classify(_sig())[0] == sc.UNKNOWN


def test_priority_sweep_over_funding():
    # sweep is the most specific structural signature -> wins over funding
    assert sc.classify(_sig(is_sfp=True, metadata={"funding_rate": 0.01}))[0] == sc.LIQUIDITY_SWEEP_SFP_REVERSAL


def test_never_raises_on_garbage():
    assert sc.classify(None)[0] == sc.UNKNOWN
    assert sc.classify(SimpleNamespace())[0] in sc.SETUP_TYPES + (sc.UNKNOWN,)
    assert sc.classify(SimpleNamespace(metadata={"funding_rate": "not_a_number"}))[0] == sc.UNKNOWN


def test_signaldata_default_setup_type():
    from core.data_layer import SignalData
    s = SignalData()
    assert s.setup_type == "UNKNOWN"
    assert "setup_type" in s.to_dict() if hasattr(s, "to_dict") else True
