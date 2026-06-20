"""Tests for the Sensor Diagnostics dialog."""

from __future__ import annotations

from services.sensors.backend import BackendStatus
from services.sensors.models import SensorReading
from ui.sensor_diagnostics_dialog import SensorDiagnosticsDialog


class _FakeHub:
    def snapshot(self) -> SensorReading:
        return SensorReading(cpu_temp_c=55.0, ssd_temp_c=45.0, backend_id="lhm-clr")

    def active_backend_id(self) -> str:
        return "lhm-clr"

    def statuses(self):
        return [BackendStatus("lhm-clr", True, "ok")]


def test_dialog_lists_backend_and_readings(qtbot):
    dialog = SensorDiagnosticsDialog(_FakeHub())
    qtbot.addWidget(dialog)
    assert "lhm-clr" in dialog._backend_label.text()
    # CPU row value column reads 55 °C; SSD reads 45 °C.
    assert dialog._table.item(0, 1).text().startswith("55")
    assert dialog._table.item(3, 1).text().startswith("45")
    assert "lhm-clr" in dialog._backends_label.text()
