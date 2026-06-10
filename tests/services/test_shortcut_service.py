from types import SimpleNamespace
from unittest.mock import patch

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

    service = ShortcutService()
    with patch("services.shortcut_service.keyboard.add_hotkey", side_effect=add_hotkey), patch(
        "services.shortcut_service.keyboard.remove_hotkey"
    ) as remove_hotkey:
        assert service.register_shortcuts(_fake_monitor()) == []
        assert calls
        assert all(call["suppress"] is True for call in calls)
        assert all(call["trigger_on_release"] is False for call in calls)

        service.unregister_all()

    assert remove_hotkey.call_count == len(calls)
