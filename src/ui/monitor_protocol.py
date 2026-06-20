"""Structural interface the context menu and tray expect from the main window."""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from PyQt6.QtCore import QSettings


@runtime_checkable
class MonitorProtocol(Protocol):
    """Protocol defining the interface for the taskbar monitor parent widget."""

    bg_opacity: int
    interval: int
    click_through: bool
    autohide_fullscreen: bool
    minimize_to_tray: bool
    settings: QSettings

    def update_opacity(self, value: int) -> None:
        """Set panel opacity."""

    def set_interval(self, milliseconds: int) -> None:
        """Set update interval."""

    def is_autostart_enabled(self) -> bool:
        """Check if autostart is enabled."""

    def toggle_autostart(self) -> None:
        """Toggle autostart status."""

    def set_click_through(self, enabled: bool) -> None:
        """Enable/disable click-through mode."""

    def set_autohide_fullscreen(self, enabled: bool) -> None:
        """Enable/disable auto-hide on fullscreen foreground apps."""

    def set_minimize_to_tray(self, enabled: bool) -> None:
        """Enable/disable minimize-to-tray behavior."""

    def show_processes_popup(self) -> None:
        """Open the top-processes popup."""

    def show_clipboard_popup(self) -> None:
        """Open the clipboard-history popup."""

    def show_snapshot_manager(self) -> None:
        """Open the process-snapshot manager dialog."""

    def show_cleanup_history(self) -> None:
        """Open the cleanup history dialog."""

    def show_cmdline_kill_dialog(self) -> None:
        """Open kill-by-WMI-command-line dialog."""

    def show_app_chord_manager(self) -> None:
        """Open the app chord shortcuts manager dialog."""

    def is_microphone_recording(self) -> bool:
        """Return whether microphone recording is active."""

    def toggle_microphone_recording(self) -> None:
        """Start or stop microphone recording."""

    def open_recordings_folder(self) -> None:
        """Open the configured recordings folder."""

    def show_recording_settings(self) -> None:
        """Open the microphone recording settings dialog."""

    def capture_regional(self) -> None:
        """Trigger regional screenshot."""

    def capture_element(self) -> None:
        """Trigger smart element screenshot."""

    def capture_last_region(self) -> None:
        """Trigger repeat regional screenshot."""

    def capture_active_window(self) -> None:
        """Trigger active window screenshot."""

    def capture_scrolling(self) -> None:
        """Trigger scrolling active window screenshot."""

    def capture_full_screen(self) -> None:
        """Trigger full-screen capture on the cursor's monitor."""

    def show_screenshot_settings(self) -> None:
        """Open screenshot output and scroll settings."""

    def reload_resource_profiles(self) -> None:
        """Reload smart/aggressive profile bindings from settings."""

    def force_reclaim(self) -> None:
        """Run a full cleanup pass, bypassing the pressure threshold."""

    def preview_cleanup(self) -> None:
        """Show a dry-run preview before running cleanup."""

    def flush_standby_cache(self) -> None:
        """Flush the Windows standby cache directly."""

    def reset_throttled(self) -> None:
        """Restore processes throttled by a previous cleanup."""

    def show_auto_clean_settings(self) -> None:
        """Open the auto-clean watchdog settings dialog."""

    def show_monitor_settings(self) -> None:
        """Open the unified monitor settings dialog."""

    def show_sensor_diagnostics(self) -> None:
        """Open the sensor diagnostics dialog."""

    def is_scope_visible(self, key: str) -> bool:
        """Return whether a scope is shown."""
        ...

    def set_scope_visible(self, key: str, visible: bool) -> None:
        """Toggle a scope's visibility."""

    def get_layout_mode(self) -> str:
        """Return the active layout density mode."""
        ...

    def set_layout_mode(self, mode: str) -> None:
        """Set the layout density mode."""

    def get_theme_mode(self) -> str:
        """Return the active theme mode (system/light/dark)."""
        ...

    def set_theme_mode(self, mode: str) -> None:
        """Set the active theme mode."""
