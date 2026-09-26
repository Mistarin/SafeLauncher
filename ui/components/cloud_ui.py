"""Shared presentation helpers for cloud-save workflows.

The cloud services own transport, caching, and operation semantics.  This
module keeps the user-facing vocabulary and safety prompts consistent across
the Cloud Center, Cloud Storage & Devices, Game Properties, and Save Manager dialogs.
"""

from __future__ import annotations

from typing import Any

from PyQt6.QtCore import Qt, QTimer, pyqtSignal
from PyQt6.QtWidgets import (
    QFrame,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QProgressDialog,
    QPushButton,
    QVBoxLayout,
    QWidget,
)


class CloudStatusPanel(QFrame):
    """Shared, text-readable status surface for cloud workflows.

    Every state has a textual title and explanation; the icon/color is only
    supplemental.  Dialogs can reuse the same widget while choosing their own
    retry/setup action through ``action_requested``.
    """

    action_requested = pyqtSignal()

    _STATE_SYMBOLS = {
        "loading": "…",
        "ready": "✓",
        "stale": "↻",
        "offline": "•",
        "empty": "—",
        "error": "!",
    }
    _STATE_COLORS = {
        "loading": "#A1A1AA",
        "ready": "#35C98A",
        "stale": "#F0A35B",
        "offline": "#F0A35B",
        "empty": "#A1A1AA",
        "error": "#F05D6C",
    }

    def __init__(self, parent=None, *, compact: bool = False, show_action: bool = True):
        super().__init__(parent)
        self.state = "loading"
        self._show_action = bool(show_action)
        self.setObjectName("cloudStatusPanel")
        self.setFrameShape(QFrame.Shape.StyledPanel)
        self.setStyleSheet(
            "QFrame#cloudStatusPanel { background: #18181B; border: 1px solid #202024; border-radius: 10px; }"
        )

        layout = QHBoxLayout(self)
        layout.setContentsMargins(12 if compact else 16, 10 if compact else 14,
                                  12 if compact else 16, 10 if compact else 14)
        layout.setSpacing(10)
        self.lbl_status_icon = QLabel("…")
        self.lbl_status_icon.setFixedSize(28, 28)
        self.lbl_status_icon.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(self.lbl_status_icon)

        text_layout = QVBoxLayout()
        text_layout.setSpacing(2)
        self.lbl_status = QLabel()
        self.lbl_status.setStyleSheet("font-size: 13px; font-weight: 700; color: #F4F4F5;")
        set_accessible_status(
            self.lbl_status,
            "Cloud status",
            "Current cloud connection and synchronization state.",
        )
        self.lbl_message = QLabel()
        self.lbl_message.setWordWrap(True)
        self.lbl_message.setStyleSheet("color: #A1A1AA; font-size: 11px;")
        set_accessible_status(self.lbl_message, "Cloud status details")
        text_layout.addWidget(self.lbl_status)
        text_layout.addWidget(self.lbl_message)
        layout.addLayout(text_layout, 1)

        self.btn_action = QPushButton()
        self.btn_action.setMinimumWidth(96)
        self.btn_action.setAccessibleName("Cloud status action")
        self.btn_action.clicked.connect(self.action_requested)
        layout.addWidget(self.btn_action)
        self.set_state("loading", "Checking cloud connection…", "Loading cloud data.")

    def set_state(
        self,
        state: str,
        title: str,
        message: str = "",
        *,
        action_text: str = "",
        action_enabled: bool = True,
    ) -> None:
        state = str(state or "error").casefold()
        if state not in self._STATE_SYMBOLS:
            state = "error"
        self.state = state
        color = self._STATE_COLORS[state]
        self.lbl_status_icon.setText(self._STATE_SYMBOLS[state])
        self.lbl_status_icon.setStyleSheet(
            f"color: {color}; font-size: 18px; font-weight: 700;"
        )
        self.lbl_status.setText(str(title or "Cloud status"))
        self.lbl_status.setStyleSheet(
            f"color: {color}; font-size: 13px; font-weight: 700;"
        )
        self.lbl_message.setText(str(message or ""))
        self.lbl_status_icon.setAccessibleName(f"Cloud status: {title}")
        self.lbl_status_icon.setAccessibleDescription(str(message or ""))
        self.lbl_status.setAccessibleDescription(str(message or ""))
        visible = bool(self._show_action and action_text)
        self.btn_action.setText(str(action_text or ""))
        self.btn_action.setVisible(visible)
        self.btn_action.setEnabled(bool(action_enabled))
        if visible:
            self.btn_action.setAccessibleName(str(action_text))

    def set_loading(self, message: str = "Loading cloud data…", *, action_text: str = "") -> None:
        self.set_state("loading", "Checking cloud connection…", message, action_text=action_text)

    def set_ready(self, message: str = "Cloud data is up to date.", *, stale: bool = False,
                  action_text: str = "Refresh") -> None:
        self.set_state(
            "stale" if stale else "ready",
            "Using cached cloud data" if stale else "Cloud ready",
            message,
            action_text=action_text,
        )

    def set_offline(self, message: str = "Cloud is unavailable; showing cached data.", *, action_text: str = "Retry") -> None:
        self.set_state("offline", "Offline", message, action_text=action_text)

    def set_empty(self, message: str = "No cloud data is available.", *, action_text: str = "") -> None:
        self.set_state("empty", "No cloud data", message, action_text=action_text)

    def set_error(self, message: str, *, title: str = "Cloud status unavailable", action_text: str = "Retry") -> None:
        self.set_state("error", title, message, action_text=action_text)


def set_cloud_focus_order(dialog: QWidget, *widgets: QWidget) -> tuple[QWidget, ...]:
    """Set and record an explicit, predictable tab sequence for a cloud dialog."""
    ordered = tuple(widget for widget in widgets if widget is not None)
    dialog._cloud_focus_order = ordered
    for previous, current in zip(ordered, ordered[1:]):
        dialog.setTabOrder(previous, current)
    return ordered


def set_cloud_initial_focus(dialog: QWidget, widget: QWidget) -> None:
    """Focus a safe, non-destructive control when a cloud dialog opens."""
    dialog._cloud_initial_focus = widget
    QTimer.singleShot(0, widget.setFocus)


def _entry_value(entry: Any, attribute: str, key: str, default: str = "") -> str:
    """Read a normalized history value without exposing raw backend fields."""
    value = getattr(entry, attribute, None)
    if value is None and isinstance(entry, dict):
        value = entry.get(key, default)
    return str(value if value is not None else default).strip()


def cloud_version_label(version: Any, *, source: str = "cloud") -> str:
    """Return the stable, user-facing label for a save-history entry."""
    if str(source).casefold() != "cloud":
        return "Local safety backup"
    value = str(version if version is not None else "?").strip() or "?"
    return f"Cloud save version {value}"


def cloud_version_details(entry: Any) -> str:
    """Return compact metadata suitable for a confirmation dialog."""
    label = _entry_value(entry, "version_label", "version_label", "")
    if not label:
        source = getattr(entry, "source", None)
        version = getattr(entry, "version", None)
        if isinstance(entry, dict):
            source = entry.get("source", source)
            version = entry.get("version", version)
        label = cloud_version_label(version, source=source or "cloud")
    date = _entry_value(entry, "date_label", "date_label", "Date unavailable")
    time = _entry_value(entry, "time_label", "time_label", "Time unavailable")
    device = _entry_value(entry, "device_text", "device_text", "Source unavailable")
    size = _entry_value(entry, "size_bytes", "size_bytes", "0")
    try:
        size_value = float(size)
        if size_value < 1024:
            size_text = f"{int(size_value)} B"
        elif size_value < 1024 * 1024:
            size_text = f"{size_value / 1024:.1f} KB"
        elif size_value < 1024 * 1024 * 1024:
            size_text = f"{size_value / (1024 * 1024):.1f} MB"
        else:
            size_text = f"{size_value / (1024 * 1024 * 1024):.2f} GB"
    except (TypeError, ValueError):
        size_text = "Size unavailable"
    return f"{label}\n{date} at {time}\n{device}\n{size_text}"


def confirm_restore(
    parent: QWidget,
    *,
    game_name: str,
    entry: Any = None,
    target_path: str = "",
    technical_details: str = "",
    title: str = "Restore save",
) -> bool:
    """Ask for an explicit, safe restore confirmation.

    Restore replaces local save files, so the safe action is deliberately the
    default.  The filesystem path is kept in expandable details instead of
    being part of the primary message users must scan.
    """
    message = QMessageBox(parent)
    message.setIcon(QMessageBox.Icon.Warning)
    message.setWindowTitle(title)
    message.setText(f"Restore and replace the local save for '{game_name}'?")

    details = cloud_version_details(entry) if entry is not None else "Latest cloud save version"
    message.setInformativeText(
        f"Selected save:\n{details}\n\n"
        "Your current local save will be copied to SafeLauncher safety backups before replacement."
    )
    if target_path or technical_details:
        detail_lines = ["Technical details"]
        if target_path:
            detail_lines.append(f"Target folder: {target_path}")
        if technical_details:
            detail_lines.append(str(technical_details))
        message.setDetailedText("\n".join(detail_lines))

    restore_button = message.addButton("Restore and replace", QMessageBox.ButtonRole.AcceptRole)
    cancel_button = message.addButton("Cancel", QMessageBox.ButtonRole.RejectRole)
    message.setDefaultButton(cancel_button)
    message.setEscapeButton(cancel_button)
    message.exec()
    return message.clickedButton() is restore_button


def confirm_delete(
    parent: QWidget,
    *,
    game_name: str,
    version: Any,
) -> bool:
    """Ask for a non-destructive-by-default cloud-version deletion prompt."""
    answer = QMessageBox.question(
        parent,
        "Delete cloud save version",
        f"Permanently delete cloud save version {version} for '{game_name}'?\n\n"
        "This cannot be undone.",
        QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
        QMessageBox.StandardButton.No,
    )
    return answer == QMessageBox.StandardButton.Yes


def set_accessible_status(widget, name: str, description: str = "") -> None:
    """Apply consistent screen-reader metadata to dynamic cloud status text."""
    widget.setAccessibleName(name)
    if description:
        widget.setAccessibleDescription(description)


def cloud_progress(parent: QWidget, message: str) -> QProgressDialog:
    """Create the standard modal indeterminate cloud-operation indicator."""
    progress = QProgressDialog(message, None, 0, 0, parent)
    progress.setWindowModality(Qt.WindowModality.WindowModal)
    progress.setCancelButton(None)
    progress.setMinimumDuration(0)
    progress.show()
    return progress


__all__ = [
    "CloudStatusPanel",
    "cloud_version_label",
    "cloud_version_details",
    "confirm_restore",
    "confirm_delete",
    "set_accessible_status",
    "set_cloud_focus_order",
    "set_cloud_initial_focus",
    "cloud_progress",
]
