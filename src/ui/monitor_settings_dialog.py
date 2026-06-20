"""Unified Monitor Settings dialog.

One place to edit the update interval, background opacity, sensor source, thermal
thresholds and alerting, and telemetry logging. Values persist to ``QSettings``
under the ``sensors/`` and ``telemetry/`` groups; ``on_apply`` is invoked so the
running monitor can reload the sensor hub and scope manager.
"""

from __future__ import annotations

from collections.abc import Callable

from PyQt6.QtCore import QSettings
from PyQt6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QDialog,
    QDialogButtonBox,
    QFormLayout,
    QLabel,
    QSpinBox,
    QWidget,
)

from core.config import read_setting_int

_SOURCES = ("auto", "clr", "http")
_FORMATS = ("csv", "jsonl")
_THRESHOLDS = (
    ("sensors/threshold_cpu_c", "CPU alert (°C)", 95),
    ("sensors/threshold_ram_c", "RAM alert (°C)", 70),
    ("sensors/threshold_gpu_c", "GPU alert (°C)", 90),
    ("sensors/threshold_ssd_c", "SSD alert (°C)", 80),
)


class MonitorSettingsDialog(QDialog):
    """Edit interval, sensor source, thresholds, alerts, and telemetry."""

    def __init__(self, settings: QSettings, on_apply: Callable[[], None] | None = None,
                 parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Monitor Settings")
        self._settings = settings
        self._on_apply = on_apply
        form = QFormLayout(self)

        self._source = QComboBox()
        self._source.addItems(_SOURCES)
        current_source = str(settings.value("sensors/source", "auto"))
        src_index = _SOURCES.index(current_source) if current_source in _SOURCES else 0
        self._source.setCurrentIndex(src_index)
        form.addRow(QLabel("Sensor source"), self._source)

        self._alerts = QCheckBox("Enable thermal alerts")
        self._alerts.setChecked(bool(read_setting_int(settings, "sensors/alerts_enabled", 1)))
        form.addRow(self._alerts)

        self._threshold_spins: dict[str, QSpinBox] = {}
        for key, label, default in _THRESHOLDS:
            spin = QSpinBox()
            spin.setRange(0, 130)
            spin.setValue(read_setting_int(settings, key, default))
            self._threshold_spins[key] = spin
            form.addRow(QLabel(label), spin)

        self._telemetry = QCheckBox("Log telemetry to a file")
        self._telemetry.setChecked(bool(read_setting_int(settings, "telemetry/enabled", 0)))
        form.addRow(self._telemetry)

        self._format = QComboBox()
        self._format.addItems(_FORMATS)
        current_fmt = str(settings.value("telemetry/format", "csv"))
        self._format.setCurrentIndex(_FORMATS.index(current_fmt) if current_fmt in _FORMATS else 0)
        form.addRow(QLabel("Telemetry format"), self._format)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self._apply)
        buttons.rejected.connect(self.reject)
        form.addRow(buttons)

    def _apply(self) -> None:
        """Persist all fields, fire on_apply, and accept the dialog."""
        self._settings.setValue("sensors/source", self._source.currentText())
        self._settings.setValue("sensors/alerts_enabled", 1 if self._alerts.isChecked() else 0)
        for key, spin in self._threshold_spins.items():
            self._settings.setValue(key, spin.value())
        self._settings.setValue("telemetry/enabled", 1 if self._telemetry.isChecked() else 0)
        self._settings.setValue("telemetry/format", self._format.currentText())
        self._settings.sync()
        if self._on_apply is not None:
            self._on_apply()
        self.accept()


def open_monitor_settings_dialog(settings: QSettings, on_apply: Callable[[], None] | None = None,
                                 parent: QWidget | None = None) -> MonitorSettingsDialog:
    """Construct, show, and return the Monitor Settings dialog."""
    dialog = MonitorSettingsDialog(settings, on_apply, parent)
    dialog.exec()
    return dialog
