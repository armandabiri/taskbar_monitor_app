"""Global keyboard shortcut service using the keyboard library."""

import logging
from typing import Any, Callable

import keyboard

LOGGER = logging.getLogger(__name__)


class ShortcutService:
    """Manages global hotkeys for the application."""

    def __init__(self) -> None:
        """Initialize shortcuts state."""
        self.failed: list[str] = []
        self.registered: list[str] = []
        self._handles: list[Any] = []

    def _try_register(self, hotkey: str, callback: Callable[[], Any]) -> None:
        """Register one hotkey, track success/failure individually."""
        try:
            handle = keyboard.add_hotkey(
                hotkey,
                callback,
                suppress=True,
                trigger_on_release=False,
            )
            self._handles.append(handle)
            self.registered.append(hotkey)
        except (ValueError, OSError) as exc:
            self.failed.append(hotkey)
            LOGGER.warning("Could not register hotkey %r: %s", hotkey, exc)
        except Exception as exc:  # pylint: disable=broad-exception-caught
            self.failed.append(hotkey)
            LOGGER.warning("Unexpected error registering hotkey %r: %s", hotkey, exc)

    def register_shortcuts(self, monitor: Any) -> list[str]:
        """Register global shortcuts for timer and resource release.

        Returns the list of hotkeys that failed to register.
        """
        self.unregister_all()
        self.failed = []
        self.registered = []

        timer_shortcuts = {
            "ctrl+shift+alt+1": 5,
            "ctrl+shift+alt+2": 10,
            "ctrl+shift+alt+3": 15,
            "ctrl+shift+alt+4": 30,
            "ctrl+shift+alt+5": 45,
            "ctrl+shift+alt+6": 60,
            "ctrl+shift+alt+0": 0,
        }

        for hk, mins in timer_shortcuts.items():
            if mins > 0:
                def make_timer_callback(m: int) -> Callable[[], None]:
                    return lambda: monitor.countdown_timer.request_start.emit(m)
                callback = make_timer_callback(mins)
            else:
                def callback() -> None:
                    monitor.countdown_timer.request_stop.emit()
            self._try_register(hk, callback)

        def adjust_plus() -> None:
            val = monitor.countdown_timer.last_preset_minutes
            monitor.countdown_timer.request_adjust.emit(val)

        def adjust_minus() -> None:
            val = monitor.countdown_timer.last_preset_minutes
            monitor.countdown_timer.request_adjust.emit(-val)

        self._try_register("ctrl+shift+alt+=", adjust_plus)
        self._try_register("ctrl+shift+alt+-", adjust_minus)

        self._try_register("ctrl+shift+alt+delete", lambda: monitor.request_release.emit())
        self._try_register("ctrl+shift+alt+backspace", lambda: monitor.request_aggressive.emit())
        # Click-through toggle — critical escape hatch when click-through is on
        self._try_register(
            "ctrl+shift+alt+c",
            lambda: monitor.request_toggle_click_through.emit(),
        )
        # Screenshot hotkeys
        self._try_register("windows+shift+r", lambda: monitor.request_capture_regional.emit())
        self._try_register("windows+shift+w", lambda: monitor.request_capture_active.emit())
        self._try_register("windows+shift+s", lambda: monitor.request_capture_scrolling.emit())
        self._try_register(
            "windows+shift+alt+r",
            lambda: monitor.request_capture_last_region.emit(),
        )
        self._try_register(
            "ctrl+shift+alt+r",
            lambda: monitor.request_capture_last_region.emit(),
        )

        LOGGER.info(
            "Shortcut registration: %d succeeded, %d failed",
            len(self.registered), len(self.failed),
        )
        return list(self.failed)

    def unregister_all(self) -> None:
        """Clear all registered hotkeys."""
        for handle in list(self._handles):
            try:
                keyboard.remove_hotkey(handle)
            except (KeyError, ValueError):
                pass
            except Exception as exc:  # pylint: disable=broad-exception-caught
                LOGGER.debug("remove_hotkey failed (non-fatal): %s", exc)
        self._handles.clear()
