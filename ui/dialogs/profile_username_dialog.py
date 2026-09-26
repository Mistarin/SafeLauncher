"""Themed first-time public-profile username dialog."""

from __future__ import annotations

from PyQt6.QtWidgets import QLineEdit, QLabel

from core.profile_models import normalize_username_handle
from ui.components.popup_shell import PopupDialog
from ui.theme import SEMANTIC_ERROR, TEXT_MUTED


class ProfileUsernameDialog(PopupDialog):
    """Collect a public username without falling back to a native prompt."""

    def __init__(self, suggestion: str, parent=None):
        super().__init__("Choose profile username", parent)
        self.setMinimumWidth(480)

        layout = self.popup_layout(margins=(24, 20, 24, 20), spacing=14)

        title = QLabel("Choose your profile username")
        title.setObjectName("popupTitle")
        layout.addWidget(title)

        subtitle = QLabel(
            "This name appears in your public profile URL and friend requests. "
            "You can change it later; previous profile links keep working."
        )
        subtitle.setObjectName("popupSubtitle")
        subtitle.setWordWrap(True)
        layout.addWidget(subtitle)

        field_label = QLabel("Username")
        field_label.setObjectName("profileUsernameLabel")
        layout.addWidget(field_label)

        self.username_edit = QLineEdit()
        self.username_edit.setObjectName("popupInput")
        self.username_edit.setAccessibleName("Public profile username")
        self.username_edit.setPlaceholderText("e.g. martin_42")
        self.username_edit.setText(str(suggestion or "player"))
        self.username_edit.selectAll()
        self.username_edit.setFixedHeight(36)
        self.username_edit.textChanged.connect(self._clear_error)
        self.username_edit.returnPressed.connect(self._accept_username)
        layout.addWidget(self.username_edit)

        self.error_label = QLabel()
        self.error_label.setObjectName("profileUsernameError")
        self.error_label.setWordWrap(True)
        self.error_label.setVisible(False)
        layout.addWidget(self.error_label)

        hint = QLabel("Use 3–32 lowercase letters, numbers, dots, underscores, or hyphens.")
        hint.setObjectName("popupHint")
        hint.setWordWrap(True)
        hint.setStyleSheet(f"color: {TEXT_MUTED}; font-size: 11px;")
        layout.addWidget(hint)

        footer = self.add_footer(
            layout,
            accept_text="Continue",
            cancel_text="Cancel",
            accept=self._accept_username,
        )
        self.cancel_button = footer.itemAt(1).widget()
        self.continue_button = footer.itemAt(2).widget()
        self.cancel_button.setFixedSize(104, 34)
        self.continue_button.setFixedSize(116, 34)
        self.continue_button.setAccessibleName("Continue with profile username")
        self.continue_button.setDefault(True)
        self.username_edit.setFocus()

    def _clear_error(self) -> None:
        if self.error_label.isVisible():
            self.error_label.clear()
            self.error_label.setVisible(False)
            self.username_edit.setProperty("invalid", False)
            self.username_edit.style().unpolish(self.username_edit)
            self.username_edit.style().polish(self.username_edit)

    def _accept_username(self) -> None:
        value = self.username_edit.text().strip()
        normalized = normalize_username_handle(value)
        if not normalized:
            self._show_error(
                "Use 3–32 lowercase letters, numbers, dots, underscores, or hyphens."
            )
            return
        self.username_edit.setText(normalized)
        self.accept()

    def _show_error(self, message: str) -> None:
        self.error_label.setText(message)
        self.error_label.setStyleSheet(f"color: {SEMANTIC_ERROR}; font-size: 11px;")
        self.error_label.setVisible(True)
        self.username_edit.setProperty("invalid", True)
        self.username_edit.style().unpolish(self.username_edit)
        self.username_edit.style().polish(self.username_edit)
        self.username_edit.setFocus()

    @property
    def value(self) -> str:
        return self.username_edit.text().strip()
