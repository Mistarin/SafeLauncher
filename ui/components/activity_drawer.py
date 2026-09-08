"""Non-modal activity drawer for background operations."""

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QFrame, QHBoxLayout, QLabel, QListWidget, QListWidgetItem, QPushButton, QVBoxLayout

from core.operation_registry import Operation


class ActivityDrawer(QFrame):
    def __init__(self, registry, parent=None):
        super().__init__(parent)
        self.registry = registry
        self.setObjectName("activityDrawer")
        self.setFixedWidth(420)
        self.setMinimumHeight(180)
        self.setMaximumHeight(430)
        self.setStyleSheet("""
            QFrame#activityDrawer {
                background: #17181D;
                border: 1px solid rgba(255, 255, 255, 0.10);
                border-radius: 12px;
            }
            QLabel { background: transparent; color: #F4F4F5; }
            QListWidget { background: transparent; border: none; }
            QListWidget::item { border: none; padding: 4px; }
            QPushButton {
                background: transparent;
                color: #A1A1AA;
                border: none;
                border-radius: 5px;
                padding: 3px 7px;
            }
            QPushButton:hover { background: rgba(255, 255, 255, 0.08); color: #FFFFFF; }
        """)

        root = QVBoxLayout(self)
        root.setContentsMargins(14, 12, 14, 12)
        root.setSpacing(8)
        header = QHBoxLayout()
        title = QLabel("Activity")
        title.setStyleSheet("font-size: 14px; font-weight: 700;")
        header.addWidget(title)
        header.addStretch()
        close_button = QPushButton("Close")
        close_button.clicked.connect(self.hide)
        header.addWidget(close_button)
        root.addLayout(header)

        self.list_widget = QListWidget()
        root.addWidget(self.list_widget)
        self.hide()

        registry.operation_added.connect(self._upsert)
        registry.operation_updated.connect(self._upsert)

    def _state_color(self, state: str) -> str:
        return {
            "running": "#3B9FE8",
            "queued": "#A1A1AA",
            "completed": "#35C98A",
            "failed": "#F05D6C",
            "cancelled": "#FF9F0A",
        }.get(state, "#A1A1AA")

    def _upsert(self, operation: Operation):
        existing = None
        for index in range(self.list_widget.count()):
            item = self.list_widget.item(index)
            if item.data(Qt.ItemDataRole.UserRole) == operation.operation_id:
                existing = item
                break

        if existing is None:
            existing = QListWidgetItem()
            existing.setData(Qt.ItemDataRole.UserRole, operation.operation_id)
            self.list_widget.addItem(existing)

        widget = self.list_widget.itemWidget(existing)
        if widget is None:
            widget = QFrame()
            layout = QHBoxLayout(widget)
            layout.setContentsMargins(2, 2, 2, 2)
            text = QVBoxLayout()
            widget.title_label = QLabel()
            widget.detail_label = QLabel()
            widget.detail_label.setStyleSheet("color: #A1A1AA; font-size: 11px;")
            text.addWidget(widget.title_label)
            text.addWidget(widget.detail_label)
            layout.addLayout(text, 1)
            widget.action_button = QPushButton()
            layout.addWidget(widget.action_button)
            self.list_widget.setItemWidget(existing, widget)

        widget.title_label.setText(operation.label)
        widget.title_label.setStyleSheet(
            f"color: {self._state_color(operation.state)}; font-weight: 600; background: transparent;"
        )
        detail = f"{operation.category} · {operation.state.title()}"
        if operation.progress is not None and operation.active:
            detail += f" · {operation.progress}%"
        if operation.error:
            detail += f" · {operation.error[:120]}"
        widget.detail_label.setText(detail)

        if operation.active and operation.cancel is not None:
            widget.action_button.setText("Cancel")
            widget.action_button.setEnabled(True)
            try:
                widget.action_button.clicked.disconnect()
            except (TypeError, RuntimeError):
                pass
            widget.action_button.clicked.connect(lambda _checked=False, oid=operation.operation_id: self.registry.cancel(oid))
        elif operation.state == "failed" and operation.retry is not None:
            widget.action_button.setText("Retry")
            widget.action_button.setEnabled(True)
            try:
                widget.action_button.clicked.disconnect()
            except (TypeError, RuntimeError):
                pass
            widget.action_button.clicked.connect(lambda _checked=False, oid=operation.operation_id: self.registry.retry_operation(oid))
        else:
            widget.action_button.setText("")
            widget.action_button.setEnabled(False)

        self.list_widget.scrollToBottom()
