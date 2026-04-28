"""Dialog for displaying cleanup results and diagnostic details."""

from __future__ import annotations

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
QTextEdit {
    background-color: #1f1f1f; color: #eee; border: 1px solid #333;
    selection-background-color: #2c2c2c;
}
QPushButton {
    background-color: #2a2a2a; color: #eee; border: 1px solid #444;
    border-radius: 3px; padding: 5px 12px;
}
QPushButton:hover { background-color: #3a3a3a; border-color: #55efc4; }
"""


class CleanupResultDialog(QDialog):
    """Display the details of a cleanup run."""

    def __init__(
        self,
        result: ReleaseResult,
        *,
        title: str,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle(title)
        self.setStyleSheet(_DIALOG_STYLE)
        self.setMinimumSize(640, 420)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(14, 14, 14, 14)
        layout.setSpacing(10)

        summary = QLabel(result.summary)
        summary.setObjectName("summary")
        summary.setWordWrap(True)
        layout.addWidget(summary)

        details = QTextEdit(self)
        details.setReadOnly(True)
        details.setPlainText(result.details)
        layout.addWidget(details, 1)

        btn_row = QHBoxLayout()
        btn_row.addStretch(1)
        close_btn = QPushButton("Close")
        close_btn.clicked.connect(self.accept)
        btn_row.addWidget(close_btn)
        layout.addLayout(btn_row)


def open_cleanup_result_dialog(
    result: ReleaseResult,
    *,
    title: str,
    parent: QWidget | None = None,
) -> None:
    """Open the cleanup result dialog modally."""

    dialog = CleanupResultDialog(result, title=title, parent=parent)
    dialog.exec()
