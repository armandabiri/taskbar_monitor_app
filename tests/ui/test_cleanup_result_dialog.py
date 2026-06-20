from __future__ import annotations

from PyQt6.QtWidgets import QPushButton, QTextEdit

from services.resource_control.models import CleanupMode, ReleaseResult, SkipReason
from ui.cleanup_result_dialog import CleanupResultDialog


def test_cleanup_result_dialog_renders_summary_and_details(qtbot) -> None:
    result = ReleaseResult(mode=CleanupMode.SYSTEM_RECLAIM.value, profile_name="Balanced")
    result.record_skip(SkipReason.VISIBLE_WINDOW, count=3)
    dialog = CleanupResultDialog(result, title="Cleanup Result")
    qtbot.addWidget(dialog)

    assert dialog.windowTitle() == "Cleanup Result"
    text_box = dialog.findChild(QTextEdit)
    assert text_box is not None
    assert "visible-window protection (3)" in text_box.toPlainText()


def test_zero_action_below_threshold_offers_escalate(qtbot) -> None:
    result = ReleaseResult(mode=CleanupMode.SYSTEM_RECLAIM.value, profile_name="Balanced")
    result.record_skip(SkipReason.BELOW_PRESSURE_THRESHOLD)
    fired: list[bool] = []
    dialog = CleanupResultDialog(
        result, title="Result", on_escalate=lambda: fired.append(True),
    )
    qtbot.addWidget(dialog)

    escalate = next(
        (b for b in dialog.findChildren(QPushButton) if b.objectName() == "escalate"), None,
    )
    assert escalate is not None
    escalate.click()
    assert fired == [True]


def test_forced_zero_action_run_hides_escalate(qtbot) -> None:
    result = ReleaseResult(mode=CleanupMode.SYSTEM_RECLAIM.value, profile_name="Balanced")
    result.was_forced = True
    dialog = CleanupResultDialog(result, title="Result", on_escalate=lambda: None)
    qtbot.addWidget(dialog)

    escalate = next(
        (b for b in dialog.findChildren(QPushButton) if b.objectName() == "escalate"), None,
    )
    assert escalate is None  # already forced — no point offering another force
