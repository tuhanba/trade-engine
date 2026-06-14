"""
tests/test_funding_hunter.py — Faz 6.7 Funding-Rate Avcısı testleri.
"""
import pytest

from core.services.funding_hunter import detect_extreme_funding, FundingHunterService


_RATES = [
    {"symbol": "BTCUSDT", "lastFundingRate": "0.0015"},   # aşırı pozitif → SHORT
    {"symbol": "ETHUSDT", "lastFundingRate": "-0.0020"},  # aşırı negatif → LONG
    {"symbol": "SOLUSDT", "lastFundingRate": "0.0002"},   # normal → atla
    {"symbol": "ADAUSDT", "lastFundingRate": "0.0009"},   # eşik üstü → SHORT
    {"symbol": "BTCUSDC", "lastFundingRate": "0.0050"},   # USDT değil → atla
]


def test_detect_mean_reversion_bias():
    out = detect_extreme_funding(_RATES, threshold=0.0008, max_signals=10)
    by_sym = {c["symbol"]: c for c in out}
    # Pozitif funding → mean-reversion SHORT
    assert by_sym["BTCUSDT"]["funding_bias"] == "SHORT"
    assert by_sym["ADAUSDT"]["funding_bias"] == "SHORT"
    # Negatif funding → mean-reversion LONG
    assert by_sym["ETHUSDT"]["funding_bias"] == "LONG"
    # Normal funding ve USDT olmayan elenmiş
    assert "SOLUSDT" not in by_sym
    assert "BTCUSDC" not in by_sym


def test_detect_sorted_and_limited():
    # En aşırı funding önce, max_signals ile sınırlı
    out = detect_extreme_funding(_RATES, threshold=0.0008, max_signals=2)
    assert len(out) == 2
    # ETH (-0.0020) en aşırı, sonra BTC (0.0015)
    assert out[0]["symbol"] == "ETHUSDT"
    assert out[1]["symbol"] == "BTCUSDT"


def test_detect_skips_open_symbols():
    out = detect_extreme_funding(_RATES, threshold=0.0008, max_signals=10,
                                 open_symbols={"BTCUSDT"})
    syms = {c["symbol"] for c in out}
    assert "BTCUSDT" not in syms
    assert "ETHUSDT" in syms


def test_detect_scanned_payload_shape():
    out = detect_extreme_funding(_RATES, threshold=0.0008)
    c = out[0]
    # SCANNED tüketicisinin (trend_service) beklediği alanlar
    assert "symbol" in c
    assert c["status"] == "Eligible"
    assert c["source"] == "funding_hunter"
    assert "tradeability_score" in c


def test_detect_empty_and_malformed():
    assert detect_extreme_funding([], 0.0008) == []
    # bozuk rate → çökme yok, atla
    bad = [{"symbol": "XUSDT", "lastFundingRate": None},
           {"symbol": "YUSDT"}]
    assert detect_extreme_funding(bad, 0.0008) == []


def test_service_disabled_by_default(monkeypatch):
    import asyncio
    import config
    monkeypatch.setattr(config, "FUNDING_HUNTER_ENABLED", False)
    svc = FundingHunterService()
    # Devre dışıyken start_background_task hemen döner (çökme yok, loop'a girmez)
    asyncio.run(svc.start_background_task())
    assert svc._running is False
