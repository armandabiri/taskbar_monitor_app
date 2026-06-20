"""Dialog for screenshot output and scroll capture options."""

from __future__ import annotations

import logging
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
    QLineEdit,
    QMessageBox,
    QPushButton,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from core.config import recordings_dir as default_screenshots_dir
from services.screenshot_settings import (
    ScreenshotSettings,
    load_screenshot_settings,
    save_screenshot_settings,
)

LOGGER = logging.getLogger(__name__)

_DIALOG_STYLE = """
QDialog { background-color: #1a1a1a; color: #ddd; }
QGroupBox {
    color: #ddd; border: 1px solid #333; border-radius: 4px;
    margin-top: 12px; padding-top: 8px;
}
QGroupBox::title { subcontrol-origin: margin; left: 10px; padding: 0 4px; color: #aaa; }
QLabel { color: #ccc; }
QComboBox, QSpinBox, QLineEdit {
    background-color: #2a2a2a; color: #eee; border: 1px solid #444;
    border-radius: 3px; padding: 2px 6px; min-width: 140px;
}
QPushButton {
    background-color: #2a2a2a; color: #eee; border: 1px solid #444;
    border-radius: 3px; padding: 4px 12px;
}
QPushButton:hover { background-color: #3a3a3a; border-color: #55efc4; }
QCheckBox { color: #ccc; }
"""


class ScreenshotSettingsDialog(QDialog):
    """Edit persistent screenshot settings."""

    def __init__(
        self,
        settings: QSettings,
        on_apply: Callable[[], None] | None = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("Screenshot Settings")
        self.setStyleSheet(_DIALOG_STYLE)
        self.setMinimumWidth(480)
        self._settings = settings
        self._on_apply = on_apply

        layout = QVBoxLayout(self)
        layout.setContentsMargins(14, 14, 14, 14)

        output_group = QGroupBox("Output")
        form = QFormLayout(output_group)
        form.setLabelAlignment(Qt.AlignmentFlag.AlignRight)

        folder_row = QHBoxLayout()
        self._output_dir = QLineEdit()
        self._output_dir.setPlaceholderText(default_screenshots_dir())
        folder_row.addWidget(self._output_dir, 1)
        browse_btn = QPushButton("Browse…")
        browse_btn.clicked.connect(self._browse_output_dir)
        folder_row.addWidget(browse_btn)
        form.addRow("Save folder:", folder_row)

        self._format = QComboBox()
        self._format.addItem("PNG", "png")
        self._format.addItem("JPEG", "jpeg")
        form.addRow("File format:", self._format)

        self._copy_clipboard = QCheckBox("Copy captures to clipboard")
        form.addRow("", self._copy_clipboard)
        self._save_file = QCheckBox("Save captures to disk")
        form.addRow("", self._save_file)

        layout.addWidget(output_group)

        capture_group = QGroupBox("Capture")
        capture_form = QFormLayout(capture_group)
        self._capture_delay = QSpinBox()
        self._capture_delay.setRange(0, 10)
        self._capture_delay.setSuffix(" s")
        capture_form.addRow("Delay before capture:", self._capture_delay)
        self._auto_open_editor = QCheckBox("Open the editor after each capture")
        capture_form.addRow("", self._auto_open_editor)
        layout.addWidget(capture_group)

        scroll_group = QGroupBox("Scrolling capture")
        scroll_form = QFormLayout(scroll_group)
        self._scroll_delay = QSpinBox()
        self._scroll_delay.setRange(120, 2000)
        self._scroll_delay.setSuffix(" ms")
        scroll_form.addRow("Delay between scroll steps:", self._scroll_delay)
        self._debug_frames = QCheckBox("Write scroll debug frames to .intelag/reports/scroll_live")
        scroll_form.addRow("", self._debug_frames)
        layout.addWidget(scroll_group)

        button_box = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Close
        )
        save_btn = button_box.button(QDialogButtonBox.StandardButton.Save)
        if save_btn is not None:
            save_btn.setText("Save")
        button_box.accepted.connect(self._on_save)
        button_box.rejected.connect(self.reject)
        layout.addWidget(button_box)

        self._load_into_editor(load_screenshot_settings(self._settings))

    def _load_into_editor(self, shot: ScreenshotSettings) -> None:
        self._output_dir.setText(shot.output_dir)
        fmt_index = self._format.findData(shot.image_format)
        self._format.setCurrentIndex(fmt_index if fmt_index >= 0 else 0)
        self._copy_clipboard.setChecked(shot.copy_enabled)
        self._save_file.setChecked(shot.save_enabled)
        self._scroll_delay.setValue(shot.scroll_delay_ms)
        self._debug_frames.setChecked(shot.debug_frames)
        self._capture_delay.setValue(shot.capture_delay_s)
        self._auto_open_editor.setChecked(shot.auto_open_editor)

    def _browse_output_dir(self) -> None:
        selected = QFileDialog.getExistingDirectory(
            self,
            "Choose Screenshot Folder",
            self._output_dir.text().strip() or default_screenshots_dir(),
        )
        if selected:
            self._output_dir.setText(selected)

    def _to_settings(self) -> ScreenshotSettings:
        return ScreenshotSettings(
            output_dir=self._output_dir.text().strip(),
            image_format=str(self._format.currentData() or "png"),
            copy_enabled=self._copy_clipboard.isChecked(),
            save_enabled=self._save_file.isChecked(),
            scroll_delay_ms=int(self._scroll_delay.value()),
            debug_frames=self._debug_frames.isChecked(),
            capture_delay_s=int(self._capture_delay.value()),
            auto_open_editor=self._auto_open_editor.isChecked(),
        ).normalized()

    def _on_save(self) -> None:
        try:
            shot = self._to_settings()
            save_screenshot_settings(self._settings, shot)
            if self._on_apply is not None:
                self._on_apply()
        except OSError as exc:
            QMessageBox.critical(self, "Save Failed", str(exc))
            return
        except Exception:  # pylint: disable=broad-exception-caught
            LOGGER.exception("Failed to save screenshot settings")
            QMessageBox.critical(self, "Save Failed", "Could not save screenshot settings.")
            return
        self.accept()


def open_screenshot_settings_dialog(
    settings: QSettings,
    on_apply: Callable[[], None] | None = None,
    parent: QWidget | None = None,
) -> None:
    dialog = ScreenshotSettingsDialog(settings, on_apply=on_apply, parent=parent)
    dialog.exec()
