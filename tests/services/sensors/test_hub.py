"""Tests for SensorHub aggregation and merging."""

from __future__ import annotations

from services.sensors.backend import BackendStatus
from services.sensors.hub import SensorHub
from services.sensors.models import SensorReading


class _FakeBackend:
    def __init__(self, backend_id: str, reading: SensorReading) -> None:
        self.id = backend_id
        self._reading = reading

    def available(self) -> bool:
        return True

    def read(self) -> SensorReading:
        return self._reading

    def close(self) -> None:
        pass

    def status(self) -> BackendStatus:
        return BackendStatus(self.id, True, "ok")


def test_refresh_caches_backend_reading():
    hub = SensorHub()
    hub._backends = [_FakeBackend("fake", SensorReading(cpu_temp_c=42.0, backend_id="fake"))]
    reading = hub.refresh_once()
    assert reading.cpu_temp_c == 42.0
    assert hub.snapshot().cpu_temp_c == 42.0
    assert hub.active_backend_id() == "fake"
    assert hub.cpu_temp_c() == 42.0


def test_merge_fills_gaps_across_backends_in_priority_order():
    primary = _FakeBackend("clr", SensorReading(cpu_temp_c=50.0, backend_id="clr"))
    fallback = _FakeBackend("nvml", SensorReading(gpu_temp_c=61.0, backend_id="nvml"))
    hub = SensorHub()
    hub._backends = [primary, fallback]
    reading = hub.refresh_once()
    assert reading.cpu_temp_c == 50.0
    assert reading.gpu_temp_c == 61.0
    assert reading.backend_id == "clr"


def test_statuses_reflects_backends():
    hub = SensorHub()
    hub._backends = [_FakeBackend("fake", SensorReading())]
    statuses = hub.statuses()
    assert statuses[0].backend_id == "fake"
