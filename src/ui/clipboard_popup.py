"""Popup window for clipboard history selection and formatting."""

from __future__ import annotations

from PyQt6.QtCore import QEvent, Qt
from PyQt6.QtGui import QClipboard, QFont, QMouseEvent
from PyQt6.QtWidgets import (
    QApplication,
    QComboBox,
    QLineEdit,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QStyle,
    QStyleOptionViewItem,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from services.clipboard_history_service import ClipboardHistoryService


class ClipboardHistoryPopup(QWidget):
    """Floating clipboard history picker and formatter."""

    def __init__(
        self,
        manager: ClipboardHistoryService,
        clipboard: QClipboard,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._manager = manager
        self._clipboard = clipboard
        self._building = False
        self._dragging = False
        self._drag_offset = None
        self._checkbox_press = False
        self.setWindowFlags(Qt.WindowType.Tool | Qt.WindowType.WindowStaysOnTopHint | Qt.WindowType.FramelessWindowHint)
        self.setWindowTitle("Clipboard History")
        self.setMinimumSize(520, 420)
        self.setStyleSheet(
            "QWidget { background:#111; color:#ddd; }"
            "QListWidget, QTextEdit, QComboBox, QLineEdit { background:#151515; color:#ddd; border:1px solid #333; }"
            "QListWidget::item:selected { background:#1f3b35; }"
            "QListWidget::indicator { width:16px; height:16px; border:1px solid #666; background:#1a1a1a; }"
            "QListWidget::indicator:checked { border:1px solid #8fffe1; background:#55efc4; }"
            "QPushButton { background:#2a2a2a; color:#ddd; border:1px solid #333; padding:4px 8px; border-radius:3px; }"
            "QPushButton:hover { border-color:#55efc4; }"
        )
        layout = QVBoxLayout(self)
        self.history_list = QListWidget(self)
        self.history_list.setFont(QFont("Segoe UI", 9))
        self.history_list.itemChanged.connect(lambda _item: self._update_preview())
        self.history_list.itemClicked.connect(self._copy_clicked_item)
        self.history_list.viewport().installEventFilter(self)
        layout.addWidget(self.history_list)
        self.template_combo = QComboBox(self)
        self.template_combo.currentTextChanged.connect(self._on_template_changed)
        layout.addWidget(self.template_combo)
        self.template_name = QLineEdit(self)
        self.template_name.setPlaceholderText("Custom template name")
        layout.addWidget(self.template_name)
        self.template_body = QTextEdit(self)
        self.template_body.setFixedHeight(72)
        self.template_body.textChanged.connect(self._update_preview)
        layout.addWidget(self.template_body)
        self.preview = QTextEdit(self)
        self.preview.setReadOnly(True)
        self.preview.setFixedHeight(120)
        layout.addWidget(self.preview)
        for text, handler in (
            ("Select All", self.select_all_history),
            ("Clear Checks", self.clear_history_checks),
            ("Copy Combined", self.copy_checked_history),
            ("Save Template", self.save_template),
            ("Delete Template", self.delete_template),
            ("Clear History", self.clear_history),
            ("Close", self.hide),
        ):
            button = QPushButton(text, self)
            button.clicked.connect(handler)
            layout.addWidget(button)
        self.refresh()

    def refresh(self) -> None:
        """Refresh history and template data."""
        self._building = True
        self.history_list.clear()
        for text in self._manager.history_items():
            item = QListWidgetItem(ClipboardHistoryService.preview_text(text), self.history_list)
            item.setData(Qt.ItemDataRole.UserRole, text)
            item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            item.setCheckState(Qt.CheckState.Unchecked)
        current = self.template_combo.currentText() or ClipboardHistoryService.default_template_name()
        self.template_combo.clear()
        self.template_combo.addItems(self._manager.template_names())
        self.template_combo.setCurrentText(current if current in self._manager.template_names() else ClipboardHistoryService.default_template_name())
        self._load_template_editor(self.template_combo.currentText())
        self._building = False
        self._update_preview()

    def select_all_history(self) -> None:
        for row in range(self.history_list.count()):
            self.history_list.item(row).setCheckState(Qt.CheckState.Checked)

    def clear_history_checks(self) -> None:
        for row in range(self.history_list.count()):
            self.history_list.item(row).setCheckState(Qt.CheckState.Unchecked)

    def copy_checked_history(self) -> None:
        texts = self._checked_history_texts()
        if not texts:
            return
        self._manager.copy_combined_items(texts, self._clipboard, self.template_combo.currentText(), self.template_body.toPlainText())
        self.refresh()

    def save_template(self) -> None:
        name = self.template_name.text()
        body = self.template_body.toPlainText()
        if not self._manager.save_template(name, body):
            return
        self.refresh()
        self.template_combo.setCurrentText(" ".join(name.split()))

    def delete_template(self) -> None:
        name = self.template_combo.currentText()
        if not self._manager.is_custom_template(name):
            return
        self._manager.delete_template(name)
        self.refresh()

    def clear_history(self) -> None:
        self._manager.clear_history()
        self.refresh()

    def _copy_clicked_item(self, item: QListWidgetItem) -> None:
        if self._checkbox_press:
            self._checkbox_press = False
            return
        self._manager.copy_history_item(str(item.data(Qt.ItemDataRole.UserRole)), self._clipboard)
        self._update_preview()

    def _checked_history_texts(self) -> list[str]:
        texts: list[str] = []
        for row in range(self.history_list.count()):
            item = self.history_list.item(row)
            if item.checkState() == Qt.CheckState.Checked:
                texts.append(str(item.data(Qt.ItemDataRole.UserRole)))
        return texts

    def _on_template_changed(self, name: str) -> None:
        if self._building:
            return
        self._load_template_editor(name)
        self._update_preview()

    def _load_template_editor(self, name: str) -> None:
        self.template_name.setText("" if not self._manager.is_custom_template(name) else name)
        self.template_body.setPlainText(self._manager.template_body(name))

    def _update_preview(self) -> None:
        texts = self._checked_history_texts()
        if texts:
            preview = self._manager.render_items(texts, self.template_combo.currentText(), self.template_body.toPlainText())
            self.preview.setPlainText(preview or "")
            return
        item = self.history_list.currentItem()
        fallback = "" if item is None else str(item.data(Qt.ItemDataRole.UserRole))
        if fallback:
            self.preview.setPlainText(fallback)
        else:
            self.preview.setPlainText(f"Check items, choose a template, or define one with {ClipboardHistoryService.template_help()}.")

    def mousePressEvent(self, event: QMouseEvent | None) -> None:  # pylint: disable=invalid-name
        """Start dragging the popup."""
        if event is not None and event.button() == Qt.MouseButton.LeftButton:
            self._dragging = True
            self._drag_offset = event.globalPosition().toPoint() - self.frameGeometry().topLeft()
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event: QMouseEvent | None) -> None:  # pylint: disable=invalid-name
        """Move the popup while dragging."""
        if self._dragging and event is not None and self._drag_offset is not None:
            self.move(event.globalPosition().toPoint() - self._drag_offset)
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event: QMouseEvent | None) -> None:  # pylint: disable=invalid-name
        """Stop dragging the popup."""
        self._dragging = False
        self._drag_offset = None
        super().mouseReleaseEvent(event)

    def focusOutEvent(self, event) -> None:  # pylint: disable=invalid-name
        """Close the popup when it loses focus."""
        self.hide()
        super().focusOutEvent(event)

    def showEvent(self, event) -> None:  # pylint: disable=invalid-name
        """Watch for outside clicks while the popup is open."""
        app = QApplication.instance()
        if app is not None:
            app.installEventFilter(self)
        super().showEvent(event)

    def hideEvent(self, event) -> None:  # pylint: disable=invalid-name
        """Stop watching for outside clicks when hidden."""
        app = QApplication.instance()
        if app is not None:
            app.removeEventFilter(self)
        super().hideEvent(event)

    def eventFilter(self, watched, event) -> bool:
        """Close the popup when the user clicks anywhere outside it."""
        if watched is self.history_list.viewport() and event is not None:
            if event.type() == QEvent.Type.MouseButtonPress:
                item = self.history_list.itemAt(event.position().toPoint())
                if item is not None:
                    self._checkbox_press = self._is_checkbox_hit(
                        item,
                        event.position().toPoint(),
                    )
                else:
                    self._checkbox_press = False
            elif event.type() == QEvent.Type.MouseButtonRelease and not self._checkbox_press:
                self._checkbox_press = False
        if self.isVisible() and event is not None and event.type() == QEvent.Type.MouseButtonPress:
            global_pos = event.globalPosition().toPoint()
            if not self.frameGeometry().contains(global_pos):
                self.hide()
        return super().eventFilter(watched, event)

    def _is_checkbox_hit(self, item: QListWidgetItem, point) -> bool:
        """Return True when the press lands on the checkbox indicator itself."""
        option = QStyleOptionViewItem()
        option.rect = self.history_list.visualItemRect(item)
        option.state = QStyle.StateFlag.State_Enabled | QStyle.StateFlag.State_Active
        option.features = QStyleOptionViewItem.ViewItemFeature.HasCheckIndicator
        option.checkState = item.checkState()
        indicator = self.history_list.style().subElementRect(
            QStyle.SubElement.SE_ItemViewItemCheckIndicator,
            option,
            self.history_list,
        )
        return indicator.contains(point)
