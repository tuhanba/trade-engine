"""
tests/test_paper_shield_bypass.py
=================================
Fix A REGRESYON TESTİ — open_paper_trade'in İKİNCİ kalkan katmanı (Pearson
korelasyon + portföy VaR) paper modda üst-pipeline ile tutarlı bypass edilir.

Bug: open_paper_trade korelasyon/VaR kalkanlarını is_paper'a bakmadan
çalıştırıyordu → üst-pipeline paper'da kalkanları bypass etse bile son aşama
EXECUTION_REJECTED(correlation/var) ile sessizce kesiyordu (2+ korele trade
boğuluyordu).

Fix: _should_bypass_portfolio_shields() tek noktada karar verir; korelasyon ve
VaR blokları "and not bypass_shields" ile koşullandı. Staleness guard ETKİLENMEZ.
"""
import numpy as np
import pytest

import config
from core.data_layer import SignalData
import execution_engine as ee
from execution_engine import ExecutionEngine


# ── Karar mantığı (birim testleri) ───────────────────────────────────────────

def test_bypass_decision_paper_production(test_db, monkeypatch):
    """paper mod + production (test dışı) → kalkanlar bypass."""
    monkeypatch.setattr(ee, "_is_pytest_running", lambda: False)
    test_db.update_system_state("tg_execution_mode", "paper")
    test_db.update_system_state("bypass_live_risk_shields", "false")
    assert ExecutionEngine()._should_bypass_portfolio_shields() is True


def test_bypass_decision_live(test_db, monkeypatch):
    """live mod → kalkanlar AKTİF (bypass yok)."""
    monkeypatch.setattr(ee, "_is_pytest_running", lambda: False)
    test_db.update_system_state("tg_execution_mode", "live")
    test_db.update_system_state("bypass_live_risk_shields", "false")
    assert ExecutionEngine()._should_bypass_portfolio_shields() is False


def test_bypass_decision_paper_in_test_is_active(test_db):
    """test içinde paper + flag kapalı → bypass ETME (mevcut testler korunur)."""
    test_db.update_system_state("tg_execution_mode", "paper")
    test_db.update_system_state("bypass_live_risk_shields", "false")
    assert ExecutionEngine()._should_bypass_portfolio_shields() is False


def test_bypass_decision_flag_forces_bypass_even_live(test_db, monkeypatch):
    """BYPASS_LIVE_RISK_SHIELDS açıksa live'da bile bypass."""
    monkeypatch.setattr(ee, "_is_pytest_running", lambda: False)
    test_db.update_system_state("tg_execution_mode", "live")
    test_db.update_system_state("bypass_live_risk_shields", "true")
    assert ExecutionEngine()._should_bypass_portfolio_shields() is True


# ── Entegrasyon: open_paper_trade içindeki gerçek kablolama ───────────────────

def _correlated_setup(monkeypatch, test_db):
    """Korelasyon 1.0 + taze fiyat + 1 açık korele pozisyon kurar."""
    import core.portfolio_risk as pr
    import core.market_data as md
    # _get_returns boş olmasın ki korelasyon hesaplanabilsin (aynı sembol → 1.0)
    monkeypatch.setattr(pr, "_get_returns",
                        lambda *a, **k: np.array([0.01, -0.02, 0.015, -0.01, 0.02, 0.005]))
    monkeypatch.setattr(md, "get_price_age", lambda symbol: 1.0)        # taze
    monkeypatch.setattr(ee, "get_current_price", lambda symbol: 100.0)
    monkeypatch.setattr(test_db, "get_open_trades",
                        lambda *a, **k: [{"symbol": "BTCUSDT", "quantity": 1.0, "entry_price": 100.0}])
    monkeypatch.setattr(test_db, "get_dashboard_stats", lambda *a, **k: {"balance": 2000.0})
    test_db.update_system_state("tg_execution_mode", "paper")
    test_db.update_system_state("bypass_live_risk_shields", "false")


def _new_signal():
    return SignalData(symbol="BTCUSDT", side="LONG", entry_price=100.0,
                      stop_loss=95.0, tp1=110.0, leverage=10, risk_pct=1.0)


def test_correlation_blocks_when_not_bypassed(test_db, monkeypatch):
    """Bypass kapalıyken (test-paper) korelasyon HÂLÂ keser — kalkan kaldırılmadı."""
    _correlated_setup(monkeypatch, test_db)
    monkeypatch.setattr(ee, "_is_pytest_running", lambda: True)  # bypass kapalı
    trade_id = ExecutionEngine().open_paper_trade(_new_signal())
    assert trade_id is None, "korelasyon 1.0 eşiği aşıyor — red beklenir"


def test_paper_mode_opens_correlated_second_trade(test_db, monkeypatch):
    """Production paper modda korelasyon/VaR bypass → 2. korele trade AÇILIR."""
    _correlated_setup(monkeypatch, test_db)
    monkeypatch.setattr(ee, "_is_pytest_running", lambda: False)  # production paper
    monkeypatch.setattr(test_db, "create_trade", lambda *a, **k: 4242)
    trade_id = ExecutionEngine().open_paper_trade(_new_signal())
    assert trade_id is not None, "paper modda korelasyon trade'i kesmemeli (Fix A)"


def test_stale_price_still_rejected_even_when_bypassed(test_db, monkeypatch):
    """Kalkan bypass'ı staleness guard'ını ETKİLEMEMELİ — bayat fiyat hâlâ red."""
    import core.market_data as md
    monkeypatch.setattr(md, "get_price_age", lambda symbol: 500.0)       # bayat
    monkeypatch.setattr(ee, "get_current_price", lambda symbol: None)    # REST de yenileyemiyor
    monkeypatch.setattr(ee, "_is_pytest_running", lambda: False)         # production paper (bypass açık)
    monkeypatch.setattr(test_db, "get_open_trades", lambda *a, **k: [])
    monkeypatch.setattr(test_db, "get_dashboard_stats", lambda *a, **k: {"balance": 2000.0})
    test_db.update_system_state("tg_execution_mode", "paper")
    trade_id = ExecutionEngine().open_paper_trade(_new_signal())
    assert trade_id is None, "bayat fiyat bypass'a rağmen reddedilmeli"


if __name__ == "__main__":
    pytest.main(["-v", __file__])
