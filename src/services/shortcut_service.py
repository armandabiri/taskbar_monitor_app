"""Global keyboard shortcut service using the keyboard library."""

import logging
from typing import Any
import keyboard

LOGGER = logging.getLogger(__name__)


class ShortcutService:
    """Manages global hotkeys for the application."""

    def __init__(self) -> None:
        """Initialize shortcuts mapping."""

    def register_shortcuts(self, monitor: Any) -> None:
        """Register global shortcuts for timer and resource release.

        Parameters
        ----------
        monitor:
            The TaskbarMonitor instance.
        """
        try:
            # 1. Timer Presets (Ctrl+Shift+Alt + 0-5)
            timer_shortcuts = {
                "ctrl+shift+alt+1": 5,
                "ctrl+shift+alt+2": 10,
                "ctrl+shift+alt+3": 15,
                "ctrl+shift+alt+4": 30,
                "ctrl+shift+alt+5": 60,
                "ctrl+shift+alt+0": 0,
            }

            for hk, mins in timer_shortcuts.items():
                if mins > 0:
                    def make_timer_callback(m: int):
                        # pylint: disable=cell-var-from-loop
                        return lambda: monitor.countdown_timer.request_start.emit(m)
                    callback = make_timer_callback(mins)
                else:
                    callback = monitor.countdown_timer.request_stop.emit

                keyboard.add_hotkey(hk, callback, suppress=False)

            # 2. Timer Adjustment (Ctrl+Shift+Alt + Plus/Minus)
            # We use '=' and '-' which are standard for keyboard library
            def adjust_plus():
                val = monitor.countdown_timer.last_preset_minutes
                monitor.countdown_timer.request_adjust.emit(val)

            def adjust_minus():
                val = monitor.countdown_timer.last_preset_minutes
                monitor.countdown_timer.request_adjust.emit(-val)

            # Note: Many users use = and - for plus/minus shortcuts
            # Using raw characters is safer for the keyboard library parse
            keyboard.add_hotkey("ctrl+shift+alt+=", adjust_plus, suppress=False)
            keyboard.add_hotkey("ctrl+shift+alt+-", adjust_minus, suppress=False)

            # 3. Resource release shortcuts
            keyboard.add_hotkey("ctrl+shift+alt+delete", monitor.request_release.emit, suppress=False)
            keyboard.add_hotkey("ctrl+shift+alt+backspace", monitor.request_aggressive.emit, suppress=False)

            LOGGER.info("All global shortcuts registered successfully")

        except Exception:  # pylint: disable=broad-exception-caught
            LOGGER.exception("Failed to register global hotkeys")

    def unregister_all(self) -> None:
        """Clear all registered hotkeys."""
        try:
            keyboard.unhook_all()
        except Exception:  # pylint: disable=broad-exception-caught
            pass
