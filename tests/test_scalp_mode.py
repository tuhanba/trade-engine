"""
tests/test_scalp_mode.py — CLAUDE_FIX_SCALP_SIMPLIFY doğrulama testleri.

SCALP_MODE (mode-gate) açıkken sistemin sade/hızlı scalp kimliğine büründüğünü
ve otonom eşik oynamalarının kapandığını doğrular. Felsefe: Beyin SİLİNMEZ,
KAPATILIR — `SCALP_MODE=false` ile her şey eski haline döner (son test bunu
doğrular).

Not: conftest.py, paket genelinde SCALP_MODE'u "false" sabitler (testler tam
"brain" davranışını doğrular). Bu dosya scalp modunu yalnız kendi testleri için
açar (`_scalp_on` autouse fixture) ve teardown'da baseline'a döner — çapraz test
kirlenmesi olmaz. Dinamik parametreler DB'den okunduğundan DB-bağımlı
assertion'lar `test_db` fixture'ı ile çalışır.
"""

import os
import importlib
import pytest

import config


@pytest.fixture(autouse=True)
def _scalp_on():
    """conftest SCALP_MODE'u 'false' sabitliyor; bu dosya scalp modunu açık ister.
    Env'i açıkça 'true' yapıp config'i yeniden yükler, teardown'da geri alır."""
    prev = os.environ.get("SCALP_MODE")
    os.environ["SCALP_MODE"] = "true"
    importlib.reload(config)
    yield
    if prev is None:
        os.environ.pop("SCALP_MODE", None)
    else:
        os.environ["SCALP_MODE"] = prev
    importlib.reload(config)


def test_scalp_mode_default_on():
    assert config.SCALP_MODE is True


def test_scalp_static_threshold(test_db):
    # FAZ 3.3: tek statik eşik = 45, dinamik/rejim eşik oynaması KAPALI
    assert config.TRADE_THRESHOLD == 45.0
    assert config.DYNAMIC_THRESHOLD_ENABLED is False


def test_threshold_ordering_preserved(test_db):
    # DATA < WATCHLIST < TELEGRAM < TRADE sıralaması korunmalı
    assert config.DATA_THRESHOLD < config.WATCHLIST_THRESHOLD
    assert config.WATCHLIST_THRESHOLD < config.TELEGRAM_THRESHOLD
    assert config.TELEGRAM_THRESHOLD < config.TRADE_THRESHOLD


def test_scalp_tp_splits_sum_100():
    # FAZ 2: 60 / 25 / 15 = 100 (test_config_tp_logic ile uyumlu)
    assert config.TP1_CLOSE_PCT == 60
    assert config.TP2_CLOSE_PCT == 25
    assert config.RUNNER_CLOSE_PCT == 15
    assert config.TP1_CLOSE_PCT + config.TP2_CLOSE_PCT + config.RUNNER_CLOSE_PCT == 100


def test_scalp_identity_params_static():
    # FAZ 2: scalp kimliği — modül-seviye statik parametreler
    assert config.MAX_HOLD_MINUTES == 25
    assert config.SCALP_TIME_DECAY_BREAKEVEN_MINUTES == 8
    assert config.MIN_SL_PCT == 0.004
    assert config.TP1_R == 1.0
    assert config.TP3_R == 3.0
    assert config.MIN_RR == 1.1
    assert config.MIN_EXPECTED_MFE_R == 0.8
    assert config.MIN_MOVE_PCT == 0.2


def test_scalp_risk_and_tp2_bypass_params_table(test_db):
    """RISK_PCT ve TP2_R, AI-tuner'ın `params` tablosundan DEĞİL statik scalp
    varsayılanından gelmeli (tuner scalp modda kapalı). init_db `params`'ı
    risk_pct=1.5 / tp_atr_mult=2.0 ile seed'lese bile değer scalp olmalı."""
    assert config.RISK_PCT == 0.5
    assert config.TP2_R == 1.8
    assert config.TRAIL_ATR_MULT == 1.0
    # SL_ATR_MULT dokümanda değişmiyor → eskisi gibi params tablosundan (1.2)
    assert config.SL_ATR_MULT == 1.2


def test_scalp_conflicting_filters_disabled(test_db):
    # FAZ 1.4 + FAZ 2: çelişen/gereksiz filtreler scalp modda KAPALI
    assert config.EQUITY_CURVE_FILTER_ENABLED is False
    assert config.MTF_TREND_ALIGN_ENABLED is False
    assert config.SESSION_FILTER_ENABLED is False
    assert config.CONNORS_RSI_ENABLED is False
    assert config.ORDER_BOOK_WALL_FILTER_ENABLED is False
    assert config.SCALP_CVD_DIVERGENCE_FILTER_ENABLED is False
    assert config.SHORT_REQUIRES_BTC_BEARISH is False


def test_regime_scaling_disabled_in_scalp(test_db, monkeypatch):
    """DYNAMIC_THRESHOLD_ENABLED=False iken rejim, TRADE_THRESHOLD'u oynatmamalı.
    Legacy'de BULLISH rejim eşiği -4 düşürürdü (45 -> 41); scalp modda 45 kalır."""
    import database
    monkeypatch.setattr(database, "get_market_regime", lambda: "BULLISH")
    assert config.TRADE_THRESHOLD == 45.0


def test_scalp_mode_off_restores_legacy():
    """`SCALP_MODE=false` → eski 'brain' parametreleri geri gelir (geri dönülebilirlik)."""
    os.environ["SCALP_MODE"] = "false"
    importlib.reload(config)
    assert config.SCALP_MODE is False
    assert config.DYNAMIC_THRESHOLD_ENABLED is True
    assert config.MAX_HOLD_MINUTES == 240
    assert config.MIN_SL_PCT == 0.015
    assert config.TP1_R == 1.5
    assert config.MIN_RR == 1.5
    assert config.SHORT_REQUIRES_BTC_BEARISH is True
    # Not: _scalp_on fixture teardown'da env'i baseline'a (false) döndürür.
