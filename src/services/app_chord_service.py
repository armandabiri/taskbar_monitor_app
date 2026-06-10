"""Per-app focus shortcut service.

The user registers a chord prefix (e.g., ``ctrl+alt+shift+t``) and a target
app. Pressing the prefix from any window:

1. Remembers the previously focused window.
2. Focuses (or launches) the target app.
3. Lets the user press the app's own shortcut (it goes to the focused app).
4. After the action completes (all modifiers released) or a short timeout,
   focus is restored to the previously focused window.

No action key is pre-configured. The "second part of the chord" is whatever
keystroke the app itself defines for the operation the user wants.
"""

from __future__ import annotations

import ctypes
import ctypes.wintypes as wintypes
import json
import logging
import os
import shlex
import subprocess
import sys
import threading
import time
from dataclasses import asdict, dataclass, field
from typing import Any, Callable

import keyboard
import psutil
from PyQt6.QtCore import QSettings

from services.native_hotkey_service import NativeHotkeyRegistrar

LOGGER = logging.getLogger(__name__)

SETTINGS_KEY = "app_chord_entries"

# Maximum time the service waits for the user to complete the action chord
# after focusing the target window. Focus is restored after this regardless.
ACTION_TIMEOUT_SECONDS = 3.5

# Small delay after focusing the target so its window has time to receive
# the activation before the first user keystroke is processed.
POST_FOCUS_SETTLE_SECONDS = 0.08

# Small delay before restoring focus, to let the target app finish processing
# the action keystroke.
PRE_RESTORE_DELAY_SECONDS = 0.12


@dataclass
class ShortcutMapping:
    """One trigger -> action remap inside an :class:`AppChordEntry`.

    When ``trigger`` fires globally, the parent app is focused and ``action``
    is sent to it. ``label`` is an optional human-readable description.
    """

    trigger: str  # the global shortcut the user invents, e.g., "win+alt+m"
    action: str  # the app's own shortcut to send, e.g., "ctrl+shift+m"
    label: str = ""

    def to_dict(self) -> dict:
        return asdict(self)

    @staticmethod
    def from_dict(data: dict) -> "ShortcutMapping":
        return ShortcutMapping(
            trigger=_normalize_chord(str(data.get("trigger", ""))),
            action=_normalize_chord(str(data.get("action", ""))),
            label=str(data.get("label", "")).strip(),
        )

    def is_valid(self) -> bool:
        return bool(self.trigger and self.action and self.trigger != self.action)


@dataclass
class AppChordEntry:
    """Focus-the-app binding plus an optional list of trigger->action remaps."""

    name: str
    process_name: str  # e.g., "ms-teams.exe"
    exe_path: str  # optional, used to launch the app if not running
    prefix_chord: str  # e.g., "ctrl+alt+shift+t" — focus only; may be empty
    window_title_contains: str = ""  # optional secondary match (case-insensitive)
    mappings: list[ShortcutMapping] = field(default_factory=list)

    def to_dict(self) -> dict:
        return asdict(self)

    @staticmethod
    def from_dict(data: dict) -> "AppChordEntry":
        raw_mappings = data.get("mappings") or []
        mappings: list[ShortcutMapping] = []
        if isinstance(raw_mappings, list):
            for item in raw_mappings:
                if isinstance(item, dict):
                    try:
                        mappings.append(ShortcutMapping.from_dict(item))
                    except (TypeError, ValueError) as exc:
                        LOGGER.warning("Skipping malformed mapping %r: %s", item, exc)
        return AppChordEntry(
            name=str(data.get("name", "")).strip(),
            process_name=str(data.get("process_name", "")).strip(),
            exe_path=str(data.get("exe_path", "")).strip(),
            prefix_chord=_normalize_chord(str(data.get("prefix_chord", ""))),
            window_title_contains=str(data.get("window_title_contains", "")).strip(),
            mappings=mappings,
        )

    def valid_mappings(self) -> list[ShortcutMapping]:
        """Return the subset of mappings that pass :meth:`ShortcutMapping.is_valid`."""
        return [m for m in self.mappings if m.is_valid()]

    def is_valid(self) -> bool:
        """Return True when the entry has the fields required to register.

        Requires a name + target (process or exe) AND at least one trigger —
        either the focus-prefix or one valid remapping.
        """
        has_trigger = bool(self.prefix_chord) or bool(self.valid_mappings())
        return bool(
            self.name
            and (self.process_name or self.exe_path)
            and has_trigger
        )


# ----------------------------------------------------------------------------
# Persistence
# ----------------------------------------------------------------------------
def load_chord_entries(settings: QSettings) -> list[AppChordEntry]:
    """Load chord entries from QSettings (stored as JSON array)."""
    raw = settings.value(SETTINGS_KEY, "")
    if not isinstance(raw, str) or not raw.strip():
        return []
    try:
        data = json.loads(raw)
    except (ValueError, TypeError):
        LOGGER.warning("Could not decode app_chord_entries; resetting to empty")
        return []
    if not isinstance(data, list):
        return []
    out: list[AppChordEntry] = []
    for item in data:
        if isinstance(item, dict):
            try:
                out.append(AppChordEntry.from_dict(item))
            except (TypeError, ValueError) as exc:
                LOGGER.warning("Skipping malformed chord entry %r: %s", item, exc)
    return out


def save_chord_entries(settings: QSettings, entries: list[AppChordEntry]) -> None:
    """Persist chord entries as a JSON string in QSettings."""
    payload = json.dumps([e.to_dict() for e in entries], ensure_ascii=False)
    settings.setValue(SETTINGS_KEY, payload)
    settings.sync()


# ----------------------------------------------------------------------------
# Chord string normalization
# ----------------------------------------------------------------------------
_MODIFIER_ORDER = ("win", "ctrl", "alt", "shift")
_MODIFIER_NAMES = set(_MODIFIER_ORDER) | {
    "left ctrl", "right ctrl",
    "left shift", "right shift",
    "left alt", "right alt",
    "left windows", "right windows",
}
_MODIFIER_ALIASES = {
    "windows": "win",
    "meta": "win",
    "super": "win",
    "command": "win",
    "cmd": "win",
    "control": "ctrl",
    "option": "alt",
}


def _normalize_chord(chord: str) -> str:
    """Normalize a chord string to a canonical lowercase form: modifiers first."""
    if not chord:
        return ""
    parts = [p.strip().lower() for p in chord.replace(" ", "").split("+") if p.strip()]
    parts = [_MODIFIER_ALIASES.get(p, p) for p in parts]
    mods = [m for m in _MODIFIER_ORDER if m in parts]
    rest = [p for p in parts if p not in _MODIFIER_ORDER]
    return "+".join(mods + rest)


# ----------------------------------------------------------------------------
# Win32 helpers
# ----------------------------------------------------------------------------
if sys.platform == "win32":
    _user32 = ctypes.windll.user32
    _kernel32 = ctypes.windll.kernel32

    _SW_RESTORE = 9

    _EnumWindowsProc = ctypes.WINFUNCTYPE(
        wintypes.BOOL, wintypes.HWND, wintypes.LPARAM
    )
else:  # pragma: no cover — non-Windows fallbacks
    _user32 = None  # type: ignore[assignment]
    _kernel32 = None  # type: ignore[assignment]


@dataclass
class WindowInfo:
    """Snapshot of one top-level window for picker UIs."""

    hwnd: int
    pid: int
    title: str
    process_name: str
    exe_path: str


def _enumerate_visible_windows() -> list[tuple[int, int, str]]:
    """Return (hwnd, pid, title) for every top-level visible window."""
    if sys.platform != "win32" or _user32 is None:
        return []
    results: list[tuple[int, int, str]] = []

    def callback(hwnd: int, _lparam: int) -> bool:
        if not _user32.IsWindowVisible(hwnd):
            return True
        length = _user32.GetWindowTextLengthW(hwnd)
        buf = ctypes.create_unicode_buffer(length + 1)
        _user32.GetWindowTextW(hwnd, buf, length + 1)
        title = buf.value
        if not title:
            return True
        pid = wintypes.DWORD(0)
        _user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        results.append((hwnd, int(pid.value), title))
        return True

    _user32.EnumWindows(_EnumWindowsProc(callback), 0)
    return results


def enumerate_pickable_windows(self_pid: int | None = None) -> list[WindowInfo]:
    """Return user-meaningful top-level windows enriched with process info.

    Excludes the calling process itself so users don't accidentally bind to
    Taskbar Monitor's own windows.
    """
    if self_pid is None:
        self_pid = os.getpid()
    pid_to_info: dict[int, tuple[str, str]] = {}
    for proc in psutil.process_iter(["pid", "name", "exe"]):
        try:
            pid_to_info[proc.info["pid"]] = (
                str(proc.info["name"] or ""),
                str(proc.info["exe"] or ""),
            )
        except (psutil.Error, TypeError, ValueError):
            continue
    out: list[WindowInfo] = []
    seen_pids: set[int] = set()
    for hwnd, pid, title in _enumerate_visible_windows():
        if pid == self_pid:
            continue
        proc_name, exe_path = pid_to_info.get(pid, ("", ""))
        # Skip system shell windows that have no process exe we can launch
        if proc_name.lower() in {"explorer.exe", "shellexperiencehost.exe"} \
                and not title.strip():
            continue
        out.append(WindowInfo(
            hwnd=hwnd, pid=pid, title=title,
            process_name=proc_name, exe_path=exe_path,
        ))
        seen_pids.add(pid)
    # Sort: process name first, then title — stable display order
    out.sort(key=lambda w: (w.process_name.lower(), w.title.lower()))
    return out


def _find_window_for_entry(entry: AppChordEntry) -> int | None:
    """Find an HWND whose process name matches the entry. Returns None if absent."""
    if sys.platform != "win32":
        return None
    target_proc = entry.process_name.strip().lower()
    title_filter = entry.window_title_contains.strip().lower()
    pid_to_name: dict[int, str] = {}
    for proc in psutil.process_iter(["pid", "name"]):
        try:
            pid_to_name[proc.info["pid"]] = str(proc.info["name"] or "").lower()
        except (psutil.Error, TypeError, ValueError):
            continue
    for hwnd, pid, title in _enumerate_visible_windows():
        proc_name = pid_to_name.get(pid, "")
        if target_proc and target_proc not in proc_name:
            continue
        if title_filter and title_filter not in title.lower():
            continue
        return hwnd
    return None


def _get_foreground_window() -> int:
    """Return the current foreground HWND, or 0."""
    if sys.platform != "win32" or _user32 is None:
        return 0
    try:
        return int(_user32.GetForegroundWindow())
    except OSError:
        return 0


def _force_foreground(hwnd: int) -> bool:
    """Bring an HWND to the foreground, attaching input threads to defeat focus
    restrictions. Returns True on success."""
    if sys.platform != "win32" or _user32 is None or _kernel32 is None or not hwnd:
        return False
    try:
        if _user32.IsIconic(hwnd):
            _user32.ShowWindow(hwnd, _SW_RESTORE)
        foreground_hwnd = _user32.GetForegroundWindow()
        target_thread = _user32.GetWindowThreadProcessId(hwnd, None)
        fg_thread = (
            _user32.GetWindowThreadProcessId(foreground_hwnd, None)
            if foreground_hwnd
            else 0
        )
        current_thread = _kernel32.GetCurrentThreadId()
        attached_a = False
        attached_b = False
        if fg_thread and fg_thread != current_thread:
            attached_a = bool(_user32.AttachThreadInput(current_thread, fg_thread, True))
        if target_thread and target_thread != current_thread:
            attached_b = bool(_user32.AttachThreadInput(current_thread, target_thread, True))
        try:
            _user32.BringWindowToTop(hwnd)
            ok = bool(_user32.SetForegroundWindow(hwnd))
        finally:
            if attached_a:
                _user32.AttachThreadInput(current_thread, fg_thread, False)
            if attached_b:
                _user32.AttachThreadInput(current_thread, target_thread, False)
        return ok
    except OSError as exc:
        LOGGER.warning("SetForegroundWindow failed for hwnd=%s: %s", hwnd, exc)
        return False


def _launch_app(entry: AppChordEntry) -> None:
    """Launch the app via its configured exe_path. No-op if unset."""
    cmd = entry.exe_path.strip()
    if not cmd:
        return
    try:
        if os.path.isfile(cmd):
            args: list[str] = [cmd]
        else:
            args = shlex.split(cmd, posix=False)
        creationflags = getattr(subprocess, "CREATE_NO_WINDOW", 0) | getattr(
            subprocess, "DETACHED_PROCESS", 0
        )
        subprocess.Popen(  # pylint: disable=consider-using-with
            args,
            close_fds=True,
            creationflags=creationflags,
        )
    except (OSError, ValueError) as exc:
        LOGGER.warning("Could not launch %r: %s", entry.exe_path, exc)


def _focus_or_launch(entry: AppChordEntry, *, launch_timeout: float = 4.0) -> bool:
    """Bring the entry's target window forward; launch and retry if missing."""
    hwnd = _find_window_for_entry(entry)
    if hwnd:
        return _force_foreground(hwnd)
    _launch_app(entry)
    deadline = time.monotonic() + launch_timeout
    while time.monotonic() < deadline:
        time.sleep(0.15)
        hwnd = _find_window_for_entry(entry)
        if hwnd:
            return _force_foreground(hwnd)
    return False


# ----------------------------------------------------------------------------
# Chord service
# ----------------------------------------------------------------------------
class AppChordService:
    """Registers focus-prefix and remap-trigger hotkeys for each app entry."""

    def __init__(
        self,
        notify: Callable[[str, str], Any] | None = None,
        *,
        prefer_native: bool | None = None,
    ) -> None:
        self._entries: list[AppChordEntry] = []
        # All registered trigger hotkeys (prefix and mapping triggers alike).
        # Key is the normalized chord string; value is the keyboard-lib handle.
        self._hotkey_handles: dict[str, Any] = {}
        self._native_hotkeys = NativeHotkeyRegistrar(enabled=prefer_native)
        self._dispatch_lock = threading.Lock()
        self._dispatch_busy = False
        self._notify = notify
        self.failed: list[str] = []

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------
    def reload(self, entries: list[AppChordEntry]) -> list[str]:
        """Replace registrations with the given list. Returns failed chords."""
        self.unregister_all()
        self._entries = [e for e in entries if e.is_valid()]
        self.failed = []
        seen: set[str] = set()
        for entry in self._entries:
            if entry.prefix_chord and entry.prefix_chord not in seen:
                seen.add(entry.prefix_chord)
                self._register_hotkey(
                    entry.prefix_chord, self._make_prefix_callback(entry.prefix_chord),
                )
            for mapping in entry.valid_mappings():
                if mapping.trigger in seen:
                    LOGGER.warning(
                        "Mapping trigger %r is already used; skipping for '%s'",
                        mapping.trigger, entry.name,
                    )
                    continue
                seen.add(mapping.trigger)
                self._register_hotkey(
                    mapping.trigger,
                    self._make_mapping_callback(entry, mapping),
                )
        LOGGER.info(
            "App chord registration: %d entries / %d hotkeys registered, %d failed",
            len(self._entries), len(self._hotkey_handles), len(self.failed),
        )
        return list(self.failed)

    def unregister_all(self) -> None:
        """Remove every registered hotkey."""
        self._native_hotkeys.unregister_all()
        for chord, handle in list(self._hotkey_handles.items()):
            if handle is None:
                continue
            try:
                keyboard.remove_hotkey(handle)
            except (KeyError, ValueError):
                pass
            except Exception as exc:  # pylint: disable=broad-exception-caught
                LOGGER.debug("remove_hotkey for chord %r: %s", chord, exc)
        self._hotkey_handles.clear()
        self._entries = []

    @property
    def entries(self) -> list[AppChordEntry]:
        """Return the currently active entries (copy)."""
        return list(self._entries)

    # ------------------------------------------------------------------
    # Registration helpers
    # ------------------------------------------------------------------
    def _register_hotkey(self, chord: str, callback: Callable[[], None]) -> None:
        native_result = self._native_hotkeys.register(chord, callback)
        if native_result is True:
            self._hotkey_handles[chord] = None
            return

        # If native registration failed (native_result is False) or is unsupported (None),
        # we fall back to the hook-based keyboard library.
        try:
            handle = keyboard.add_hotkey(
                chord, callback, suppress=True, trigger_on_release=False,
            )
            self._hotkey_handles[chord] = handle
            if native_result is False:
                LOGGER.info(
                    "Native registration failed for chord %r; "
                    "fell back to hook-based registration successfully.",
                    chord,
                )
        except (ValueError, OSError) as exc:
            self.failed.append(chord)
            if native_result is False:
                LOGGER.warning(
                    "Could not register chord %r natively or via hook fallback: %s",
                    chord,
                    exc,
                )
            else:
                LOGGER.warning("Could not register chord %r: %s", chord, exc)
        except Exception as exc:  # pylint: disable=broad-exception-caught
            self.failed.append(chord)
            LOGGER.warning("Unexpected error registering chord %r: %s", chord, exc)

    def _make_prefix_callback(self, prefix: str) -> Callable[[], None]:
        def _cb() -> None:
            self._on_prefix_fired(prefix)
        return _cb

    def _make_mapping_callback(
        self, entry: AppChordEntry, mapping: ShortcutMapping,
    ) -> Callable[[], None]:
        def _cb() -> None:
            self._on_mapping_fired(entry, mapping)
        return _cb

    # ------------------------------------------------------------------
    # Dispatch
    # ------------------------------------------------------------------
    def _on_prefix_fired(self, prefix: str) -> None:
        with self._dispatch_lock:
            if self._dispatch_busy:
                return
            entry = next(
                (e for e in self._entries if e.prefix_chord == prefix), None,
            )
            if entry is None:
                return
            self._dispatch_busy = True
        threading.Thread(
            target=self._dispatch_focus_only,
            args=(entry,),
            name="app-chord-focus",
            daemon=True,
        ).start()

    def _on_mapping_fired(
        self, entry: AppChordEntry, mapping: ShortcutMapping,
    ) -> None:
        with self._dispatch_lock:
            if self._dispatch_busy:
                return
            self._dispatch_busy = True
        threading.Thread(
            target=self._dispatch_mapping,
            args=(entry, mapping),
            name="app-chord-remap",
            daemon=True,
        ).start()

    def _dispatch_focus_only(self, entry: AppChordEntry) -> None:
        """Focus target, wait for the user's action, then restore previous focus."""
        try:
            prev_hwnd = _get_foreground_window()
            if not self._focus_with_notify(entry):
                return
            time.sleep(POST_FOCUS_SETTLE_SECONDS)
            _wait_for_action_complete(timeout=ACTION_TIMEOUT_SECONDS)
            time.sleep(PRE_RESTORE_DELAY_SECONDS)
            if prev_hwnd:
                _force_foreground(prev_hwnd)
        finally:
            with self._dispatch_lock:
                self._dispatch_busy = False

    def _dispatch_mapping(
        self, entry: AppChordEntry, mapping: ShortcutMapping,
    ) -> None:
        """Focus target, send the mapping's action, then restore previous focus."""
        try:
            prev_hwnd = _get_foreground_window()
            if not self._focus_with_notify(entry):
                return
            # Wait for the user to release the trigger's modifiers so we don't
            # combine them with the action we're about to synthesize.
            _wait_for_modifiers_released(timeout=0.4)
            time.sleep(POST_FOCUS_SETTLE_SECONDS)
            try:
                keyboard.send(mapping.action)
            except (ValueError, OSError) as exc:
                LOGGER.warning(
                    "keyboard.send failed for action %r: %s", mapping.action, exc,
                )
                return
            time.sleep(PRE_RESTORE_DELAY_SECONDS)
            if prev_hwnd:
                _force_foreground(prev_hwnd)
        finally:
            with self._dispatch_lock:
                self._dispatch_busy = False

    def _focus_with_notify(self, entry: AppChordEntry) -> bool:
        focused = _focus_or_launch(entry)
        if not focused:
            LOGGER.warning(
                "Could not focus or launch %r for entry '%s'",
                entry.process_name or entry.exe_path, entry.name,
            )
            if self._notify is not None:
                self._notify(
                    "App chord",
                    f"Could not focus '{entry.name}'. Is it installed and the "
                    "process name correct?",
                )
        return focused


# ----------------------------------------------------------------------------
# Action-complete detection
# ----------------------------------------------------------------------------
def _wait_for_action_complete(*, timeout: float) -> bool:
    """Block until the user's next action keystroke is observed and all
    modifiers are released, or until ``timeout`` seconds elapse.

    Returns True if an action was observed, False on plain timeout.
    """
    done = threading.Event()
    state = {"action_seen": False}

    def _on_key(event: Any) -> None:
        name = (getattr(event, "name", "") or "").lower()
        event_type = getattr(event, "event_type", "")
        if event_type == "down" and name not in _MODIFIER_NAMES and name:
            state["action_seen"] = True
        elif event_type == "up":
            # After we've seen at least one non-modifier press, wait for all
            # modifiers to release before signalling completion.
            if state["action_seen"] and not _any_modifier_pressed():
                done.set()

    hook = keyboard.hook(_on_key)
    try:
        triggered = done.wait(timeout=timeout)
        return triggered
    finally:
        try:
            keyboard.unhook(hook)
        except (KeyError, ValueError):
            pass
        except Exception as exc:  # pylint: disable=broad-exception-caught
            LOGGER.debug("unhook failed: %s", exc)


def _any_modifier_pressed() -> bool:
    """Return True if any chord modifier (Ctrl/Shift/Alt/Win) is currently held."""
    try:
        return any(
            keyboard.is_pressed(mod)
            for mod in ("ctrl", "shift", "alt", "windows")
        )
    except Exception as exc:  # pylint: disable=broad-exception-caught
        LOGGER.debug("is_pressed check failed: %s", exc)
        return False


def _wait_for_modifiers_released(*, timeout: float) -> None:
    """Spin briefly waiting for the user to release Ctrl/Shift/Alt/Win.

    Prevents the synthesized action keys from being combined with modifiers
    the user is still holding from the trigger chord.
    """
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline and _any_modifier_pressed():
        time.sleep(0.015)
