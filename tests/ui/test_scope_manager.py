"""Tests for ScopeManager scope construction, updates, alerts, and telemetry."""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from PyQt6.QtCore import QSettings
from PyQt6.QtWidgets import QHBoxLayout, QWidget

from services.sensors.models import SensorReading
from ui.scope_manager import ScopeManager


def _gpu(util=None, vram=None, temp=None):
    return SimpleNamespace(util_percent=util, vram_percent=vram, temp_c=temp)


@pytest.fixture
def manager(qtbot, tmp_path):
    settings = QSettings(str(tmp_path / "s.ini"), QSettings.Format.IniFormat)
    host = QWidget()
    qtbot.addWidget(host)
    layout = QHBoxLayout(host)
    notes: list[tuple[str, str]] = []
    mgr = ScopeManager(layout, settings, gpu_available=True, temp_available=True,
                       notify=lambda t, m: notes.append((t, m)))
    mgr.build()
    mgr._notes = notes  # type: ignore[attr-defined]
    mgr._host = host  # keep the parent widget (and its scope children) alive
    return mgr


def test_build_includes_gputemp_and_ssdtemp(manager):
    assert "gputemp" in manager.scopes
    assert "ssdtemp" in manager.scopes
    assert "temp" in manager.scopes


def test_update_renders_celsius(manager):
    reading = SensorReading(cpu_temp_c=55.0, ram_temp_c=40.0, ssd_temp_c=45.0)
    manager.update([5.0], 5.0, 30.0, 100.0, 200.0, 300.0, _gpu(temp=61.0), reading)
    assert manager.scopes["ssdtemp"].display_text == "45°C"
    assert manager.scopes["gputemp"].display_text == "61°C"  # NVML fallback
    assert "CPU 55°C" in manager.scopes["temp"].display_text


def test_ssd_threshold_breach_fires_one_alert_and_flags_scope(manager):
    hot = SensorReading(ssd_temp_c=85.0)
    # debounce is 5s; force immediate firing by resetting debounce
    manager._alerts._debounce = 0.0
    manager.update([5.0], 5.0, 30.0, 0.0, 0.0, 0.0, _gpu(), hot)
    assert manager.scopes["ssdtemp"].alert is True
    assert any("SSD" in msg for _t, msg in manager._notes)


def test_telemetry_append_when_enabled(qtbot, tmp_path):
    settings = QSettings(str(tmp_path / "s.ini"), QSettings.Format.IniFormat)
    settings.setValue("telemetry/enabled", 1)
    settings.sync()
    host = QWidget()
    qtbot.addWidget(host)
    mgr = ScopeManager(QHBoxLayout(host), settings, True, True, lambda t, m: None)
    mgr.build()
    mgr.update([5.0], 5.0, 30.0, 0.0, 0.0, 0.0, _gpu(), SensorReading(cpu_temp_c=50.0))
    # The telemetry file is created under app_data_dir; just assert the sink exists.
    assert mgr._telemetry is not None
