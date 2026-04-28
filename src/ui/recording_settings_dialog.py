"""Dialog for microphone recording settings."""

from __future__ import annotations

import logging
import os
from typing import Callable

from PyQt6.QtCore import QSettings, Qt
from PyQt6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMessageBox,
    QPushButton,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from core.config import recordings_dir as default_recordings_dir
from services.microphone_recorder import (
    DEVICE_DEFAULT_SAMPLE_RATE,
    RecordingSettings,
    load_recording_settings,
    save_recording_settings,
)

LOGGER = logging.getLogger(__name__)

_DIALOG_STYLE = """
QDialog { background-color: #1a1a1a; color: #ddd; }
QGroupBox { color: #ddd; border: 1px solid #333; border-radius: 4px; margin-top: 12px; padding-top: 8px; }
QGroupBox::title { subcontrol-origin: margin; left: 10px; padding: 0 4px; color: #aaa; }
QLabel { color: #ccc; }
QComboBox, QSpinBox, QLineEdit {
    background-color: #2a2a2a; color: #eee; border: 1px solid #444;
    border-radius: 3px; padding: 2px 6px; min-width: 140px;
}
QComboBox QAbstractItemView { background-color: #2a2a2a; color: #eee; selection-background-color: #444; }
QPushButton {
    background-color: #2a2a2a; color: #eee; border: 1px solid #444;
    border-radius: 3px; padding: 4px 12px;
}
QPushButton:hover { background-color: #3a3a3a; border-color: #55efc4; }
QPushButton:pressed { background-color: #4a4a4a; }
QCheckBox { color: #ccc; }
"""

_SAMPLE_RATE_OPTIONS = (
    ("Use device default", DEVICE_DEFAULT_SAMPLE_RATE),
    ("22,050 Hz", 22050),
    ("32,000 Hz", 32000),
    ("44,100 Hz", 44100),
    ("48,000 Hz", 48000),
)


class RecordingSettingsDialog(QDialog):
    """Edit persistent settings for microphone recording."""

    def __init__(
        self,
        settings: QSettings,
        on_apply: Callable[[], None] | None = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("Recording Settings")
        self.setStyleSheet(_DIALOG_STYLE)
        self.setMinimumWidth(500)
        self._settings = settings
        self._on_apply = on_apply

        layout = QVBoxLayout(self)
        layout.setContentsMargins(14, 14, 14, 14)
        layout.setSpacing(10)

        group = QGroupBox("Microphone Recording")
        group_layout = QVBoxLayout(group)
        form = QFormLayout()
        form.setLabelAlignment(Qt.AlignmentFlag.AlignRight)
        form.setHorizontalSpacing(14)
        form.setVerticalSpacing(8)

        folder_row = QHBoxLayout()
        self._output_dir = QLineEdit()
        self._output_dir.setPlaceholderText(default_recordings_dir())
        folder_row.addWidget(self._output_dir, 1)
        browse_btn = QPushButton("Browse…")
        browse_btn.clicked.connect(self._browse_output_dir)
        folder_row.addWidget(browse_btn)
        default_btn = QPushButton("Use Default")
        default_btn.clicked.connect(self._use_default_output_dir)
        folder_row.addWidget(default_btn)
        form.addRow("Save folder:", _wrap_layout(folder_row))

        self._prefix = QLineEdit()
        self._prefix.setPlaceholderText("mic_recording")
        form.addRow("Filename prefix:", self._prefix)

        self._bitrate = QSpinBox()
        self._bitrate.setRange(64, 320)
        self._bitrate.setSingleStep(32)
        self._bitrate.setSuffix(" kbps")
        form.addRow("MP3 bitrate:", self._bitrate)

        self._sample_rate = QComboBox()
        for label, value in _SAMPLE_RATE_OPTIONS:
            self._sample_rate.addItem(label, value)
        form.addRow("Sample rate:", self._sample_rate)

        self._channels = QComboBox()
        self._channels.addItem("Mono", 1)
        self._channels.addItem("Stereo if available", 2)
        form.addRow("Channels:", self._channels)

        self._open_folder_after_save = QCheckBox("Open the recordings folder after each saved recording")
        form.addRow("", self._open_folder_after_save)

        note = QLabel(
            "Changes apply to the next recording. The recorder always uses shared input access "
            "and does not request exclusive ownership of the microphone."
        )
        note.setWordWrap(True)
        form.addRow("Note:", note)

        group_layout.addLayout(form)
        layout.addWidget(group)

        button_box = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Close
        )
        save_btn = button_box.button(QDialogButtonBox.StandardButton.Save)
        if save_btn is not None:
            save_btn.setText("Save")
        button_box.accepted.connect(self._on_save)
        button_box.rejected.connect(self.reject)
        layout.addWidget(button_box)

        self._load_into_editor(load_recording_settings(self._settings))

    def _load_into_editor(self, recording: RecordingSettings) -> None:
        self._output_dir.setText(recording.output_dir)
        self._prefix.setText(recording.filename_prefix)
        self._bitrate.setValue(recording.bitrate_kbps)
        sample_index = self._sample_rate.findData(recording.sample_rate_hz)
        self._sample_rate.setCurrentIndex(sample_index if sample_index >= 0 else 0)
        channel_index = self._channels.findData(recording.channels)
        self._channels.setCurrentIndex(channel_index if channel_index >= 0 else 0)
        self._open_folder_after_save.setChecked(recording.open_folder_after_save)

    def _browse_output_dir(self) -> None:
        selected = QFileDialog.getExistingDirectory(
            self,
            "Choose Recording Folder",
            self._output_dir.text().strip() or default_recordings_dir(),
        )
        if selected:
            self._output_dir.setText(selected)

    def _use_default_output_dir(self) -> None:
        self._output_dir.clear()

    def _to_recording_settings(self) -> RecordingSettings:
        return RecordingSettings(
            output_dir=self._output_dir.text().strip(),
            filename_prefix=self._prefix.text().strip(),
            bitrate_kbps=int(self._bitrate.value()),
            sample_rate_hz=int(self._sample_rate.currentData() or DEVICE_DEFAULT_SAMPLE_RATE),
            channels=int(self._channels.currentData() or 1),
            open_folder_after_save=self._open_folder_after_save.isChecked(),
        ).normalized()

    def _on_save(self) -> None:
        try:
            recording = self._to_recording_settings()
            _validate_output_dir(recording.output_dir)
            save_recording_settings(self._settings, recording)
            if self._on_apply is not None:
                self._on_apply()
        except OSError as exc:
            LOGGER.warning("Invalid recording output directory: %s", exc)
            QMessageBox.critical(
                self,
                "Invalid Recording Folder",
                str(exc),
            )
            return
        except Exception:  # pylint: disable=broad-exception-caught
            LOGGER.exception("Failed to save recording settings")
            QMessageBox.critical(
                self,
                "Save Failed",
                "Recording settings could not be saved. Check the log for details.",
            )
            return
        self.accept()


def open_recording_settings_dialog(
    settings: QSettings,
    on_apply: Callable[[], None] | None = None,
    parent: QWidget | None = None,
) -> None:
    dialog = RecordingSettingsDialog(settings, on_apply=on_apply, parent=parent)
    dialog.exec()


def _wrap_layout(layout) -> QWidget:
    widget = QWidget()
    widget.setLayout(layout)
    return widget


def _validate_output_dir(output_dir: str) -> None:
    target = output_dir.strip()
    if not target:
        return
    os.makedirs(target, exist_ok=True)
    if not os.path.isdir(target):
        raise OSError(f"Recording folder is not a directory: {target}")
