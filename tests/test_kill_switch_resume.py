"""
tests/test_kill_switch_resume.py — Kill switch pause/resume test
"""
import asyncio
import sys
from unittest.mock import MagicMock, patch
import pytest

# Mock aiohttp before importing scanner
sys.modules.setdefault("aiohttp", MagicMock())

from core.event_types import Event, EventType


def _new_svc():
    """Create a fresh ScannerService with mocked AsyncMarketScanner."""
    with patch("core.services.scanner_service.AsyncMarketScanner", MagicMock):
        from core.services import scanner_service as _sm
        import importlib
        importlib.reload(_sm)
        return _sm.ScannerService(interval_seconds=1)


def test_kill_switch_sets_paused():
    svc = _new_svc()
    assert svc._paused is False
    asyncio.run(svc.handle_kill_switch(Event(type=EventType.KILL_SWITCH_ACTIVATED, payload={})))
    assert svc._paused is True


def test_kill_switch_deactivated_clears_paused():
    svc = _new_svc()
    svc._paused = True
    asyncio.run(svc.handle_kill_switch_deactivated(Event(type=EventType.KILL_SWITCH_DEACTIVATED, payload={})))
    assert svc._paused is False


def test_pause_resume_cycle():
    svc = _new_svc()
    asyncio.run(svc.handle_kill_switch(Event(type=EventType.KILL_SWITCH_ACTIVATED, payload={})))
    assert svc._paused is True
    asyncio.run(svc.handle_kill_switch_deactivated(Event(type=EventType.KILL_SWITCH_DEACTIVATED, payload={})))
    assert svc._paused is False
