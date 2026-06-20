"""Owns the live-metric widgets (CPU core grid + oscilloscope scopes).

Extracted from ``main.py`` so new temperature scopes can be added under the
code-size cap. The manager builds the scopes into the monitor layout and, on each
UI tick, translates raw psutil scalars plus a ``SensorReading`` snapshot into
scope updates, thermal alerts, and optional telemetry logging. All temperatures
are displayed in Celsius.
"""

from __future__ import annotations

import os
from collections.abc import Callable

from core.config import (
    COLOR_GPU,
    COLOR_GPU_TEMP,
    COLOR_SSD_TEMP,
    COLOR_TEMP,
    COLOR_VRAM,
    KB,
    MB,
    app_data_dir,
    read_setting_int,
)
from services.sensors.models import SensorKind, SensorReading
from services.sensors.telemetry_log import TelemetryLog
from services.sensors.thermal_alerts import DEFAULT_THRESHOLDS_C, ThermalAlerts
from ui.widgets import CPUBarWidget, ScopeWidget

# Scope keys in display order; the graphs menu mirrors this set.
SCOPE_ORDER = ("cpu", "ram", "up", "dn", "r/w", "gpu", "vram", "temp", "gputemp", "ssdtemp")

_THRESHOLD_KEYS = {
    SensorKind.CPU_TEMP: "sensors/threshold_cpu_c",
    SensorKind.RAM_TEMP: "sensors/threshold_ram_c",
    SensorKind.GPU_TEMP: "sensors/threshold_gpu_c",
    SensorKind.SSD_TEMP: "sensors/threshold_ssd_c",
}


def format_speed(bytes_per_second: float) -> str:
    """Format network/disk throughput in K or M units."""
    if bytes_per_second >= MB:
        return f"{bytes_per_second / MB:.1f}M"
    return f"{bytes_per_second / KB:.0f}K"


def _celsius(value: float | None) -> str:
    return f"{int(round(value))}°C" if value is not None else "N/A"


class ScopeManager:
    """Build and update the monitor's metric scopes."""

    def __init__(self, main_layout, settings, gpu_available: bool, temp_available: bool,
                 notify: Callable[[str, str], None]) -> None:
        self._layout = main_layout
        self._settings = settings
        self._gpu_available = gpu_available
        self._temp_available = temp_available
        self.scopes: dict[str, ScopeWidget] = {}
        self.cpu_grid: CPUBarWidget | None = None
        self._alerts = ThermalAlerts(notify)
        self._telemetry: TelemetryLog | None = None
        self.reload()

    # -- construction ---------------------------------------------------
    def build(self) -> CPUBarWidget:
        """Create the CPU grid and scopes and add them to the layout."""
        self.cpu_grid = CPUBarWidget()
        self._layout.addWidget(self.cpu_grid)
        self.scopes = {
            "cpu": ScopeWidget("CPU", "#4db8ff"),
            "ram": ScopeWidget("RAM", "#a29bfe"),
            "up": ScopeWidget("UP", "#ff7675"),
            "dn": ScopeWidget("DN", "#55efc4"),
            "r/w": ScopeWidget("R/W", "#fdcb6e"),
        }
        if self._gpu_available:
            self.scopes["gpu"] = ScopeWidget("GPU", COLOR_GPU)
            self.scopes["vram"] = ScopeWidget("VRAM", COLOR_VRAM)
        if self._temp_available:
            self.scopes["temp"] = ScopeWidget("TEMP", COLOR_TEMP)
            self.scopes["gputemp"] = ScopeWidget("GPU°", COLOR_GPU_TEMP)
            self.scopes["ssdtemp"] = ScopeWidget("SSD°", COLOR_SSD_TEMP)
        for scope in self.scopes.values():
            self._layout.addWidget(scope, 1)
        self.apply_visibility()
        return self.cpu_grid

    # -- per-tick update ------------------------------------------------
    def update(self, per_cpu, cpu: float, ram: float, net_up: float, net_dn: float,
               disk_rw: float, gpu_stats, reading: SensorReading) -> None:
        """Refresh every scope from the latest scalars and sensor snapshot."""
        if self.cpu_grid is not None:
            self.cpu_grid.update_usage(per_cpu)
        self.scopes["cpu"].update_value(cpu, f"{int(cpu)}%")
        self.scopes["ram"].update_value(ram, f"{int(ram)}%")
        self.scopes["up"].update_value(net_up, format_speed(net_up), auto_scale=True)
        self.scopes["dn"].update_value(net_dn, format_speed(net_dn), auto_scale=True)
        self.scopes["r/w"].update_value(disk_rw, format_speed(disk_rw), auto_scale=True)
        if "gpu" in self.scopes and gpu_stats.util_percent is not None:
            util = gpu_stats.util_percent
            self.scopes["gpu"].update_value(util, f"{int(util)}%")
        if "vram" in self.scopes and gpu_stats.vram_percent is not None:
            vram = gpu_stats.vram_percent
            self.scopes["vram"].update_value(vram, f"{int(vram)}%")
        # Fill GPU temperature from NVML when the sensor backend did not report it.
        eff = reading.merged_with(SensorReading(gpu_temp_c=gpu_stats.temp_c))
        self._update_temps(eff)
        self._apply_alerts(eff)
        self._append_telemetry(eff)

    def _update_temps(self, reading: SensorReading) -> None:
        if "temp" in self.scopes:
            cpu_t, ram_t = reading.cpu_temp_c, reading.ram_temp_c
            text = self._temp_text(cpu_t, ram_t)
            self.scopes["temp"].update_value(
                cpu_t if cpu_t is not None else 0.0, text,
                auto_scale=True, secondary_value=ram_t,
            )
        if "gputemp" in self.scopes:
            g = reading.gpu_temp_c
            self.scopes["gputemp"].update_value(g if g is not None else 0.0, _celsius(g),
                                                auto_scale=True)
        if "ssdtemp" in self.scopes:
            s = reading.ssd_temp_c
            self.scopes["ssdtemp"].update_value(s if s is not None else 0.0, _celsius(s),
                                                auto_scale=True)

    @staticmethod
    def _temp_text(cpu_t: float | None, ram_t: float | None) -> str:
        parts = []
        if cpu_t is not None:
            parts.append(f"CPU {int(round(cpu_t))}°C")
        if ram_t is not None:
            parts.append(f"RAM {int(round(ram_t))}°C")
        return "  ".join(parts) if parts else "N/A"

    # -- alerts & telemetry --------------------------------------------
    def _apply_alerts(self, reading: SensorReading) -> None:
        thresholds = self._thresholds()
        self._alerts.evaluate(reading, thresholds, enabled=self._alerts_enabled())
        breached = self._alerts.breached(reading, thresholds)
        if "temp" in self.scopes:
            self.scopes["temp"].alert = (
                SensorKind.CPU_TEMP in breached or SensorKind.RAM_TEMP in breached
            )
        if "gputemp" in self.scopes:
            self.scopes["gputemp"].alert = SensorKind.GPU_TEMP in breached
        if "ssdtemp" in self.scopes:
            self.scopes["ssdtemp"].alert = SensorKind.SSD_TEMP in breached

    def _append_telemetry(self, reading: SensorReading) -> None:
        if self._telemetry is not None:
            self._telemetry.append(reading)

    def _thresholds(self) -> dict[SensorKind, int]:
        return {
            kind: read_setting_int(self._settings, key, DEFAULT_THRESHOLDS_C[kind])
            for kind, key in _THRESHOLD_KEYS.items()
        }

    def _alerts_enabled(self) -> bool:
        return bool(read_setting_int(self._settings, "sensors/alerts_enabled", 1))

    # -- settings & layout ---------------------------------------------
    def reload(self) -> None:
        """Re-read telemetry settings and rebuild the telemetry sink."""
        if read_setting_int(self._settings, "telemetry/enabled", 0):
            fmt = str(self._settings.value("telemetry/format", "csv"))
            retention = read_setting_int(self._settings, "telemetry/retention_rows", 50000)
            ext = "jsonl" if fmt == "jsonl" else "csv"
            path = os.path.join(app_data_dir(), f"sensor_telemetry.{ext}")
            self._telemetry = TelemetryLog(path, fmt, retention)
        else:
            self._telemetry = None

    def is_scope_visible(self, key: str) -> bool:
        return bool(read_setting_int(self._settings, f"scope_visible_{key}", 1))

    def set_scope_visible(self, key: str, visible: bool) -> None:
        self._settings.setValue(f"scope_visible_{key}", 1 if visible else 0)
        self._settings.sync()
        self.apply_visibility()

    def apply_visibility(self) -> None:
        for key, scope in self.scopes.items():
            scope.setVisible(self.is_scope_visible(key))

    def apply_layout(self, scope_min_w: int) -> None:
        for scope in self.scopes.values():
            scope.setMinimumWidth(scope_min_w)

    def on_theme_changed(self) -> None:
        if self.cpu_grid is not None:
            self.cpu_grid.update()
        for scope in self.scopes.values():
            scope.grid_pixmap = None
            scope.update()
