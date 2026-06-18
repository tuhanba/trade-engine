"""Regression tests for the 7-condition live safety gate (directive Section 18).

P0-1: ``config.is_live_trading_allowed()`` must fail CLOSED — live trading is
impossible unless ALL seven conditions hold AND ``core.live_readiness.check()``
reports ready.

NOTE on EXECUTION_MODE
----------------------
The gate reads the mode via ``config.__getattr__("EXECUTION_MODE")`` — the
Redis/DB-first *dynamic* resolver — NOT the static module global. In the test
environment the DB has no ``system_state`` row, so the resolver falls back to
the frozen static default (``"paper"``, forced by conftest). Monkeypatching the
module global ``EXECUTION_MODE`` alone therefore does NOT flip the mode, and
every assertion below would pass *trivially* at the first check without ever
exercising the DRY_RUN / private-API / readiness branches. ``_force_mode()``
patches the resolver so the gate actually advances to the condition under test,
and each blocking test forces readiness PASS so the *named* condition is the
sole reason the gate returns False.
"""

import importlib
import config as cfg


def _reload():
    importlib.reload(cfg)
    return cfg


def _force_mode(monkeypatch, c, mode):
    """Make the dynamic resolver report EXECUTION_MODE == ``mode``."""
    orig = c.__dict__["__getattr__"]

    def _fake(name):
        if name == "EXECUTION_MODE":
            return mode
        return orig(name)

    monkeypatch.setattr(c, "__getattr__", _fake, raising=False)


def _set_flags(monkeypatch, c, *, dry_run=False, private_api=True,
               live_enabled=True, confirm=True):
    monkeypatch.setattr(c, "DRY_RUN", dry_run, raising=False)
    monkeypatch.setattr(c, "LIVE_TRADING_ENABLED", live_enabled, raising=False)
    monkeypatch.setattr(c, "CONFIRM_LIVE_TRADING", confirm, raising=False)
    monkeypatch.setattr(c, "USE_BINANCE_PRIVATE_API", private_api, raising=False)


def _patch_readiness(monkeypatch, ready):
    import core.live_readiness as lr
    monkeypatch.setattr(lr, "check", lambda: {"ready": ready, "gates": []})


# --- each of the 5 env-flag conditions, isolated (readiness forced PASS) ------

def test_gate_blocks_when_execution_mode_not_live(monkeypatch):
    c = _reload()
    _set_flags(monkeypatch, c)
    _force_mode(monkeypatch, c, "paper")
    _patch_readiness(monkeypatch, ready=True)
    assert c.is_live_trading_allowed() is False


def test_gate_blocks_when_live_trading_disabled(monkeypatch):
    c = _reload()
    _set_flags(monkeypatch, c, live_enabled=False)
    _force_mode(monkeypatch, c, "live")
    _patch_readiness(monkeypatch, ready=True)
    assert c.is_live_trading_allowed() is False


def test_gate_blocks_when_dry_run(monkeypatch):
    c = _reload()
    _set_flags(monkeypatch, c, dry_run=True)
    _force_mode(monkeypatch, c, "live")
    _patch_readiness(monkeypatch, ready=True)   # isolate DRY_RUN as the blocker
    assert c.is_live_trading_allowed() is False


def test_gate_blocks_when_confirm_off(monkeypatch):
    c = _reload()
    _set_flags(monkeypatch, c, confirm=False)
    _force_mode(monkeypatch, c, "live")
    _patch_readiness(monkeypatch, ready=True)
    assert c.is_live_trading_allowed() is False


def test_gate_blocks_when_private_api_off(monkeypatch):
    c = _reload()
    _set_flags(monkeypatch, c, private_api=False)
    _force_mode(monkeypatch, c, "live")
    _patch_readiness(monkeypatch, ready=True)   # isolate private-API as blocker
    assert c.is_live_trading_allowed() is False


# --- the proof-of-edge / readiness condition ----------------------------------

def test_gate_blocks_when_readiness_not_ready(monkeypatch):
    c = _reload()
    _set_flags(monkeypatch, c)                  # all 5 env flags satisfied
    _force_mode(monkeypatch, c, "live")
    _patch_readiness(monkeypatch, ready=False)
    assert c.is_live_trading_allowed() is False


def test_gate_fails_closed_on_error(monkeypatch):
    c = _reload()
    _set_flags(monkeypatch, c)                  # all 5 env flags satisfied
    _force_mode(monkeypatch, c, "live")
    import core.live_readiness as lr

    def boom():
        raise RuntimeError("db down")

    monkeypatch.setattr(lr, "check", boom)
    assert c.is_live_trading_allowed() is False


# --- positive control: proves the suite is NOT vacuously always-False ---------

def test_gate_allows_only_when_all_conditions_pass(monkeypatch):
    c = _reload()
    _set_flags(monkeypatch, c)                  # all 5 env flags satisfied
    _force_mode(monkeypatch, c, "live")
    _patch_readiness(monkeypatch, ready=True)   # readiness PASS
    assert c.is_live_trading_allowed() is True
