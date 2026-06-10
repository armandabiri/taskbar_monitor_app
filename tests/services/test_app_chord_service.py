"""Tests for the per-app focus chord service."""

from __future__ import annotations

import sys
from unittest.mock import patch

import pytest
from PyQt6.QtCore import QSettings

from services.app_chord_service import (
    AppChordEntry,
    AppChordService,
    ShortcutMapping,
    _normalize_chord,
    load_chord_entries,
    save_chord_entries,
)


def _make_settings(tmp_path) -> QSettings:
    return QSettings(str(tmp_path / "app_chord_test.ini"), QSettings.Format.IniFormat)


class TestNormalizeChord:
    def test_orders_modifiers_consistently(self) -> None:
        assert _normalize_chord("Ctrl+Shift+M") == "ctrl+shift+m"
        assert _normalize_chord("Shift+Ctrl+M") == "ctrl+shift+m"
        assert _normalize_chord("M+Shift+Ctrl") == "ctrl+shift+m"

    def test_lowercases_and_strips_whitespace(self) -> None:
        assert _normalize_chord("  WIN + CTRL + SHIFT + T  ") == "win+ctrl+shift+t"

    def test_aliases_resolve_to_canonical(self) -> None:
        assert _normalize_chord("Windows+T") == "win+t"
        assert _normalize_chord("Cmd+Control+M") == "win+ctrl+m"
        assert _normalize_chord("Option+Shift+A") == "alt+shift+a"

    def test_empty_input_returns_empty(self) -> None:
        assert _normalize_chord("") == ""
        assert _normalize_chord("   ") == ""


class TestAppChordEntry:
    def test_round_trip_through_dict(self) -> None:
        entry = AppChordEntry(
            name="Teams",
            process_name="ms-teams.exe",
            exe_path="C:/Teams.exe",
            prefix_chord="ctrl+alt+shift+t",
            window_title_contains="Teams",
        )
        restored = AppChordEntry.from_dict(entry.to_dict())
        assert restored == entry

    def test_from_dict_normalizes_prefix(self) -> None:
        restored = AppChordEntry.from_dict({
            "name": "Teams",
            "process_name": "ms-teams.exe",
            "exe_path": "",
            "prefix_chord": "Shift+Ctrl+ALT+T",
        })
        assert restored.prefix_chord == "ctrl+alt+shift+t"

    def test_from_dict_ignores_legacy_action_chord(self) -> None:
        # Older settings used to carry an action_chord; it should be ignored cleanly.
        restored = AppChordEntry.from_dict({
            "name": "Teams",
            "process_name": "ms-teams.exe",
            "exe_path": "",
            "prefix_chord": "ctrl+alt+shift+t",
            "action_chord": "ctrl+shift+m",  # legacy, should not raise
        })
        assert restored.prefix_chord == "ctrl+alt+shift+t"
        assert not hasattr(restored, "action_chord")

    def test_is_valid_requires_target_and_some_trigger(self) -> None:
        assert AppChordEntry("n", "x.exe", "", "ctrl+alt+shift+t").is_valid()
        # Missing target
        assert not AppChordEntry("n", "", "", "ctrl+alt+shift+t").is_valid()
        # Missing prefix AND no mappings
        assert not AppChordEntry("n", "x.exe", "", "").is_valid()
        # Missing name
        assert not AppChordEntry("", "x.exe", "", "ctrl+alt+shift+t").is_valid()
        # Mappings-only is valid (prefix may be empty if at least one mapping exists)
        only_mappings = AppChordEntry(
            "n", "x.exe", "", "",
            mappings=[ShortcutMapping("win+alt+m", "ctrl+shift+m")],
        )
        assert only_mappings.is_valid()


class TestShortcutMapping:
    def test_round_trip_through_dict(self) -> None:
        mapping = ShortcutMapping("win+alt+m", "ctrl+shift+m", "Mute")
        assert ShortcutMapping.from_dict(mapping.to_dict()) == mapping

    def test_from_dict_normalizes_chords(self) -> None:
        mapping = ShortcutMapping.from_dict({
            "trigger": "Alt+Win+M", "action": "Shift+Ctrl+M",
        })
        assert mapping.trigger == "win+alt+m"
        assert mapping.action == "ctrl+shift+m"

    def test_is_valid_rejects_equal_or_empty(self) -> None:
        assert ShortcutMapping("win+alt+m", "ctrl+shift+m").is_valid()
        assert not ShortcutMapping("ctrl+m", "ctrl+m").is_valid()
        assert not ShortcutMapping("", "ctrl+m").is_valid()
        assert not ShortcutMapping("ctrl+m", "").is_valid()


class TestEntryWithMappings:
    def test_round_trip_preserves_mappings(self) -> None:
        entry = AppChordEntry(
            name="Teams",
            process_name="ms-teams.exe",
            exe_path="",
            prefix_chord="ctrl+alt+shift+t",
            mappings=[
                ShortcutMapping("win+alt+m", "ctrl+shift+m", "Mute"),
                ShortcutMapping("win+alt+v", "ctrl+shift+o", "Video"),
            ],
        )
        restored = AppChordEntry.from_dict(entry.to_dict())
        assert restored == entry

    def test_invalid_mappings_filtered_by_valid_mappings(self) -> None:
        entry = AppChordEntry("n", "x.exe", "", "ctrl+alt+shift+t", mappings=[
            ShortcutMapping("", ""),                       # both empty
            ShortcutMapping("ctrl+m", "ctrl+m"),           # trigger == action
            ShortcutMapping("win+alt+m", "ctrl+shift+m"),  # valid
        ])
        assert len(entry.valid_mappings()) == 1


class TestPersistence:
    def test_empty_settings_returns_empty_list(self, tmp_path) -> None:
        assert load_chord_entries(_make_settings(tmp_path)) == []

    def test_save_then_load_round_trip(self, tmp_path) -> None:
        settings = _make_settings(tmp_path)
        entries = [
            AppChordEntry("Teams", "ms-teams.exe", "", "ctrl+alt+shift+t"),
            AppChordEntry("Code", "Code.exe", "", "ctrl+alt+shift+v"),
        ]
        save_chord_entries(settings, entries)
        reloaded = load_chord_entries(_make_settings(tmp_path))
        assert reloaded == entries

    def test_malformed_payload_returns_empty(self, tmp_path) -> None:
        settings = _make_settings(tmp_path)
        settings.setValue("app_chord_entries", "not valid json {")
        settings.sync()
        assert load_chord_entries(_make_settings(tmp_path)) == []


class TestAppChordServiceReload:
    def test_invalid_entries_are_filtered_out(self) -> None:
        service = AppChordService(prefer_native=False)
        add_path = "services.app_chord_service.keyboard.add_hotkey"
        rm_path = "services.app_chord_service.keyboard.remove_hotkey"
        with patch(add_path, return_value="handle"), patch(rm_path):
            failed = service.reload([
                AppChordEntry("ok", "x.exe", "", "ctrl+alt+shift+t"),
                AppChordEntry("bad-no-prefix", "x.exe", "", ""),
            ])
        assert failed == []
        assert [e.name for e in service.entries] == ["ok"]

    def test_duplicate_prefixes_register_once(self) -> None:
        service = AppChordService(prefer_native=False)
        add_path = "services.app_chord_service.keyboard.add_hotkey"
        rm_path = "services.app_chord_service.keyboard.remove_hotkey"
        with patch(add_path, return_value="handle") as add, patch(rm_path):
            service.reload([
                AppChordEntry("a", "x.exe", "", "ctrl+alt+shift+t"),
                AppChordEntry("b", "y.exe", "", "ctrl+alt+shift+t"),
            ])
        # Both share the same prefix => add_hotkey called only once
        assert add.call_count == 1

    def test_mapping_triggers_are_registered(self) -> None:
        service = AppChordService(prefer_native=False)
        add_path = "services.app_chord_service.keyboard.add_hotkey"
        rm_path = "services.app_chord_service.keyboard.remove_hotkey"
        with patch(add_path, return_value="handle") as add, patch(rm_path):
            service.reload([
                AppChordEntry(
                    "Teams", "ms-teams.exe", "", "ctrl+alt+shift+t",
                    mappings=[
                        ShortcutMapping("win+alt+m", "ctrl+shift+m"),
                        ShortcutMapping("win+alt+v", "ctrl+shift+o"),
                    ],
                ),
            ])
        # 1 prefix + 2 mappings => 3 hotkeys registered
        assert add.call_count == 3

    def test_entry_with_only_mappings_registers(self) -> None:
        service = AppChordService(prefer_native=False)
        add_path = "services.app_chord_service.keyboard.add_hotkey"
        rm_path = "services.app_chord_service.keyboard.remove_hotkey"
        with patch(add_path, return_value="handle") as add, patch(rm_path):
            service.reload([
                AppChordEntry(
                    "Teams", "ms-teams.exe", "", "",
                    mappings=[ShortcutMapping("win+alt+m", "ctrl+shift+m")],
                ),
            ])
        assert add.call_count == 1

    def test_failed_registration_is_reported(self) -> None:
        service = AppChordService(prefer_native=False)
        with patch(
            "services.app_chord_service.keyboard.add_hotkey",
            side_effect=ValueError("nope"),
        ), patch("services.app_chord_service.keyboard.remove_hotkey"):
            failed = service.reload([
                AppChordEntry("a", "x.exe", "", "ctrl+alt+shift+t"),
            ])
        assert failed == ["ctrl+alt+shift+t"]

    def test_app_chord_fallback_on_native_failure(self) -> None:
        service = AppChordService(prefer_native=True)
        calls: list[str] = []

        def add_hotkey(chord, _callback, **_kwargs):
            calls.append(chord)
            return object()

        with patch.object(
            service._native_hotkeys, "register", return_value=False  # pylint: disable=protected-access
        ) as mock_register, patch(
            "services.app_chord_service.keyboard.add_hotkey", side_effect=add_hotkey
        ) as mock_add_hotkey, patch(
            "services.app_chord_service.keyboard.remove_hotkey"
        ):
            failed = service.reload([
                AppChordEntry("a", "x.exe", "", "ctrl+alt+shift+t"),
            ])
            assert failed == []
            assert mock_register.call_count == 1
            assert mock_add_hotkey.call_count == 1
            assert calls == ["ctrl+alt+shift+t"]
            service.unregister_all()


@pytest.mark.skipif(sys.platform != "win32", reason="Win32-only window helpers")
class TestWin32Helpers:
    def test_enumerate_visible_windows_returns_list(self) -> None:
        from services.app_chord_service import _enumerate_visible_windows
        result = _enumerate_visible_windows()
        assert isinstance(result, list)
        for item in result:
            assert isinstance(item, tuple) and len(item) == 3
            hwnd, pid, title = item
            assert isinstance(hwnd, int)
            assert isinstance(pid, int)
            assert isinstance(title, str)

    def test_enumerate_pickable_windows_excludes_self(self) -> None:
        import os

        from services.app_chord_service import enumerate_pickable_windows
        windows = enumerate_pickable_windows()
        assert isinstance(windows, list)
        self_pid = os.getpid()
        for win in windows:
            assert win.pid != self_pid
            assert isinstance(win.title, str) and win.title
