"""Tests for the typed SensorReading model."""

from __future__ import annotations

import pytest

from services.sensors.models import SensorKind, SensorReading


def test_value_returns_field_or_none():
    reading = SensorReading(cpu_temp_c=55.0, ssd_temp_c=None, backend_id="x")
    assert reading.value(SensorKind.CPU_TEMP) == 55.0
    assert reading.value(SensorKind.SSD_TEMP) is None


def test_value_rejects_bad_kind():
    with pytest.raises(ValueError):
        SensorReading().value("cpu")  # type: ignore[arg-type]


def test_merged_with_fills_gaps_and_keeps_priority():
    primary = SensorReading(cpu_temp_c=50.0, backend_id="lhm-clr")
    fallback = SensorReading(cpu_temp_c=99.0, gpu_temp_c=60.0, backend_id="nvml")
    merged = primary.merged_with(fallback)
    assert merged.cpu_temp_c == 50.0  # primary wins
    assert merged.gpu_temp_c == 60.0  # filled from fallback
    assert merged.backend_id == "lhm-clr"


def test_has_any_temp():
    assert not SensorReading().has_any_temp()
    assert SensorReading(ram_temp_c=40.0).has_any_temp()
