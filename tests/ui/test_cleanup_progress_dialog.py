"""Tests for the cleanup progress/cancel overlay and preview dialog."""

from __future__ import annotations

from services.resource_control.models import ProcessCandidate, ReleaseResult
from services.resource_control.progress import CleanupPhase, CleanupProgress
from ui.cleanup_preview_dialog import CHOICE_FORCE, CHOICE_RUN, CleanupPreviewDialog
from ui.cleanup_progress_dialog import CleanupProgressDialog


def test_progress_dialog_updates_label_for_scan(qtbot) -> None:
    dialog = CleanupProgressDialog()
    qtbot.addWidget(dialog)
    dialog.on_progress(CleanupProgress(CleanupPhase.SCANNING, scanned=40, total=200))
    assert "40/200" in dialog._label.text()
    assert dialog._bar.maximum() == 200
    assert dialog._bar.value() == 40


def test_progress_dialog_indeterminate_for_non_scan_phase(qtbot) -> None:
    dialog = CleanupProgressDialog()
    qtbot.addWidget(dialog)
    dialog.on_progress(CleanupProgress(CleanupPhase.TRIMMING))
    assert dialog._bar.maximum() == 0  # indeterminate
    assert "Trimming" in dialog._label.text()


def test_progress_dialog_cancel_emits_signal(qtbot) -> None:
    dialog = CleanupProgressDialog()
    qtbot.addWidget(dialog)
    with qtbot.waitSignal(dialog.cancel_clicked, timeout=1000):
        dialog._cancel_btn.click()
    assert not dialog._cancel_btn.isEnabled()


def _preview_result() -> ReleaseResult:
    result = ReleaseResult(plan_only=True)
    result.preview_candidates = [
        ProcessCandidate(1, "a.exe", 1.0, 0.5, 0.0, 0.0, 0.0, None, 0.4, 1.0, 0.0),
        ProcessCandidate(2, "b.exe", 0.5, 0.3, 0.0, 0.0, 0.0, None, 0.2, 0.5, 0.0),
    ]
    return result


def test_preview_dialog_lists_candidates_and_defaults_to_cancel(qtbot) -> None:
    dialog = CleanupPreviewDialog(_preview_result())
    qtbot.addWidget(dialog)
    from PyQt6.QtWidgets import QTableWidget

    table = dialog.findChild(QTableWidget)
    assert table is not None
    assert table.rowCount() == 2
    assert dialog.choice == "cancel"  # nothing chosen yet


def test_preview_dialog_run_and_force_choices(qtbot) -> None:
    run_dialog = CleanupPreviewDialog(_preview_result())
    qtbot.addWidget(run_dialog)
    run_dialog._choose_run()
    assert run_dialog.choice == CHOICE_RUN

    force_dialog = CleanupPreviewDialog(_preview_result())
    qtbot.addWidget(force_dialog)
    force_dialog._choose_force()
    assert force_dialog.choice == CHOICE_FORCE
