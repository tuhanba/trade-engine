"""
tests/test_ghost_price_freshness.py
===================================
Fix G REGRESYON TESTİ — Ghost sonuç simülasyonu fiyat-tazeliği guard'ı.

Bug: process_pending_results yalnız cached WS fiyatlarıyla simüle ediyordu; WS
boşluğunda bayat fiyatla tp1_hit/sl_hit yanlış etiketleniyor → bozuk öğrenme.

Fix: _process_single bayat cache fiyatı tespit edince taze REST dener; o da
yoksa kaydı ERTELER (finalize ETMEZ).
"""
from datetime import datetime, timezone

import core.ghost_learning as gl
import core.market_data as md
import database


def _record():
    return {
        "id": 1,
        "symbol": "BTCUSDT",
        "direction": "LONG",
        "preview_entry": 100.0,
        "preview_sl": 95.0,
        "preview_tp1": 110.0,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "max_favorable_excursion": 0.0,
        "max_adverse_excursion": 0.0,
        "tracked_from": "VETO",
    }


def test_stale_price_without_rest_defers(monkeypatch):
    """Bayat cache + REST yok → kayıt finalize EDİLMEZ (ertelenir)."""
    calls = []
    monkeypatch.setattr(database, "update_paper_result", lambda *a, **k: calls.append((a, k)))
    monkeypatch.setattr(md, "get_price_age", lambda symbol: 999.0)   # bayat
    monkeypatch.setattr(gl, "_get_price", lambda client, symbol: 0.0)  # REST yok

    now = datetime.now(timezone.utc)
    # cache fiyatı 111 → normalde tp1_hit; ama bayat olduğu için işlenmemeli
    gl._process_single(_record(), client=None, now=now, prices={"BTCUSDT": 111.0})

    assert calls == [], "Bayat fiyatla ghost sonucu yanlış finalize edildi"


def test_stale_cache_uses_fresh_rest(monkeypatch):
    """Bayat cache ama taze REST var → REST fiyatıyla doğru finalize."""
    calls = []
    monkeypatch.setattr(database, "update_paper_result", lambda rid, upd: calls.append((rid, upd)))
    monkeypatch.setattr(md, "get_price_age", lambda symbol: 999.0)     # cache bayat
    monkeypatch.setattr(gl, "_get_price", lambda client, symbol: 111.0)  # REST taze → tp1

    now = datetime.now(timezone.utc)
    # cache fiyatı 90 (yanlış: sl_hit derdi); REST 111 (doğru: tp1_hit)
    gl._process_single(_record(), client=None, now=now, prices={"BTCUSDT": 90.0})

    assert len(calls) == 1
    assert calls[0][1].get("hit_tp") == 1, "Taze REST fiyatıyla tp1_hit beklenirdi"


def test_fresh_cache_finalizes_normally(monkeypatch):
    """Taze cache fiyatı → normal finalize (guard araya girmez)."""
    calls = []
    monkeypatch.setattr(database, "update_paper_result", lambda rid, upd: calls.append((rid, upd)))
    monkeypatch.setattr(md, "get_price_age", lambda symbol: 5.0)       # taze
    # REST çağrılmamalı; çağrılırsa testi bozmasın diye yine de patch'le
    monkeypatch.setattr(gl, "_get_price", lambda client, symbol: 0.0)

    now = datetime.now(timezone.utc)
    gl._process_single(_record(), client=None, now=now, prices={"BTCUSDT": 111.0})

    assert len(calls) == 1
    assert calls[0][1].get("hit_tp") == 1
