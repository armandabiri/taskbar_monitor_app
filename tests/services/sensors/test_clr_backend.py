"""Tests for the embedded CLR backend's graceful-degradation path.

The DLL may or may not be present in a given environment, so these force the
absent path by pointing the loader at a nonexistent DLL.
"""

from __future__ import annotations

import pytest

from services.sensors import lhm_clr_loader
from services.sensors.lhm_clr_backend import LhmClrBackend


@pytest.fixture(autouse=True)
def _no_dll(monkeypatch, tmp_path):
    monkeypatch.setattr(lhm_clr_loader, "dll_path", lambda: tmp_path / "missing.dll")


def test_unavailable_when_dll_absent():
    backend = LhmClrBackend()
    assert backend.available() is False


def test_read_returns_empty_when_unavailable():
    backend = LhmClrBackend()
    reading = backend.read()
    assert not reading.has_any_temp()
    assert backend.id == "lhm-clr"


def test_status_reports_unavailable_detail():
    backend = LhmClrBackend()
    backend.available()
    status = backend.status()
    assert status.available is False
    assert status.backend_id == "lhm-clr"
