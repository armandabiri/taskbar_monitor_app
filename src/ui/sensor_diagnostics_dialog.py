"""Sensor Diagnostics dialog.

Shows which backend the SensorHub is using and the live per-sensor readings, so a
user can tell whether (and why) a temperature reads N/A. Refreshes on a 1 s timer
while open.
"""

from __future__ import annotations

from PyQt6.QtCore import QTimer
from PyQt6.QtWidgets import (
    QDialog,
    QLabel,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from services.sensors.models import SensorKind
from services.win_elevation import is_elevated

_ROWS = (
    ("CPU", SensorKind.CPU_TEMP),
    ("RAM", SensorKind.RAM_TEMP),
    ("GPU", SensorKind.GPU_TEMP),
    ("SSD", SensorKind.SSD_TEMP),
)


class SensorDiagnosticsDialog(QDialog):
    """Live view of the active backend and per-sensor temperature readings."""

    def __init__(self, hub, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Sensor Diagnostics")
        self._hub = hub
        layout = QVBoxLayout(self)

        self._backend_label = QLabel()
        layout.addWidget(self._backend_label)

        self._elevation_label = QLabel()
        self._elevation_label.setWordWrap(True)
        layout.addWidget(self._elevation_label)

        self._table = QTableWidget(len(_ROWS), 2)
        self._table.setHorizontalHeaderLabels(["Sensor", "Temperature"])
        self._table.verticalHeader().setVisible(False)
        layout.addWidget(self._table)

        self._backends_label = QLabel()
        self._backends_label.setWordWrap(True)
        layout.addWidget(self._backends_label)

        self._timer = QTimer(self)
        self._timer.setInterval(1000)
        self._timer.timeout.connect(self.refresh)
        self._timer.start()
        self.refresh()

    def refresh(self) -> None:
        """Repopulate the backend label and the per-sensor table."""
        reading = self._hub.snapshot()
        self._backend_label.setText(f"Active backend: {self._hub.active_backend_id()}")
        if is_elevated():
            self._elevation_label.setText("Administrator: yes")
        else:
            self._elevation_label.setText(
                "Administrator: no — run as Administrator to read CPU, RAM, and SSD "
                "temperatures (GPU works without it)."
            )
        for row, (label, kind) in enumerate(_ROWS):
            value = reading.value(kind)
            text = f"{value:.0f} °C" if value is not None else "N/A"
            self._table.setItem(row, 0, QTableWidgetItem(label))
            self._table.setItem(row, 1, QTableWidgetItem(text))
        statuses = self._hub.statuses()
        summary = "; ".join(
            f"{s.backend_id}: {'up' if s.available else 'down'} ({s.detail})" for s in statuses
        )
        self._backends_label.setText(f"Backends — {summary}" if summary else "Backends — none")

    def closeEvent(self, a0) -> None:  # noqa: N802
        self._timer.stop()
        super().closeEvent(a0)


def open_sensor_diagnostics_dialog(hub, parent: QWidget | None = None) -> SensorDiagnosticsDialog:
    """Construct, show, and return the Sensor Diagnostics dialog."""
    dialog = SensorDiagnosticsDialog(hub, parent)
    dialog.exec()
    return dialog
