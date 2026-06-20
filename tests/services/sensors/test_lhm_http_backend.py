"""Tests for parsing the LibreHardwareMonitor HTTP JSON tree."""

from __future__ import annotations

from services.sensors.lhm_http_backend import LhmHttpBackend


def _leaf(text: str, value: str) -> dict:
    return {"Text": text, "Type": "Temperature", "Value": value, "Children": []}


def _group(text: str, children: list[dict]) -> dict:
    return {"Text": text, "Type": "", "Value": "", "Children": children}


FIXTURE_TREE = _group(
    "Sensor",
    [
        _group("Intel Core i9-13900K", [
            _group("Temperatures", [
                _leaf("CPU Package", "55.0 °C"),
                _leaf("CPU Core #1", "50.0 °C"),
            ]),
        ]),
        _group("Generic Memory", [_leaf("Memory", "40,0 °C")]),
        _group("NVIDIA GeForce RTX 4090", [_leaf("GPU Core", "60.0 °C")]),
        _group("CT4000T700SSD3", [_leaf("Drive Temperature", "45.0 °C")]),
    ],
)


def test_parse_extracts_all_four_temperatures():
    reading = LhmHttpBackend().parse(FIXTURE_TREE, taken_at=1.0)
    assert reading.cpu_temp_c == 55.0
    assert reading.ram_temp_c == 40.0  # comma decimal handled
    assert reading.gpu_temp_c == 60.0
    assert reading.ssd_temp_c == 45.0
    assert reading.backend_id == "lhm-http"


def test_parse_empty_tree_is_all_none():
    reading = LhmHttpBackend().parse(_group("Sensor", []), taken_at=2.0)
    assert not reading.has_any_temp()
