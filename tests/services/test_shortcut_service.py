from types import SimpleNamespace
from unittest.mock import patch

from services.native_hotkey_service import parse_hotkey
from services.shortcut_service import ShortcutService


class _Signal:
    def emit(self, *_args) -> None:
        pass


def _fake_monitor():
    return SimpleNamespace(
        countdown_timer=SimpleNamespace(
            last_preset_minutes=5,
            request_start=_Signal(),
            request_stop=_Signal(),
            request_adjust=_Signal(),
        ),
        request_release=_Signal(),
        request_aggressive=_Signal(),
        request_toggle_click_through=_Signal(),
        request_capture_regional=_Signal(),
        request_capture_active=_Signal(),
        request_capture_scrolling=_Signal(),
        request_capture_last_region=_Signal(),
    )


def test_global_shortcuts_suppress_trigger_keystrokes() -> None:
    calls: list[dict] = []

    def add_hotkey(_hotkey, _callback, **kwargs):
        calls.append(kwargs)
        return object()

    service = ShortcutService(prefer_native=False)
    with patch("services.shortcut_service.keyboard.add_hotkey", side_effect=add_hotkey), patch(
        "services.shortcut_service.keyboard.remove_hotkey"
    ) as remove_hotkey:
        assert service.register_shortcuts(_fake_monitor()) == []
        assert calls
        assert all(call["suppress"] is True for call in calls)
        assert all(call["trigger_on_release"] is False for call in calls)

        service.unregister_all()

    assert remove_hotkey.call_count == len(calls)


def test_native_hotkey_parser_supports_app_shortcuts() -> None:
    assert parse_hotkey("ctrl+shift+alt+c") is not None
    assert parse_hotkey("windows+shift+r") is not None
    assert parse_hotkey("ctrl+shift+alt+delete") is not None
    assert parse_hotkey("ctrl+shift+alt+=") is not None


def test_shortcut_registration_fallback_on_native_failure() -> None:
    calls: list[dict] = []

    def add_hotkey(_hotkey, _callback, **kwargs):
        calls.append(kwargs)
        return object()

    # Create a Service with prefer_native=True, so it tries native registration.
    service = ShortcutService(prefer_native=True)

    # Mock NativeHotkeyRegistrar.register to return False (native registration failure)
    with patch.object(
        service._native_hotkeys, "register", return_value=False  # pylint: disable=protected-access
    ) as mock_register, patch(
        "services.shortcut_service.keyboard.add_hotkey", side_effect=add_hotkey
    ) as mock_add_hotkey, patch(
        "services.shortcut_service.keyboard.remove_hotkey"
    ):
        # This should try native, fail, and then try keyboard.add_hotkey which succeeds.
        assert service.register_shortcuts(_fake_monitor()) == []
        assert mock_register.call_count > 0
        assert mock_add_hotkey.call_count == mock_register.call_count
        assert service.failed == []
        assert len(service.registered) > 0
        service.unregister_all()
