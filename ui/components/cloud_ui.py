"""Shared presentation helpers for cloud-save workflows.

The cloud services own transport, caching, and operation semantics.  This
module keeps the user-facing vocabulary and safety prompts consistent across
the Cloud Center, Cloud Storage & Devices, Game Properties, and Save Manager dialogs.
"""

from __future__ import annotations

from typing import Any

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QMessageBox, QProgressDialog, QWidget


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
    "cloud_version_label",
    "cloud_version_details",
    "confirm_restore",
    "confirm_delete",
    "set_accessible_status",
    "cloud_progress",
]
