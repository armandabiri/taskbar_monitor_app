from __future__ import annotations

from pathlib import Path

from PyQt6.QtCore import QSettings

from services.resource_control.profiles import (
    DEFAULT_AGGRESSIVE_NAME,
    load_active_aggressive_profile,
    load_profile,
)
from ui.resource_settings_dialog import ResourceSettingsDialog


def _build_settings(tmp_path: Path) -> QSettings:
    return QSettings(str(tmp_path / "resource-settings.ini"), QSettings.Format.IniFormat)


def test_resource_settings_dialog_reopens_last_edited_profile_with_saved_values(
    qtbot, tmp_path: Path,
) -> None:
    settings = _build_settings(tmp_path)

    dialog = ResourceSettingsDialog(settings)
    qtbot.addWidget(dialog)
    dialog._select_in_combo(dialog._editor_combo, "Aggressive")
    dialog._load_profile_into_editor("Aggressive")
    dialog._trim_threshold.setValue(321)
    dialog._max_trim.setValue(5)
    dialog._keep_list.setText("persist.exe")
    dialog._on_save()

    reloaded = load_profile(settings, "Aggressive")
    assert reloaded.trim_threshold_mb == 321.0
    assert reloaded.max_trim_per_run == 5
    assert reloaded.always_spare_names == "persist.exe"

    reopened = ResourceSettingsDialog(settings)
    qtbot.addWidget(reopened)
    assert reopened._editor_combo.currentText() == "Aggressive"
    assert reopened._trim_threshold.value() == 321
    assert reopened._max_trim.value() == 5
    assert reopened._keep_list.text() == "persist.exe"


def test_aggressive_binding_defaults_to_nuclear_when_unset(tmp_path: Path) -> None:
    settings = _build_settings(tmp_path)

    assert DEFAULT_AGGRESSIVE_NAME == "Nuclear"
    assert load_active_aggressive_profile(settings).name == "Nuclear"


def test_legacy_aggressive_binding_migrates_to_nuclear(tmp_path: Path) -> None:
    settings = _build_settings(tmp_path)
    settings.setValue("resource_control/active_aggressive_profile", "Aggressive")
    settings.sync()

    assert load_active_aggressive_profile(settings).name == "Nuclear"
    assert settings.value("resource_control/active_aggressive_profile") == "Nuclear"
