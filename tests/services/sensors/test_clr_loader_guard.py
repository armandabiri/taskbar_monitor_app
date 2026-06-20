"""Tests for the CLR loader's fail-soft bootstrap guard."""

from __future__ import annotations

import logging

from services.sensors import lhm_clr_loader
from services.sensors.lhm_clr_backend import LhmClrBackend


def test_missing_dll_logs_unavailable(monkeypatch, caplog, tmp_path):
    # Force the DLL-absent path so the guard runs regardless of the environment.
    monkeypatch.setattr(lhm_clr_loader, "dll_path", lambda: tmp_path / "missing.dll")
    with caplog.at_level(logging.INFO):
        result = lhm_clr_loader.load_computer()
    assert result is None
    assert any("clr backend unavailable" in r.message for r in caplog.records)


def test_backend_reports_unavailable_on_guard_failure(monkeypatch, tmp_path):
    monkeypatch.setattr(lhm_clr_loader, "dll_path", lambda: tmp_path / "missing.dll")
    backend = LhmClrBackend()
    assert backend.available() is False
    assert backend.read().has_any_temp() is False
    assert backend.status().available is False


def test_dll_path_points_into_assets_sensors():
    assert "assets" in str(lhm_clr_loader.dll_path()).lower()
    assert str(lhm_clr_loader.dll_path()).endswith("LibreHardwareMonitorLib.dll")
