"""Tests for the Monitor Settings dialog persistence."""

from __future__ import annotations

from PyQt6.QtCore import QSettings

from core.config import read_setting_int
from ui.monitor_settings_dialog import MonitorSettingsDialog


def test_ssd_threshold_round_trips(qtbot, tmp_path):
    settings = QSettings(str(tmp_path / "s.ini"), QSettings.Format.IniFormat)
    applied = []
    dialog = MonitorSettingsDialog(settings, on_apply=lambda: applied.append(True))
    qtbot.addWidget(dialog)
    dialog._threshold_spins["sensors/threshold_ssd_c"].setValue(83)
    dialog._source.setCurrentText("clr")
    dialog._telemetry.setChecked(True)
    dialog._apply()
    assert read_setting_int(settings, "sensors/threshold_ssd_c", 80) == 83
    assert str(settings.value("sensors/source")) == "clr"
    assert read_setting_int(settings, "telemetry/enabled", 0) == 1
    assert applied == [True]
