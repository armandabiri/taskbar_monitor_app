"""Dry-run preview: show what a cleanup *would* do before it acts.

Lists the ranked candidate processes and the estimated reclaim, then lets the
user confirm (Run now / Run forced) or cancel. Nothing is executed by opening
this dialog — it renders a ``plan_only`` :class:`ReleaseResult`.
"""

from __future__ import annotations

from PyQt6.QtWidgets import (
    QAbstractItemView,
    QDialog,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from services.resource_control.models import ReleaseResult

# Returned via ``choice`` after the dialog closes.
CHOICE_CANCEL = "cancel"
CHOICE_RUN = "run"
CHOICE_FORCE = "force"

_DIALOG_STYLE = """
QDialog { background-color: #1a1a1a; color: #ddd; }
QLabel { color: #ddd; }
QLabel#headline { color: #55efc4; font-weight: bold; }
QTableWidget {
    background-color: #1f1f1f; color: #eee; border: 1px solid #333; gridline-color: #2c2c2c;
}
QHeaderView::section { background-color: #2a2a2a; color: #ccc; border: 0; padding: 4px; }
QPushButton {
    background-color: #2a2a2a; color: #eee; border: 1px solid #444;
    border-radius: 3px; padding: 5px 12px;
}
QPushButton:hover { background-color: #3a3a3a; border-color: #55efc4; }
"""


class CleanupPreviewDialog(QDialog):
    """Show ranked reclaim candidates + estimate; return the user's choice."""

    def __init__(
        self,
        result: ReleaseResult,
        *,
        title: str = "Cleanup Preview",
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.choice = CHOICE_CANCEL
        self.setWindowTitle(title)
        self.setStyleSheet(_DIALOG_STYLE)
        self.setMinimumSize(560, 420)

        candidates = list(result.preview_candidates)
        total_estimate = sum(max(c.estimated_reclaim_gb, 0.0) for c in candidates)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(14, 14, 14, 14)
        layout.setSpacing(10)

        headline = QLabel(
            f"{len(candidates)} candidate process(es) — estimated reclaim "
            f"~{total_estimate:.2f} GB"
        )
        headline.setObjectName("headline")
        headline.setWordWrap(True)
        layout.addWidget(headline)

        hint = QLabel(
            "This is a preview. Nothing has been changed yet. "
            "Run now applies your current profile; Run forced bypasses the pressure threshold."
        )
        hint.setWordWrap(True)
        layout.addWidget(hint)

        table = QTableWidget(len(candidates), 3, self)
        table.setHorizontalHeaderLabels(["Process", "RSS (GB)", "Est. reclaim (GB)"])
        table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        table.setSelectionMode(QAbstractItemView.SelectionMode.NoSelection)
        v_header = table.verticalHeader()
        if v_header is not None:
            v_header.setVisible(False)
        header = table.horizontalHeader()
        if header is not None:
            header.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
            header.setSectionResizeMode(1, QHeaderView.ResizeMode.ResizeToContents)
            header.setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)
        for row, candidate in enumerate(candidates):
            est = max(candidate.estimated_reclaim_gb, 0.0)
            table.setItem(row, 0, QTableWidgetItem(candidate.name or f"pid {candidate.pid}"))
            table.setItem(row, 1, QTableWidgetItem(f"{candidate.rss_gb:.2f}"))
            table.setItem(row, 2, QTableWidgetItem(f"{est:.2f}"))
        layout.addWidget(table, 1)

        btn_row = QHBoxLayout()
        cancel_btn = QPushButton("Cancel")
        cancel_btn.clicked.connect(self.reject)
        btn_row.addWidget(cancel_btn)
        btn_row.addStretch(1)
        force_btn = QPushButton("Run forced")
        force_btn.clicked.connect(self._choose_force)
        btn_row.addWidget(force_btn)
        run_btn = QPushButton("Run now")
        run_btn.setDefault(True)
        run_btn.clicked.connect(self._choose_run)
        btn_row.addWidget(run_btn)
        layout.addLayout(btn_row)

    def _choose_run(self) -> None:
        self.choice = CHOICE_RUN
        self.accept()

    def _choose_force(self) -> None:
        self.choice = CHOICE_FORCE
        self.accept()
