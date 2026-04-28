from __future__ import annotations

from PyQt6.QtWidgets import QTextEdit

from ui.cleanup_result_dialog import CleanupResultDialog
from services.resource_control.models import CleanupMode, ReleaseResult, SkipReason


def test_cleanup_result_dialog_renders_summary_and_details(qtbot) -> None:
    result = ReleaseResult(mode=CleanupMode.SYSTEM_RECLAIM.value, profile_name="Balanced")
    result.record_skip(SkipReason.VISIBLE_WINDOW, count=3)
    dialog = CleanupResultDialog(result, title="Cleanup Result")
    qtbot.addWidget(dialog)

    assert dialog.windowTitle() == "Cleanup Result"
    text_box = dialog.findChild(QTextEdit)
    assert text_box is not None
    assert "visible-window protection (3)" in text_box.toPlainText()
