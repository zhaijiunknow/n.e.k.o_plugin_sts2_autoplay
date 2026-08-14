from __future__ import annotations

from pathlib import Path

import pytest
from plugin.plugins.sts2_autoplay.qt_overlay_manager import QtOverlayManager


class DummyLogger:
    def info(self, *args, **kwargs):
        return None

    def warning(self, *args, **kwargs):
        return None


class FakeProc:
    def __init__(self, alive: bool = True, pid: int = 123) -> None:
        self._alive = alive
        self.pid = pid

    def poll(self):
        return None if self._alive else 0

    def terminate(self) -> None:
        self._alive = False

    def wait(self, timeout=None) -> None:
        self._alive = False


def _make_manager() -> QtOverlayManager:
    return QtOverlayManager(DummyLogger(), plugin_id="sts2_autoplay", plugin_dir=Path("."))


@pytest.mark.unit
def test_overlay_status_when_not_started() -> None:
    m = _make_manager()
    st = m.status()
    assert st["ok"] is True
    assert st["running"] is False
    assert st["pid"] is None


@pytest.mark.unit
def test_overlay_stop_when_not_started() -> None:
    m = _make_manager()
    r = m.stop()
    assert r["was_running"] is False


@pytest.mark.unit
def test_overlay_status_when_running() -> None:
    m = _make_manager()
    m._proc = FakeProc()
    st = m.status()
    assert st["running"] is True
    assert st["pid"] == 123


@pytest.mark.unit
def test_overlay_stop_terminates_process() -> None:
    m = _make_manager()
    proc = FakeProc()
    m._proc = proc
    r = m.stop()
    assert r["was_running"] is True
    assert m._proc is None  # 清空引用


@pytest.mark.unit
def test_overlay_start_already_running() -> None:
    m = _make_manager()
    m._proc = FakeProc()
    r = m.start()
    assert r["already_running"] is True
