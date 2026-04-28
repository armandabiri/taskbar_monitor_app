from __future__ import annotations

from PyQt6.QtCore import QSettings
from PyQt6.QtWidgets import QDialog, QWidget

from ui.kill_confirm_dialog import _position_dialog_above_parent as position_kill_dialog
from ui.resource_settings_dialog import (
    ResourceSettingsDialog,
    _position_dialog_above_parent as position_settings_dialog,
)


def _build_parent() -> QWidget:
    parent = QWidget()
    parent.setGeometry(500, 900, 600, 60)
    parent.show()
    return parent


def test_resource_settings_dialog_positions_above_parent(qtbot, tmp_path) -> None:
    settings = QSettings(str(tmp_path / "resource-settings.ini"), QSettings.Format.IniFormat)
    parent = _build_parent()
    qtbot.addWidget(parent)

    dialog = ResourceSettingsDialog(settings, parent=parent)
    qtbot.addWidget(dialog)
    position_settings_dialog(dialog, parent)

    assert dialog.y() < parent.y()


def test_kill_confirm_dialog_positions_above_parent(qtbot) -> None:
    parent = _build_parent()
    qtbot.addWidget(parent)

    dialog = QDialog(parent)
    dialog.setMinimumSize(620, 540)
    qtbot.addWidget(dialog)
    position_kill_dialog(dialog, parent)

    assert dialog.y() < parent.y()
