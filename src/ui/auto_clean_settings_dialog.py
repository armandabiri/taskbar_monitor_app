"""Small dialog to configure the auto-clean watchdog."""

from __future__ import annotations

from typing import Callable

from PyQt6.QtCore import QSettings
from PyQt6.QtWidgets import (
    QCheckBox,
    QDialog,
    QDialogButtonBox,
    QDoubleSpinBox,
    QFormLayout,
    QLabel,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from services.auto_clean_watchdog import (
    AutoCleanConfig,
    load_auto_clean_config,
    save_auto_clean_config,
)

_DIALOG_STYLE = """
QDialog { background-color: #1a1a1a; color: #ddd; }
QLabel { color: #ccc; }
QCheckBox { color: #ccc; }
QSpinBox, QDoubleSpinBox {
    background-color: #2a2a2a; color: #eee; border: 1px solid #444;
    border-radius: 3px; padding: 2px 6px; min-width: 110px;
}
QPushButton {
    background-color: #2a2a2a; color: #eee; border: 1px solid #444;
    border-radius: 3px; padding: 4px 12px;
}
QPushButton:hover { background-color: #3a3a3a; border-color: #55efc4; }
"""


class AutoCleanSettingsDialog(QDialog):
    """Enable / tune the RAM-pressure auto-clean watchdog."""

    def __init__(
        self,
        settings: QSettings,
        on_apply: Callable[[], None] | None = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._settings = settings
        self._on_apply = on_apply
        self.setWindowTitle("Auto-Clean Settings")
        self.setStyleSheet(_DIALOG_STYLE)
        self.setMinimumWidth(380)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(14, 14, 14, 14)
        layout.setSpacing(10)

        intro = QLabel(
            "Automatically run a forced Smart cleanup when memory stays under "
            "pressure. Disabled by default."
        )
        intro.setWordWrap(True)
        layout.addWidget(intro)

        form = QFormLayout()
        self._enabled = QCheckBox("Enable auto-clean")
        form.addRow("", self._enabled)

        self._threshold = QDoubleSpinBox()
        self._threshold.setRange(50.0, 99.0)
        self._threshold.setDecimals(0)
        self._threshold.setSuffix(" %")
        self._threshold.setToolTip("Fire when memory used stays at/above this percent.")
        form.addRow("RAM used at/above:", self._threshold)

        self._debounce = QSpinBox()
        self._debounce.setRange(5, 600)
        self._debounce.setSuffix(" s")
        self._debounce.setToolTip("How long memory must stay high before firing.")
        form.addRow("Sustained for:", self._debounce)

        self._cooldown = QSpinBox()
        self._cooldown.setRange(30, 3600)
        self._cooldown.setSuffix(" s")
        self._cooldown.setToolTip("Minimum gap between two automatic cleanups.")
        form.addRow("Cooldown:", self._cooldown)
        layout.addLayout(form)

        config = load_auto_clean_config(settings)
        self._enabled.setChecked(config.enabled)
        self._threshold.setValue(config.threshold_percent)
        self._debounce.setValue(int(config.debounce_seconds))
        self._cooldown.setValue(int(config.cooldown_seconds))

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel
        )
        save_btn = buttons.button(QDialogButtonBox.StandardButton.Save)
        if save_btn is not None:
            save_btn.setText("Save && Apply")
        buttons.accepted.connect(self._on_save)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def _on_save(self) -> None:
        config = AutoCleanConfig(
            enabled=self._enabled.isChecked(),
            threshold_percent=float(self._threshold.value()),
            debounce_seconds=float(self._debounce.value()),
            cooldown_seconds=float(self._cooldown.value()),
        )
        save_auto_clean_config(self._settings, config)
        if self._on_apply is not None:
            self._on_apply()
        self.accept()


def open_auto_clean_settings_dialog(
    settings: QSettings,
    on_apply: Callable[[], None] | None = None,
    parent: QWidget | None = None,
) -> None:
    dialog = AutoCleanSettingsDialog(settings, on_apply=on_apply, parent=parent)
    dialog.exec()
