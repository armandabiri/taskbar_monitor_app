"""Dialog: manage per-app chord shortcuts (Ctrl+Alt+Shift+<key> focuses target)."""

from __future__ import annotations

import logging
import os
from typing import Callable

from PyQt6.QtCore import QSettings, Qt
from PyQt6.QtGui import QKeyEvent
from PyQt6.QtWidgets import (
    QAbstractItemView,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QMessageBox,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from services.app_chord_service import (
    AppChordEntry,
    ShortcutMapping,
    WindowInfo,
    _normalize_chord,  # type: ignore[attr-defined]
    enumerate_pickable_windows,
    load_chord_entries,
    save_chord_entries,
)

LOGGER = logging.getLogger(__name__)

_DIALOG_STYLE = """
QDialog { background-color: #1a1a1a; color: #ddd; }
QGroupBox {
    color: #ddd; border: 1px solid #333; border-radius: 4px;
    margin-top: 12px; padding-top: 8px;
}
QGroupBox::title { subcontrol-origin: margin; left: 10px; padding: 0 4px; color: #aaa; }
QLabel { color: #ccc; }
QLabel#hint { color: #888; font-size: 11px; }
QLabel#recordPreview {
    color: #55efc4;
    background-color: #111;
    border: 1px solid #2a2a2a;
    border-radius: 4px;
    padding: 18px;
    font-family: Consolas, monospace;
    font-size: 18px;
}
QLineEdit {
    background-color: #2a2a2a; color: #eee; border: 1px solid #444;
    border-radius: 3px; padding: 4px 8px; min-width: 160px;
}
QListWidget {
    background-color: #1f1f1f; color: #eee; border: 1px solid #333;
    border-radius: 3px;
}
QListWidget::item { padding: 6px; }
QListWidget::item:selected { background-color: #333; color: #fff; }
QPushButton {
    background-color: #2a2a2a; color: #eee; border: 1px solid #444;
    border-radius: 3px; padding: 5px 14px; min-width: 70px;
}
QPushButton:hover { background-color: #3a3a3a; border-color: #55efc4; }
QPushButton:pressed { background-color: #4a4a4a; }
"""

# Qt key codes that should be ignored when capturing a "main" key.
_MODIFIER_QT_KEYS = {
    Qt.Key.Key_Shift, Qt.Key.Key_Control, Qt.Key.Key_Alt, Qt.Key.Key_AltGr,
    Qt.Key.Key_Meta, Qt.Key.Key_Super_L, Qt.Key.Key_Super_R,
    Qt.Key.Key_Hyper_L, Qt.Key.Key_Hyper_R,
}


def _qt_key_to_name(event: QKeyEvent) -> str:
    """Map a Qt key event to a single-key name (no modifiers).

    Resolves the key by Qt key code first so chords like Ctrl+M work — when
    a Ctrl modifier is held, ``event.text()`` returns a control character
    (e.g. ``"\\x0d"`` for Ctrl+M) which fails ``isprintable()`` and would
    otherwise drop the keystroke. Returns "" if the key cannot be named in
    a form the ``keyboard`` library understands.
    """
    key = Qt.Key(event.key())
    mods = event.modifiers()
    on_keypad = bool(mods & Qt.KeyboardModifier.KeypadModifier)

    # Numpad keys — Qt reports them with KeypadModifier set. Use the
    # ``num <key>`` names that the keyboard library accepts.
    if on_keypad:
        npad = _NUMPAD_NAMES.get(key)
        if npad:
            return npad

    # Letter keys A–Z
    if Qt.Key.Key_A <= key <= Qt.Key.Key_Z:
        return chr(int(key)).lower()

    # Top-row digit keys 0–9
    if Qt.Key.Key_0 <= key <= Qt.Key.Key_9:
        return chr(int(key))

    # Function keys F1–F24
    if Qt.Key.Key_F1 <= key <= Qt.Key.Key_F24:
        return f"f{int(key) - int(Qt.Key.Key_F1) + 1}"

    if key in _NAMED_KEYS:
        return _NAMED_KEYS[key]

    # Last-resort fallback: use event.text() for punctuation we didn't map.
    text = event.text()
    if text and text.isprintable() and not text.isspace():
        char = text.lower()
        if len(char) == 1:
            return char
    return ""


# Named non-printable, lock, media, and browser keys. Values must be names the
# `keyboard` Python library accepts (probed against keyboard.add_hotkey).
_NAMED_KEYS: dict[Qt.Key, str] = {
    # Navigation / editing
    Qt.Key.Key_Space: "space",
    Qt.Key.Key_Tab: "tab",
    Qt.Key.Key_Backtab: "tab",
    Qt.Key.Key_Return: "enter",
    Qt.Key.Key_Enter: "enter",
    Qt.Key.Key_Escape: "esc",
    Qt.Key.Key_Backspace: "backspace",
    Qt.Key.Key_Delete: "delete",
    Qt.Key.Key_Insert: "insert",
    Qt.Key.Key_Home: "home",
    Qt.Key.Key_End: "end",
    Qt.Key.Key_PageUp: "page up",
    Qt.Key.Key_PageDown: "page down",
    Qt.Key.Key_Left: "left",
    Qt.Key.Key_Right: "right",
    Qt.Key.Key_Up: "up",
    Qt.Key.Key_Down: "down",
    # Punctuation
    Qt.Key.Key_Plus: "+",
    Qt.Key.Key_Minus: "-",
    Qt.Key.Key_Equal: "=",
    Qt.Key.Key_Comma: ",",
    Qt.Key.Key_Period: ".",
    Qt.Key.Key_Slash: "/",
    Qt.Key.Key_Backslash: "\\",
    Qt.Key.Key_Semicolon: ";",
    Qt.Key.Key_Apostrophe: "'",
    Qt.Key.Key_BracketLeft: "[",
    Qt.Key.Key_BracketRight: "]",
    Qt.Key.Key_QuoteLeft: "`",
    # Locks
    Qt.Key.Key_CapsLock: "caps lock",
    Qt.Key.Key_NumLock: "num lock",
    Qt.Key.Key_ScrollLock: "scroll lock",
    # System
    Qt.Key.Key_Print: "print screen",
    Qt.Key.Key_Pause: "pause",
    # Media
    Qt.Key.Key_VolumeUp: "volume up",
    Qt.Key.Key_VolumeDown: "volume down",
    Qt.Key.Key_VolumeMute: "volume mute",
    Qt.Key.Key_MediaPlay: "play/pause media",
    Qt.Key.Key_MediaPause: "play/pause media",
    Qt.Key.Key_MediaTogglePlayPause: "play/pause media",
    Qt.Key.Key_MediaStop: "stop media",
    Qt.Key.Key_MediaNext: "next track",
    Qt.Key.Key_MediaPrevious: "previous track",
    # Browser
    Qt.Key.Key_Back: "browser back",
    Qt.Key.Key_Forward: "browser forward",
    Qt.Key.Key_Refresh: "browser refresh",
    Qt.Key.Key_Stop: "browser stop",
    Qt.Key.Key_Favorites: "browser favorites",
}

# Numpad-specific names. The Qt key code is the same as the equivalent
# main-keyboard key — what distinguishes them is the KeypadModifier flag.
_NUMPAD_NAMES: dict[Qt.Key, str] = {
    Qt.Key.Key_0: "num 0",
    Qt.Key.Key_1: "num 1",
    Qt.Key.Key_2: "num 2",
    Qt.Key.Key_3: "num 3",
    Qt.Key.Key_4: "num 4",
    Qt.Key.Key_5: "num 5",
    Qt.Key.Key_6: "num 6",
    Qt.Key.Key_7: "num 7",
    Qt.Key.Key_8: "num 8",
    Qt.Key.Key_9: "num 9",
    Qt.Key.Key_Plus: "num plus",
    Qt.Key.Key_Minus: "num -",
    Qt.Key.Key_Asterisk: "num *",
    Qt.Key.Key_Slash: "num /",
    Qt.Key.Key_Period: "decimal",
    Qt.Key.Key_Enter: "enter",
}


# ----------------------------------------------------------------------------
# Full-chord capture line edit (records modifiers + main key)
# ----------------------------------------------------------------------------
class FullChordLineEdit(QLineEdit):
    """Plain line edit for chord strings. Typing is direct; the parent dialog
    pops a :class:`RecordShortcutDialog` for live-capture recording.
    """

    def __init__(self, parent: QWidget | None = None) -> None:
        super().__init__(parent)
        self.setPlaceholderText("e.g., win+alt+m")


def _qt_modifiers_pretty(mods: Qt.KeyboardModifier) -> list[str]:
    """Return a list of display-friendly modifier names currently held."""
    parts: list[str] = []
    if mods & Qt.KeyboardModifier.MetaModifier:
        parts.append("Win")
    if mods & Qt.KeyboardModifier.ControlModifier:
        parts.append("Ctrl")
    if mods & Qt.KeyboardModifier.AltModifier:
        parts.append("Alt")
    if mods & Qt.KeyboardModifier.ShiftModifier:
        parts.append("Shift")
    return parts


class RecordShortcutDialog(QDialog):
    """Modal panel that captures a chord with a live preview + OK/Cancel.

    The user can press any chord — even one currently registered as a global
    hotkey — because the parent (manager) dialog pauses the chord service
    while it is open. Pressing more keys overwrites the captured chord, so
    re-recording is just pressing again.
    """

    def __init__(self, title: str, parent: QWidget | None) -> None:
        super().__init__(parent)
        self.setWindowTitle(title)
        self.setStyleSheet(_DIALOG_STYLE)
        self.setMinimumSize(420, 220)
        self._captured: str = ""

        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 18, 20, 18)
        layout.setSpacing(10)

        self._instruction_label = QLabel(
            "Press the keys you want for this shortcut. Press again to "
            "overwrite. Click OK when ready."
        )
        self._instruction_label.setObjectName("hint")
        self._instruction_label.setWordWrap(True)
        layout.addWidget(self._instruction_label)

        self._preview_label = QLabel("…")
        self._preview_label.setObjectName("recordPreview")
        self._preview_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self._preview_label.setMinimumHeight(60)
        layout.addWidget(self._preview_label, 1)

        button_box = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        ok_btn = button_box.button(QDialogButtonBox.StandardButton.Ok)
        if ok_btn is not None:
            ok_btn.setEnabled(False)
            ok_btn.setDefault(False)
            ok_btn.setAutoDefault(False)
        self._ok_btn = ok_btn
        button_box.accepted.connect(self.accept)
        button_box.rejected.connect(self.reject)
        layout.addWidget(button_box)

        # Capture all key events on the dialog itself rather than a focused
        # child, so the user doesn't have to think about focus.
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)

    def chord(self) -> str:
        return self._captured

    def keyPressEvent(self, a0: QKeyEvent | None) -> None:  # noqa: N802  pylint: disable=invalid-name
        if a0 is None:
            return
        # Show held modifiers live, even before the main key is pressed.
        if Qt.Key(a0.key()) in _MODIFIER_QT_KEYS:
            pretty = _qt_modifiers_pretty(a0.modifiers())
            self._preview_label.setText(" + ".join(pretty + ["…"]) if pretty else "…")
            a0.accept()
            return
        chord = _qt_event_to_chord(a0)
        if chord:
            self._captured = chord
            self._preview_label.setText(_pretty_chord(chord))
            if self._ok_btn is not None:
                self._ok_btn.setEnabled(True)
                self._ok_btn.setDefault(True)
            a0.accept()
            return
        # Key fired but we couldn't map it to a name the keyboard library
        # understands (e.g. vendor-specific G-keys, macro keys, some media
        # keys the OS doesn't standardize). Tell the user it registered but
        # can't be used, so they don't think Record is broken.
        scan = a0.nativeScanCode()
        vk = a0.nativeVirtualKey()
        self._preview_label.setText(
            f"Unrecognized key (scancode {scan}, vk {vk}) — try another"
        )
        a0.accept()

    def keyReleaseEvent(self, a0: QKeyEvent | None) -> None:  # noqa: N802  pylint: disable=invalid-name
        # When all modifiers are released and no chord captured yet, clear preview.
        if a0 is None:
            return
        if not self._captured and Qt.Key(a0.key()) in _MODIFIER_QT_KEYS:
            pretty = _qt_modifiers_pretty(a0.modifiers())
            self._preview_label.setText(" + ".join(pretty + ["…"]) if pretty else "…")
        a0.accept()


def record_shortcut(title: str, parent: QWidget | None) -> str:
    """Open the recorder dialog and return the captured chord, or '' on cancel."""
    dialog = RecordShortcutDialog(title, parent)
    if dialog.exec() != QDialog.DialogCode.Accepted:
        return ""
    return dialog.chord()


def _qt_event_to_chord(event: QKeyEvent) -> str:
    """Convert a QKeyEvent (modifiers + main key) into a normalized chord."""
    mods = event.modifiers()
    parts: list[str] = []
    if mods & Qt.KeyboardModifier.MetaModifier:
        parts.append("win")
    if mods & Qt.KeyboardModifier.ControlModifier:
        parts.append("ctrl")
    if mods & Qt.KeyboardModifier.AltModifier:
        parts.append("alt")
    if mods & Qt.KeyboardModifier.ShiftModifier:
        parts.append("shift")
    name = _qt_key_to_name(event)
    if not name:
        return ""
    parts.append(name)
    return _normalize_chord("+".join(parts))


# ----------------------------------------------------------------------------
# Pick-Window dialog
# ----------------------------------------------------------------------------
class PickWindowDialog(QDialog):
    """List of running top-level windows the user can pick from."""

    def __init__(self, parent: QWidget | None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Pick a window")
        self.setStyleSheet(_DIALOG_STYLE)
        self.setMinimumSize(640, 600)
        self._selected: WindowInfo | None = None
        self._windows: list[WindowInfo] = []

        layout = QVBoxLayout(self)
        layout.setContentsMargins(14, 14, 14, 14)
        layout.setSpacing(8)

        hint = QLabel(
            "Pick the window of the app you want this chord to focus. The display "
            "name, process name, executable, and window title are auto-filled."
        )
        hint.setObjectName("hint")
        hint.setWordWrap(True)
        layout.addWidget(hint)

        filter_row = QHBoxLayout()
        filter_row.addWidget(QLabel("Filter:"))
        self._filter_edit = QLineEdit()
        self._filter_edit.setPlaceholderText("Type to filter by title or process…")
        self._filter_edit.textChanged.connect(self._apply_filter)
        filter_row.addWidget(self._filter_edit, 1)
        refresh_btn = QPushButton("Refresh")
        refresh_btn.clicked.connect(self._reload_windows)
        filter_row.addWidget(refresh_btn)
        layout.addLayout(filter_row)

        self._list = QListWidget()
        self._list.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self._list.itemDoubleClicked.connect(lambda _i: self._accept_selection())
        layout.addWidget(self._list, 1)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self._accept_selection)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

        self._reload_windows()

    def picked(self) -> WindowInfo | None:
        return self._selected

    def _reload_windows(self) -> None:
        try:
            self._windows = enumerate_pickable_windows()
        except Exception:  # pylint: disable=broad-exception-caught
            LOGGER.exception("Could not enumerate windows")
            self._windows = []
        self._apply_filter(self._filter_edit.text())

    def _apply_filter(self, needle: str) -> None:
        needle_l = (needle or "").strip().lower()
        self._list.clear()
        for win in self._windows:
            label = _format_window_label(win)
            if needle_l and needle_l not in label.lower():
                continue
            item = QListWidgetItem(label)
            item.setData(Qt.ItemDataRole.UserRole, win)
            self._list.addItem(item)
        if self._list.count() > 0:
            self._list.setCurrentRow(0)

    def _accept_selection(self) -> None:
        item = self._list.currentItem()
        if item is None:
            QMessageBox.information(self, "Pick a window", "Select a window first.")
            return
        self._selected = item.data(Qt.ItemDataRole.UserRole)
        self.accept()


def _format_window_label(win: WindowInfo) -> str:
    proc = win.process_name or "(unknown process)"
    return f"{win.title}\n    {proc}    pid={win.pid}"


# ----------------------------------------------------------------------------
# Edit-one-entry dialog
# ----------------------------------------------------------------------------
class AppChordEditDialog(QDialog):
    """Edit a single AppChordEntry."""

    def __init__(self, entry: AppChordEntry | None, parent: QWidget | None) -> None:
        super().__init__(parent)
        self.setWindowTitle("App Chord Shortcut")
        self.setStyleSheet(_DIALOG_STYLE)
        self.setMinimumSize(560, 700)
        self._existing = entry
        self._result: AppChordEntry | None = None

        layout = QVBoxLayout(self)
        layout.setContentsMargins(14, 14, 14, 14)
        layout.setSpacing(10)

        pick_row = QHBoxLayout()
        pick_btn = QPushButton("Pick Window…")
        pick_btn.setToolTip(
            "Choose a running window — display name, process, executable and "
            "title fields will be filled in for you."
        )
        pick_btn.clicked.connect(self._pick_window)
        pick_row.addWidget(pick_btn)
        pick_row.addStretch(1)
        layout.addLayout(pick_row)

        target_group = QGroupBox("Target Application")
        target_form = QFormLayout(target_group)
        target_form.setLabelAlignment(Qt.AlignmentFlag.AlignRight)
        target_form.setHorizontalSpacing(14)
        target_form.setVerticalSpacing(8)

        self._name_edit = QLineEdit()
        self._name_edit.setPlaceholderText("Microsoft Teams")
        target_form.addRow("Display name:", self._name_edit)

        self._process_edit = QLineEdit()
        self._process_edit.setPlaceholderText("ms-teams.exe")
        target_form.addRow("Process name:", self._process_edit)

        exe_row = QHBoxLayout()
        self._exe_edit = QLineEdit()
        self._exe_edit.setPlaceholderText("Optional: full path to launch if not running")
        exe_row.addWidget(self._exe_edit, 1)
        browse_btn = QPushButton("Browse…")
        browse_btn.clicked.connect(self._browse_exe)
        exe_row.addWidget(browse_btn)
        target_form.addRow("Executable:", _wrap_layout(exe_row))

        self._title_edit = QLineEdit()
        self._title_edit.setPlaceholderText("Optional: window title must contain this text")
        target_form.addRow("Window title:", self._title_edit)
        layout.addWidget(target_group)

        chord_group = QGroupBox("Prefix Shortcut")
        chord_form = QFormLayout(chord_group)
        chord_form.setLabelAlignment(Qt.AlignmentFlag.AlignRight)
        chord_form.setHorizontalSpacing(14)
        chord_form.setVerticalSpacing(8)

        prefix_row = QHBoxLayout()
        self._prefix_chord_edit = FullChordLineEdit()
        self._prefix_chord_edit.setPlaceholderText("e.g., ctrl+alt+shift+t or f12")
        prefix_row.addWidget(self._prefix_chord_edit, 1)
        self._record_btn = QPushButton("Record")
        self._record_btn.clicked.connect(self._begin_capture)
        prefix_row.addWidget(self._record_btn)
        chord_form.addRow("Prefix:", _wrap_layout(prefix_row))

        chord_hint = QLabel(
            "Press any chord you like (e.g. Ctrl+Alt+Shift+T, F12, Win+`) from "
            "any window to focus this app. After it has focus, press the app's "
            "own shortcut — the service waits for you to finish, then restores "
            "focus to wherever you were. Leave blank if you only want to use "
            "the remappings below."
        )
        chord_hint.setObjectName("hint")
        chord_hint.setWordWrap(True)
        chord_form.addRow("", chord_hint)
        layout.addWidget(chord_group)

        # ---- Shortcut remappings ----
        mapping_group = QGroupBox("Shortcut Remappings (optional)")
        mapping_layout = QVBoxLayout(mapping_group)
        mapping_layout.setSpacing(6)

        mapping_hint = QLabel(
            "Remap any global shortcut to one of the target app's own "
            "shortcuts. When the trigger fires from any window, the app is "
            "focused, the action is sent to it, then focus returns. Type the "
            "chords or press Record."
        )
        mapping_hint.setObjectName("hint")
        mapping_hint.setWordWrap(True)
        mapping_layout.addWidget(mapping_hint)

        add_row = QHBoxLayout()
        add_row.addWidget(QLabel("Your shortcut:"))
        self._mapping_trigger_edit = FullChordLineEdit()
        add_row.addWidget(self._mapping_trigger_edit, 1)
        self._mapping_trigger_record_btn = QPushButton("Record")
        self._mapping_trigger_record_btn.clicked.connect(
            lambda: self._begin_mapping_capture(self._mapping_trigger_edit)
        )
        add_row.addWidget(self._mapping_trigger_record_btn)
        mapping_layout.addLayout(add_row)

        add_row2 = QHBoxLayout()
        add_row2.addWidget(QLabel("App's shortcut:"))
        self._mapping_action_edit = FullChordLineEdit()
        self._mapping_action_edit.setPlaceholderText("e.g., ctrl+shift+m")
        add_row2.addWidget(self._mapping_action_edit, 1)
        self._mapping_action_record_btn = QPushButton("Record")
        self._mapping_action_record_btn.clicked.connect(
            lambda: self._begin_mapping_capture(self._mapping_action_edit)
        )
        add_row2.addWidget(self._mapping_action_record_btn)
        mapping_layout.addLayout(add_row2)

        add_row3 = QHBoxLayout()
        add_row3.addWidget(QLabel("Label (optional):"))
        self._mapping_label_edit = QLineEdit()
        self._mapping_label_edit.setPlaceholderText("e.g., Mute mic")
        add_row3.addWidget(self._mapping_label_edit, 1)
        add_btn = QPushButton("Add to List")
        add_btn.clicked.connect(self._add_mapping)
        add_row3.addWidget(add_btn)
        mapping_layout.addLayout(add_row3)

        self._mapping_list = QListWidget()
        self._mapping_list.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        mapping_layout.addWidget(self._mapping_list, 1)

        remove_row = QHBoxLayout()
        remove_row.addStretch(1)
        remove_btn = QPushButton("Remove Selected")
        remove_btn.clicked.connect(self._remove_selected_mapping)
        remove_row.addWidget(remove_btn)
        mapping_layout.addLayout(remove_row)

        layout.addWidget(mapping_group, 1)

        button_box = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel
        )
        save_btn = button_box.button(QDialogButtonBox.StandardButton.Save)
        if save_btn is not None:
            save_btn.setText("Save")
        button_box.accepted.connect(self._on_save)
        button_box.rejected.connect(self.reject)
        layout.addWidget(button_box)

        if entry is not None:
            self._name_edit.setText(entry.name)
            self._process_edit.setText(entry.process_name)
            self._exe_edit.setText(entry.exe_path)
            self._title_edit.setText(entry.window_title_contains)
            self._prefix_chord_edit.setText(entry.prefix_chord)
            self._mappings: list[ShortcutMapping] = list(entry.mappings)
        else:
            self._mappings = []

        self._refresh_mapping_list()

    def _begin_capture(self) -> None:
        chord = record_shortcut("Record prefix chord", parent=self)
        if chord:
            self._prefix_chord_edit.setText(chord)

    def _pick_window(self) -> None:
        dialog = PickWindowDialog(self)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        win = dialog.picked()
        if win is None:
            return
        # Always overwrite the auto-fillable fields when the user picks.
        self._name_edit.setText(_derive_display_name(win))
        self._process_edit.setText(win.process_name)
        if win.exe_path:
            self._exe_edit.setText(win.exe_path)
        self._title_edit.setText(win.title)

    def _browse_exe(self) -> None:
        start_dir = self._exe_edit.text().strip()
        if start_dir and os.path.isfile(start_dir):
            start_dir = os.path.dirname(start_dir)
        elif not start_dir:
            start_dir = os.environ.get("ProgramFiles", "")
        chosen, _ = QFileDialog.getOpenFileName(
            self,
            "Pick application executable",
            start_dir,
            "Executables (*.exe);;All files (*.*)",
        )
        if chosen:
            self._exe_edit.setText(chosen)
            if not self._process_edit.text().strip():
                self._process_edit.setText(os.path.basename(chosen))

    # ---- Mapping list helpers ----
    def _refresh_mapping_list(self) -> None:
        self._mapping_list.clear()
        for mapping in self._mappings:
            self._mapping_list.addItem(_format_mapping_label(mapping))

    def _begin_mapping_capture(self, edit: FullChordLineEdit) -> None:
        title = (
            "Record your shortcut" if edit is self._mapping_trigger_edit
            else "Record the app's shortcut"
        )
        chord = record_shortcut(title, parent=self)
        if chord:
            edit.setText(chord)

    def _add_mapping(self) -> None:
        trigger = _normalize_chord(self._mapping_trigger_edit.text())
        action = _normalize_chord(self._mapping_action_edit.text())
        label = self._mapping_label_edit.text().strip()
        if not trigger or not action:
            QMessageBox.warning(
                self,
                "Missing field",
                "Both 'Your shortcut' and 'App's shortcut' are required.",
            )
            return
        if trigger == action:
            QMessageBox.warning(
                self,
                "Invalid mapping",
                "Your shortcut and the app's shortcut must be different.",
            )
            return
        if any(m.trigger == trigger for m in self._mappings):
            QMessageBox.warning(
                self,
                "Duplicate trigger",
                f"A mapping with trigger {_pretty_chord(trigger)} already exists.",
            )
            return
        self._mappings.append(ShortcutMapping(trigger=trigger, action=action, label=label))
        self._refresh_mapping_list()
        self._mapping_trigger_edit.clear()
        self._mapping_action_edit.clear()
        self._mapping_label_edit.clear()

    def _remove_selected_mapping(self) -> None:
        row = self._mapping_list.currentRow()
        if 0 <= row < len(self._mappings):
            self._mappings.pop(row)
            self._refresh_mapping_list()

    # ---- Save ----
    def _on_save(self) -> None:
        name = self._name_edit.text().strip()
        process_name = self._process_edit.text().strip()
        exe_path = self._exe_edit.text().strip()
        prefix = _normalize_chord(self._prefix_chord_edit.text())

        if not name:
            QMessageBox.warning(self, "Missing field", "Please enter a display name.")
            return
        if not (process_name or exe_path):
            QMessageBox.warning(
                self,
                "Missing field",
                "Provide a process name or an executable path so the target app can be found.",
            )
            return
        if not prefix and not self._mappings:
            QMessageBox.warning(
                self,
                "Missing field",
                "Choose a prefix chord or add at least one shortcut remapping.",
            )
            return

        self._result = AppChordEntry(
            name=name,
            process_name=process_name,
            exe_path=exe_path,
            prefix_chord=prefix,
            window_title_contains=self._title_edit.text().strip(),
            mappings=list(self._mappings),
        )
        self.accept()

    def result_entry(self) -> AppChordEntry | None:
        return self._result


# ----------------------------------------------------------------------------
# Manager dialog
# ----------------------------------------------------------------------------
class AppChordManagerDialog(QDialog):
    """List/add/edit/delete app chord entries."""

    def __init__(
        self,
        settings: QSettings,
        on_apply: Callable[[list[AppChordEntry]], None] | None,
        parent: QWidget | None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("App Chord Shortcuts")
        self.setStyleSheet(_DIALOG_STYLE)
        self.setMinimumSize(640, 560)
        self._settings = settings
        self._on_apply = on_apply
        self._entries: list[AppChordEntry] = load_chord_entries(settings)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(14, 14, 14, 14)
        layout.setSpacing(10)

        hint = QLabel(
            "Each entry focuses a target app from any window via its prefix "
            "chord, or remaps any global shortcut to the app's own shortcut. "
            "Focus is restored when you're done."
        )
        hint.setObjectName("hint")
        hint.setWordWrap(True)
        layout.addWidget(hint)

        self._list = QListWidget()
        self._list.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self._list.itemDoubleClicked.connect(lambda _i: self._edit_selected())
        layout.addWidget(self._list, 1)

        action_row = QHBoxLayout()
        add_btn = QPushButton("Add…")
        add_btn.clicked.connect(self._add_new)
        edit_btn = QPushButton("Edit…")
        edit_btn.clicked.connect(self._edit_selected)
        delete_btn = QPushButton("Delete")
        delete_btn.clicked.connect(self._delete_selected)
        action_row.addWidget(add_btn)
        action_row.addWidget(edit_btn)
        action_row.addWidget(delete_btn)
        action_row.addStretch(1)
        layout.addLayout(action_row)

        button_box = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        button_box.rejected.connect(self.accept)
        layout.addWidget(button_box)

        self._refresh_list()

    def _refresh_list(self) -> None:
        self._list.clear()
        for entry in self._entries:
            item = QListWidgetItem(_format_entry_label(entry))
            self._list.addItem(item)

    def _selected_index(self) -> int:
        row = self._list.currentRow()
        return row if 0 <= row < len(self._entries) else -1

    def _add_new(self) -> None:
        dialog = AppChordEditDialog(None, parent=self)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        new_entry = dialog.result_entry()
        if new_entry is None:
            return
        conflict = self._find_prefix_conflict(new_entry, exclude_index=-1)
        if conflict is not None:
            QMessageBox.warning(
                self,
                "Duplicate prefix",
                f"Another entry '{conflict.name}' already uses prefix "
                f"{conflict.prefix_chord}. Pick a different key.",
            )
            return
        self._entries.append(new_entry)
        self._refresh_list()
        self._list.setCurrentRow(len(self._entries) - 1)
        self._persist_and_apply()

    def _edit_selected(self) -> None:
        index = self._selected_index()
        if index < 0:
            return
        dialog = AppChordEditDialog(self._entries[index], parent=self)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        edited = dialog.result_entry()
        if edited is None:
            return
        conflict = self._find_prefix_conflict(edited, exclude_index=index)
        if conflict is not None:
            QMessageBox.warning(
                self,
                "Duplicate prefix",
                f"Another entry '{conflict.name}' already uses prefix "
                f"{conflict.prefix_chord}. Pick a different key.",
            )
            return
        self._entries[index] = edited
        self._refresh_list()
        self._list.setCurrentRow(index)
        self._persist_and_apply()

    def _delete_selected(self) -> None:
        index = self._selected_index()
        if index < 0:
            return
        entry = self._entries[index]
        confirm = QMessageBox.question(
            self,
            "Delete chord",
            f"Delete '{entry.name}' ({entry.prefix_chord})?",
        )
        if confirm != QMessageBox.StandardButton.Yes:
            return
        self._entries.pop(index)
        self._refresh_list()
        self._persist_and_apply()

    def _find_prefix_conflict(
        self, entry: AppChordEntry, *, exclude_index: int
    ) -> AppChordEntry | None:
        for i, existing in enumerate(self._entries):
            if i == exclude_index:
                continue
            if existing.prefix_chord == entry.prefix_chord:
                return existing
        return None

    def _persist_and_apply(self) -> None:
        """Persist entries to QSettings and reload the hotkey service.

        Called after every add/edit/delete so changes survive closing the
        dialog with Close or the window's X button — the user does not have
        to remember to click an extra save button.
        """
        try:
            save_chord_entries(self._settings, self._entries)
        except Exception:  # pylint: disable=broad-exception-caught
            LOGGER.exception("Could not save app chord entries")
            QMessageBox.critical(
                self,
                "Save failed",
                "App chord entries could not be saved. Check the log for details.",
            )
            return
        if self._on_apply is not None:
            try:
                self._on_apply(list(self._entries))
            except Exception:  # pylint: disable=broad-exception-caught
                LOGGER.exception("on_apply callback failed for app chord entries")


def open_app_chord_manager(
    settings: QSettings,
    on_apply: Callable[[list[AppChordEntry]], None] | None = None,
    parent: QWidget | None = None,
) -> None:
    """Open the app chord manager dialog (modal)."""
    dialog = AppChordManagerDialog(settings, on_apply=on_apply, parent=parent)
    dialog.exec()


def _format_entry_label(entry: AppChordEntry) -> str:
    target = entry.process_name or os.path.basename(entry.exe_path) or "(no target)"
    prefix_part = _pretty_chord(entry.prefix_chord) if entry.prefix_chord else "(no prefix)"
    valid_mappings = entry.valid_mappings()
    extra = ""
    if valid_mappings:
        extra = f"    +{len(valid_mappings)} remap"
        if len(valid_mappings) > 1:
            extra += "s"
    return (
        f"{entry.name}    [{target}]\n"
        f"    {prefix_part}{extra}"
    )


def _format_mapping_label(mapping: ShortcutMapping) -> str:
    base = f"{_pretty_chord(mapping.trigger)}  →  {_pretty_chord(mapping.action)}"
    if mapping.label:
        base += f"    — {mapping.label}"
    return base


def _pretty_chord(chord: str) -> str:
    """Convert ``ctrl+alt+shift+t`` to ``Ctrl+Alt+Shift+T`` for display."""
    return "+".join(p.capitalize() if len(p) > 1 else p.upper() for p in chord.split("+") if p)


def _derive_display_name(win: WindowInfo) -> str:
    """Pick a sensible default display name from a picked window."""
    proc = win.process_name
    if proc:
        base, _ext = os.path.splitext(proc)
        if base:
            return base.replace("-", " ").replace("_", " ").title()
    if win.title:
        return win.title
    return "App"


def _wrap_layout(layout) -> QWidget:
    widget = QWidget()
    widget.setLayout(layout)
    return widget
