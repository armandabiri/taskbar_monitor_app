from __future__ import annotations

from pathlib import Path

from PyQt6.QtCore import QSettings

from services.microphone_recorder import load_recording_settings
from ui.recording_settings_dialog import RecordingSettingsDialog


def _build_settings(tmp_path: Path) -> QSettings:
    return QSettings(str(tmp_path / "recording-settings.ini"), QSettings.Format.IniFormat)


def test_recording_settings_dialog_persists_and_reloads_values(qtbot, tmp_path: Path) -> None:
    settings = _build_settings(tmp_path)

    dialog = RecordingSettingsDialog(settings)
    qtbot.addWidget(dialog)
    dialog._output_dir.setText(str(tmp_path / "captures"))
    dialog._prefix.setText("podcast raw")
    dialog._bitrate.setValue(192)
    dialog._sample_rate.setCurrentIndex(dialog._sample_rate.findData(48000))
    dialog._channels.setCurrentIndex(dialog._channels.findData(2))
    dialog._open_folder_after_save.setChecked(True)
    dialog._on_save()

    loaded = load_recording_settings(settings)
    assert loaded.output_dir == str(tmp_path / "captures")
    assert loaded.filename_prefix == "podcast_raw"
    assert loaded.bitrate_kbps == 192
    assert loaded.sample_rate_hz == 48000
    assert loaded.channels == 2
    assert loaded.open_folder_after_save is True

    reopened = RecordingSettingsDialog(settings)
    qtbot.addWidget(reopened)
    assert reopened._output_dir.text() == str(tmp_path / "captures")
    assert reopened._prefix.text() == "podcast_raw"
    assert reopened._bitrate.value() == 192
    assert reopened._sample_rate.currentData() == 48000
    assert reopened._channels.currentData() == 2
    assert reopened._open_folder_after_save.isChecked() is True
