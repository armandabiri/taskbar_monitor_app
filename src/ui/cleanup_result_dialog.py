"""Dialog for displaying cleanup results and diagnostic details."""

from __future__ import annotations

from typing import Callable

from PyQt6.QtWidgets import (
    QDialog,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from services.resource_control.models import ReleaseResult

_DIALOG_STYLE = """
QDialog { background-color: #1a1a1a; color: #ddd; }
QLabel { color: #ddd; }
QLabel#summary { color: #55efc4; font-weight: bold; }
QLabel#reason { color: #ffeaa7; }
QTextEdit {
    background-color: #1f1f1f; color: #eee; border: 1px solid #333;
    selection-background-color: #2c2c2c;
}
QPushButton {
    background-color: #2a2a2a; color: #eee; border: 1px solid #444;
    border-radius: 3px; padding: 5px 12px;
}
QPushButton#escalate { border-color: #e17055; }
QPushButton:hover { background-color: #3a3a3a; border-color: #55efc4; }
"""


class CleanupResultDialog(QDialog):
    """Display the details of a cleanup run.

    On a 0-action run that was not already forced, an "escalate" button is
    offered (when ``on_escalate`` is supplied) so the user can immediately run
    a forced full pass instead of being left with an unexplained no-op.
    """

    def __init__(
        self,
        result: ReleaseResult,
        *,
        title: str,
        parent: QWidget | None = None,
        on_escalate: Callable[[], None] | None = None,
    ) -> None:
        super().__init__(parent)
        self._on_escalate = on_escalate
        self.setWindowTitle(title)
        self.setStyleSheet(_DIALOG_STYLE)
        self.setMinimumSize(640, 440)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(14, 14, 14, 14)
        layout.setSpacing(10)

        summary = QLabel(result.summary)
        summary.setObjectName("summary")
        summary.setWordWrap(True)
        layout.addWidget(summary)

        # Plain-language explanation — front and center so a no-op run reads as
        # an explained outcome, not a broken button.
        reason = QLabel(result.plain_reason())
        reason.setObjectName("reason")
        reason.setWordWrap(True)
        layout.addWidget(reason)

        details = QTextEdit(self)
        details.setReadOnly(True)
        details.setPlainText(result.details)
        layout.addWidget(details, 1)

        btn_row = QHBoxLayout()
        offer_escalate = (
            on_escalate is not None
            and result.processes_cleaned_total == 0
            and not result.was_forced
        )
        if offer_escalate:
            escalate_btn = QPushButton("Force a full pass")
            escalate_btn.setObjectName("escalate")
            escalate_btn.setToolTip("Run again, bypassing the memory-pressure threshold.")
            escalate_btn.clicked.connect(self._on_escalate_clicked)
            btn_row.addWidget(escalate_btn)
        btn_row.addStretch(1)
        close_btn = QPushButton("Close")
        close_btn.clicked.connect(self.accept)
        btn_row.addWidget(close_btn)
        layout.addLayout(btn_row)

    def _on_escalate_clicked(self) -> None:
        self.accept()
        if self._on_escalate is not None:
            self._on_escalate()


def open_cleanup_result_dialog(
    result: ReleaseResult,
    *,
    title: str,
    parent: QWidget | None = None,
    on_escalate: Callable[[], None] | None = None,
) -> None:
    """Open the cleanup result dialog modally."""

    dialog = CleanupResultDialog(result, title=title, parent=parent, on_escalate=on_escalate)
    dialog.exec()
