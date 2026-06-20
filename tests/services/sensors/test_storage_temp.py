"""Tests for NVMe/SSD temperature selection from an LHM Computer."""

from __future__ import annotations

from types import SimpleNamespace

from services.sensors.storage_temp import read_ssd_temp


def _sensor(name: str, value: float | None, kind: str = "Temperature"):
    return SimpleNamespace(Name=name, Value=value, SensorType=kind)


def _storage(name: str, sensors: list):
    return SimpleNamespace(
        HardwareType="Storage", Name=name, Sensors=sensors, Update=lambda: None
    )


def _computer(hardware: list):
    return SimpleNamespace(Hardware=hardware)


def test_prefers_named_crucial_t700_drive():
    computer = _computer([
        _storage("Samsung USB Flash", [_sensor("Temperature", 30.0)]),
        _storage("CT4000T700SSD3", [_sensor("Drive Temperature", 48.0)]),
    ])
    assert read_ssd_temp(computer) == 48.0


def test_falls_back_to_first_storage_with_temp():
    computer = _computer([_storage("WD Blue SN570", [_sensor("Temperature", 41.0)])])
    assert read_ssd_temp(computer) == 41.0


def test_none_when_no_storage_or_no_computer():
    assert read_ssd_temp(None) is None
    assert read_ssd_temp(_computer([])) is None
