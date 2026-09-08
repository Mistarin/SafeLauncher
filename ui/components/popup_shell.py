"""Shared popup primitives for SafeLauncher.

The popup shell owns window chrome and presentation only.  Feature dialogs
remain responsible for their domain state and asynchronous work.
"""

from __future__ import annotations

from typing import Callable, Optional

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtWidgets import (
    QDialog,
    QFrame,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QProgressBar,
    QVBoxLayout,
    QWidget,
)

from ui.components.sidebar import DialogTitleBar
from ui.theme import (
    ACCENT_PRIMARY,
    BG_APP,
    SURFACE,
    SURFACE_ELEVATED,
    TEXT_MUTED,
    TEXT_PRIMARY,
    TEXT_SECONDARY,
    SEMANTIC_ERROR,
    SEMANTIC_SUCCESS,
    SEMANTIC_WARNING,
)


POPUP_STYLE = f"""
QDialog#safeLauncherPopup {{
    background: {BG_APP};
    color: {TEXT_PRIMARY};
}}
QFrame#popupBody {{
    background: {BG_APP};
    border: none;
}}
QFrame#popupSection {{
    background: {SURFACE};
    border: none;
}}
QLabel#popupTitle {{
    color: {TEXT_PRIMARY};
    font-size: 15px;
    font-weight: 700;
}}
QLabel#popupSubtitle, QLabel#popupHint {{
    color: {TEXT_SECONDARY};
    font-size: 12px;
}}
QLineEdit#popupInput {{
    background: {SURFACE};
    color: {TEXT_PRIMARY};
    border: none;
    border-radius: 5px;
    padding: 8px 10px;
}}
QLineEdit#popupInput:focus {{
    background: {SURFACE_ELEVATED};
    border: 1px solid {ACCENT_PRIMARY};
}}
QPushButton#popupPrimary, QPushButton#popupSecondary,
QPushButton#popupDestructive {{
    min-height: 30px;
    border: none;
    border-radius: 5px;
    padding: 5px 14px;
    font-size: 12px;
    font-weight: 600;
}}
QPushButton#popupPrimary {{
    background: {ACCENT_PRIMARY};
    color: #ffffff;
}}
QPushButton#popupPrimary:hover {{ background: #55ACED; }}
QPushButton#popupSecondary {{
    background: {SURFACE_ELEVATED};
    color: {TEXT_PRIMARY};
}}
QPushButton#popupSecondary:hover {{ background: #29292f; }}
QPushButton#popupDestructive {{
    background: transparent;
    color: {SEMANTIC_ERROR};
}}
QPushButton#popupDestructive:hover {{ background: rgba(240, 93, 108, 0.14); }}
QProgressBar#popupProgress {{
    background: {SURFACE};
    border: none;
    border-radius: 3px;
    height: 6px;
    text-visible: false;
}}
QProgressBar#popupProgress::chunk {{
    background: {ACCENT_PRIMARY};
    border-radius: 3px;
}}
"""


class PopupDialog(QDialog):
    """Common custom-chrome dialog base.

    Existing dialogs can use ``popup_layout()`` to retain their public API
    while adopting the same shell, colors, sizing, and close behavior.
    """

    def __init__(self, title: str = "", parent: Optional[QWidget] = None):
        super().__init__(parent)
        self.setObjectName("safeLauncherPopup")
        self.setWindowFlags(Qt.WindowType.FramelessWindowHint | Qt.WindowType.Dialog)
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose, True)
        self.setStyleSheet(POPUP_STYLE)
        self._popup_root = QVBoxLayout(self)
        self._popup_root.setContentsMargins(0, 0, 0, 0)
        self._popup_root.setSpacing(0)
        self.title_bar = DialogTitleBar(self, title)
        self._popup_root.addWidget(self.title_bar)

    def popup_layout(self, *, margins=(20, 16, 20, 16), spacing=12) -> QVBoxLayout:
        body = QFrame(self)
        body.setObjectName("popupBody")
        layout = QVBoxLayout(body)
        layout.setContentsMargins(*margins)
        layout.setSpacing(spacing)
        self._popup_root.addWidget(body, 1)
        return layout

    def add_footer(self, layout: QVBoxLayout, *, accept_text="Save", cancel_text="Cancel",
                   accept: Optional[Callable[[], None]] = None,
                   reject: Optional[Callable[[], None]] = None) -> QHBoxLayout:
        footer = QHBoxLayout()
        footer.setSpacing(8)
        footer.addStretch()
        cancel = QPushButton(cancel_text)
        cancel.setObjectName("popupSecondary")
        cancel.clicked.connect(reject or self.reject)
        footer.addWidget(cancel)
        if accept is not None:
            confirm = QPushButton(accept_text)
            confirm.setObjectName("popupPrimary")
            confirm.clicked.connect(accept)
            footer.addWidget(confirm)
            confirm.setDefault(True)
        layout.addLayout(footer)
        return footer


class MessageDialog(PopupDialog):
    """Themed replacement for application-owned information/warning/error prompts."""

    def __init__(self, title: str, message: str, *, level: str = "info", parent=None,
                 details: str = ""):
        super().__init__(title, parent)
        body = self.popup_layout(margins=(24, 20, 24, 20), spacing=14)
        heading = QLabel(title)
        heading.setObjectName("popupTitle")
        body.addWidget(heading)
        text = QLabel(message)
        text.setObjectName("popupHint")
        text.setWordWrap(True)
        body.addWidget(text)
        if details:
            detail_label = QLabel(details)
            detail_label.setObjectName("popupHint")
            detail_label.setWordWrap(True)
            body.addWidget(detail_label)
        body.addStretch()
        self.add_footer(body, accept_text="Close", accept=self.accept)

        color = {
            "success": SEMANTIC_SUCCESS,
            "warning": SEMANTIC_WARNING,
            "error": SEMANTIC_ERROR,
        }.get(level, ACCENT_PRIMARY)
        heading.setStyleSheet(f"color: {color};")

    @classmethod
    def information(cls, parent, title: str, message: str, details: str = "") -> int:
        return cls(title, message, parent=parent, details=details).exec()

    @classmethod
    def warning(cls, parent, title: str, message: str, details: str = "") -> int:
        return cls(title, message, level="warning", parent=parent, details=details).exec()

    @classmethod
    def critical(cls, parent, title: str, message: str, details: str = "") -> int:
        return cls(title, message, level="error", parent=parent, details=details).exec()


class ConfirmDialog(PopupDialog):
    """Themed yes/no confirmation with a stable boolean result."""

    def __init__(self, title: str, message: str, *, parent=None,
                 confirm_text="Confirm", destructive=False):
        super().__init__(title, parent)
        body = self.popup_layout(margins=(24, 20, 24, 20), spacing=14)
        heading = QLabel(title)
        heading.setObjectName("popupTitle")
        body.addWidget(heading)
        text = QLabel(message)
        text.setObjectName("popupHint")
        text.setWordWrap(True)
        body.addWidget(text)
        body.addStretch()
        footer = QHBoxLayout()
        footer.addStretch()
        cancel = QPushButton("Cancel")
        cancel.setObjectName("popupSecondary")
        cancel.clicked.connect(self.reject)
        footer.addWidget(cancel)
        confirm = QPushButton(confirm_text)
        confirm.setObjectName("popupDestructive" if destructive else "popupPrimary")
        confirm.clicked.connect(self.accept)
        confirm.setDefault(True)
        footer.addWidget(confirm)
        body.addLayout(footer)

    @classmethod
    def ask(cls, parent, title: str, message: str, *, confirm_text="Confirm", destructive=False) -> bool:
        return cls(title, message, parent=parent, confirm_text=confirm_text,
                   destructive=destructive).exec() == QDialog.DialogCode.Accepted


class InputDialog(PopupDialog):
    """Small themed text input dialog for app-owned input flows."""

    def __init__(self, title: str, label: str, value: str = "", *, parent=None):
        super().__init__(title, parent)
        self.value = ""
        body = self.popup_layout(margins=(24, 20, 24, 20), spacing=12)
        heading = QLabel(title)
        heading.setObjectName("popupTitle")
        body.addWidget(heading)
        prompt = QLabel(label)
        prompt.setObjectName("popupHint")
        body.addWidget(prompt)
        self.input = QLineEdit(value)
        self.input.setObjectName("popupInput")
        self.input.returnPressed.connect(self._accept)
        body.addWidget(self.input)
        self.add_footer(body, accept_text="OK", accept=self._accept)
        self.input.setFocus()

    def _accept(self):
        self.value = self.input.text()
        self.accept()

    @classmethod
    def get_text(cls, parent, title: str, label: str, value: str = "") -> tuple[str, bool]:
        dialog = cls(title, label, value, parent=parent)
        accepted = dialog.exec() == QDialog.DialogCode.Accepted
        return dialog.value, accepted


class OperationDialog(PopupDialog):
    """Non-cancelable/cancelable progress surface for supervised work."""

    cancel_requested = pyqtSignal()

    def __init__(self, title: str, message: str, *, parent=None, cancellable=True):
        super().__init__(title, parent)
        self.setModal(True)
        body = self.popup_layout(margins=(24, 20, 24, 20), spacing=14)
        heading = QLabel(title)
        heading.setObjectName("popupTitle")
        body.addWidget(heading)
        self.message_label = QLabel(message)
        self.message_label.setObjectName("popupHint")
        self.message_label.setWordWrap(True)
        body.addWidget(self.message_label)
        self.progress = QProgressBar()
        self.progress.setObjectName("popupProgress")
        self.progress.setRange(0, 0)
        body.addWidget(self.progress)
        body.addStretch()
        footer = QHBoxLayout()
        footer.addStretch()
        self.cancel_button = QPushButton("Cancel")
        self.cancel_button.setObjectName("popupSecondary")
        self.cancel_button.setVisible(cancellable)
        self.cancel_button.clicked.connect(self.cancel_requested)
        self.cancel_button.clicked.connect(self.reject)
        footer.addWidget(self.cancel_button)
        body.addLayout(footer)

    def set_message(self, message: str):
        self.message_label.setText(message)

    def set_progress(self, current: int, total: int):
        self.progress.setRange(0, max(1, total))
        self.progress.setValue(current)

    def finish(self, success: bool, message: str):
        self.set_message(message)
        self.progress.setVisible(False)
        self.cancel_button.setVisible(False)
        self.setWindowTitle("Completed" if success else "Operation failed")

