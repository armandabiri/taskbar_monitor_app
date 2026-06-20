from __future__ import annotations

from PyQt6.QtCore import QPoint, QRect
from PyQt6.QtGui import QColor, QImage

from ui.screenshot_editor_dialog import ScreenshotEditorDialog


def _build_image() -> QImage:
    image = QImage(80, 60, QImage.Format.Format_ARGB32)
    image.fill(QColor("#336699"))
    return image


def test_dialog_constructs(qtbot) -> None:
    dialog = ScreenshotEditorDialog(_build_image())
    qtbot.addWidget(dialog)
    assert dialog.active_tool == "Arrow"
    assert dialog.result_image().width() == 80


def test_tool_selection_switches_active_tool(qtbot) -> None:
    dialog = ScreenshotEditorDialog(_build_image())
    qtbot.addWidget(dialog)
    for tool in ("Text", "Rectangle", "Blur", "Crop", "Arrow"):
        dialog.set_active_tool(tool)
        assert dialog.active_tool == tool


def test_drawing_tools_keep_image_valid(qtbot) -> None:
    dialog = ScreenshotEditorDialog(_build_image())
    qtbot.addWidget(dialog)

    dialog.set_active_tool("Arrow")
    dialog.on_release(QPoint(5, 5), QPoint(40, 40))
    dialog.set_active_tool("Rectangle")
    dialog.on_release(QPoint(10, 10), QPoint(30, 30))
    dialog.set_active_tool("Blur")
    dialog.on_release(QPoint(0, 0), QPoint(50, 40))

    assert not dialog.result_image().isNull()
    assert dialog.result_image().size() == _build_image().size()


def test_crop_shrinks_working_image(qtbot) -> None:
    dialog = ScreenshotEditorDialog(_build_image())
    qtbot.addWidget(dialog)
    dialog.set_active_tool("Crop")
    dialog.on_release(QRect(10, 10, 20, 15).topLeft(), QRect(10, 10, 20, 15).bottomRight())
    assert dialog.result_image().width() < 80


def test_edit_returns_image_on_accept(monkeypatch) -> None:
    from PyQt6.QtWidgets import QDialog

    monkeypatch.setattr(
        ScreenshotEditorDialog, "exec", lambda self: QDialog.DialogCode.Accepted, raising=True
    )
    result = ScreenshotEditorDialog.edit(_build_image())
    assert isinstance(result, QImage)
    assert result.width() == 80


def test_edit_returns_none_on_cancel(monkeypatch) -> None:
    from PyQt6.QtWidgets import QDialog

    monkeypatch.setattr(
        ScreenshotEditorDialog, "exec", lambda self: QDialog.DialogCode.Rejected, raising=True
    )
    assert ScreenshotEditorDialog.edit(_build_image()) is None
