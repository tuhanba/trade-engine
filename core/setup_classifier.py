"""core/setup_classifier.py — P1-1: mandatory setup_type taxonomy.

Directive Section 4/9: every signal must carry a setup_type. This maps a
finalized signal to exactly one of the six allowed setups, or UNKNOWN when it
cannot be classified. UNKNOWN is the SAFE default — such signals may be
paper-tracked but must never open a live trade (enforced on the live path).

Pure and dependency-free => unit-testable. Features are read defensively, so a
missing attribute / metadata key never raises; it just lowers confidence toward
UNKNOWN.
"""
from __future__ import annotations

from typing import Any, Tuple

MOMENTUM_EXPANSION_SCALP = "MOMENTUM_EXPANSION_SCALP"
EMA_VWAP_PULLBACK_SCALP = "EMA_VWAP_PULLBACK_SCALP"
LIQUIDITY_SWEEP_SFP_REVERSAL = "LIQUIDITY_SWEEP_SFP_REVERSAL"
ORDERFLOW_IMBALANCE_SCALP = "ORDERFLOW_IMBALANCE_SCALP"
FUNDING_SQUEEZE_MEAN_REVERSION = "FUNDING_SQUEEZE_MEAN_REVERSION"
NEWS_VOLATILITY_OPPORTUNITY = "NEWS_VOLATILITY_OPPORTUNITY"
UNKNOWN = "UNKNOWN"

SETUP_TYPES = (
    MOMENTUM_EXPANSION_SCALP,
    EMA_VWAP_PULLBACK_SCALP,
    LIQUIDITY_SWEEP_SFP_REVERSAL,
    ORDERFLOW_IMBALANCE_SCALP,
    FUNDING_SQUEEZE_MEAN_REVERSION,
    NEWS_VOLATILITY_OPPORTUNITY,
)

# Defaults (config-overridable via SETUP_* keys)
_FUNDING_EXTREME = 0.0005   # |funding rate| per interval — crowded side
_OI_EXPANSION_PCT = 2.0     # |OI change %| signalling expansion
_IMBALANCE_MIN = 0.60       # |orderbook/CVD imbalance| threshold

_TREND_REGIMES = ("BULLISH", "BEARISH", "TREND", "TRENDING")


def _md(sig) -> dict:
    md = getattr(sig, "metadata", None)
    return md if isinstance(md, dict) else {}


def _num(md: dict, *keys, default: float = 0.0) -> float:
    for k in keys:
        v = md.get(k)
        if v not in (None, ""):
            try:
                return float(v)
            except (TypeError, ValueError):
                continue
    return default


def _flag(sig, md: dict, attr: str, *keys) -> bool:
    if bool(getattr(sig, attr, False)):
        return True
    return any(bool(md.get(k)) for k in keys)


def classify(sig: Any) -> Tuple[str, str]:
    """Return (setup_type, setup_reason). Never raises; UNKNOWN on no match."""
    try:
        return _classify(sig)
    except Exception:
        return UNKNOWN, "classify_error"


def _classify(sig) -> Tuple[str, str]:
    try:
        import config
    except Exception:
        config = None

    def _cfg(name, default):
        return float(getattr(config, name, default)) if config is not None else default

    md = _md(sig)
    regime = str(getattr(sig, "market_regime", "") or md.get("market_regime", "")).upper()

    funding_extreme = _cfg("SETUP_FUNDING_EXTREME", _FUNDING_EXTREME)
    oi_min = _cfg("SETUP_OI_EXPANSION_PCT", _OI_EXPANSION_PCT)
    imb_min = _cfg("SETUP_IMBALANCE_MIN", _IMBALANCE_MIN)

    # 1) Liquidity sweep / SFP reversal — explicit structural flags (most reliable)
    if _flag(sig, md, "is_liquidity_sweep", "is_liquidity_sweep", "liquidity_sweep") or \
       _flag(sig, md, "is_sfp", "is_sfp", "sfp"):
        return LIQUIDITY_SWEEP_SFP_REVERSAL, "liquidity sweep / SFP rejection"

    # 2) Funding squeeze / mean reversion — extreme funding, crowded side
    funding = _num(md, "funding_rate", "funding", "avg_funding")
    if abs(funding) >= funding_extreme:
        return FUNDING_SQUEEZE_MEAN_REVERSION, f"extreme funding {funding:+.4f}"

    # 3) News volatility — explicit news driver
    if _flag(sig, md, "is_news", "is_news", "news_event") or \
       abs(_num(md, "news_impact", "news_sentiment_score")) >= 1.0:
        return NEWS_VOLATILITY_OPPORTUNITY, "news-driven volatility"

    # 4) Orderflow imbalance — strong CVD / orderbook skew
    imbalance = _num(md, "ob_imbalance", "orderbook_imbalance", "imbalance", "cvd_buy_ratio")
    if abs(imbalance) >= imb_min or bool(md.get("cvd_absorption")):
        return ORDERFLOW_IMBALANCE_SCALP, f"orderflow imbalance {imbalance:.2f}"

    # 5) EMA/VWAP pullback — trend present + pullback flag
    if _flag(sig, md, "is_pullback", "is_pullback", "ema_pullback", "vwap_pullback", "pullback") \
       and regime in _TREND_REGIMES:
        return EMA_VWAP_PULLBACK_SCALP, "trend pullback to EMA/VWAP"

    # 6) Momentum expansion — breakout / OI expansion with directional confirm
    oi_chg = _num(md, "oi_change_pct")
    cvd = _num(md, "cvd", "cvd_delta", "cvd_slope")
    if (bool(md.get("breakout")) or abs(oi_chg) >= oi_min) and (abs(cvd) > 0 or regime in _TREND_REGIMES):
        return MOMENTUM_EXPANSION_SCALP, "volume/OI expansion with directional confirm"

    return UNKNOWN, "unclassified"
