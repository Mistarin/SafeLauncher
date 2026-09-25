"""Reusable chronological save-history presentation for Qt dialogs."""

from __future__ import annotations

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtWidgets import (
    QButtonGroup,
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QScrollArea,
    QSizePolicy,
    QVBoxLayout,
    QWidget,
)

from core.save_history import (
    HistoryEntry,
    history_registered_device_label,
    normalize_history_entries,
)
from ui.icons import get_icon


def _format_bytes(size_bytes: int) -> str:
    value = float(max(0, int(size_bytes or 0)))
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if value < 1024 or unit == "TB":
            return f"{int(value)} {unit}" if unit == "B" else f"{value:.1f} {unit}"
        value /= 1024
    return "0 B"


class _HistoryItemCompatibilityView:
    """Small read-only QListWidget-shaped view for older smoke/plugin callers."""

    def __init__(self, entry: HistoryEntry):
        self.entry = entry

    def text(self) -> str:
        return f"{self.entry.title}\n{self.entry.date_label} at {self.entry.time_label} · {self.entry.device_text}"

    def data(self, role):
        if role == UserRole:
            return self.entry.raw
        return None


UserRole = Qt.ItemDataRole.UserRole


class SaveHistoryTimeline(QWidget):
    """Device- and date-grouped, selectable timeline for save history."""

    entry_selected = pyqtSignal(object)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._entries: list[HistoryEntry] = []
        self._registered_device_labels: list[str] = []
        self._selected: HistoryEntry | None = None
        self._buttons: dict[str, QPushButton] = {}
        self._button_group = QButtonGroup(self)
        self._button_group.setExclusive(True)
        self._button_group.buttonClicked.connect(self._on_button_clicked)

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)
        self.scroll = QScrollArea()
        self.scroll.setAccessibleName("Cloud save versions and local backups")
        self.scroll.setAccessibleDescription(
            "Select a cloud save version or local safety backup to restore it."
        )
        self.scroll.setWidgetResizable(True)
        self.scroll.setFrameShape(QFrame.Shape.NoFrame)
        self.scroll.setStyleSheet("QScrollArea { background: transparent; border: none; }")
        self.content = QWidget()
        self.content.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Maximum)
        self.content_layout = QVBoxLayout(self.content)
        self.content_layout.setContentsMargins(4, 4, 4, 4)
        self.content_layout.setSpacing(6)
        self.scroll.setWidget(self.content)
        root.addWidget(self.scroll)

    def set_message(self, message: str) -> None:
        """Show a non-selectable loading/error/empty message."""
        self._entries = []
        self._selected = None
        self._buttons.clear()
        self._clear_content()
        label = QLabel(str(message))
        label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        label.setWordWrap(True)
        label.setStyleSheet("color: #71717A; padding: 28px 12px;")
        self.content_layout.addWidget(label)
        self.content_layout.addStretch()

    def set_entries(self, entries) -> None:
        self._entries = normalize_history_entries(entries)
        self._selected = None
        self._buttons.clear()
        self._render_entries()

    def set_devices(self, devices) -> None:
        """Set registered devices, including devices with no versions yet."""
        labels = []
        seen = set()
        for device in devices or ():
            label = history_registered_device_label(device)
            key = label.casefold()
            if key in seen:
                continue
            seen.add(key)
            labels.append(label)
        self._registered_device_labels = labels
        if self._entries or self._registered_device_labels:
            self._render_entries()

    def _clear_content(self) -> None:
        while self.content_layout.count():
            item = self.content_layout.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()

    def _render_entries(self) -> None:
        self._clear_content()

        if not self._entries and not self._registered_device_labels:
            empty = QLabel("No saved versions or local safety backups found.")
            empty.setAlignment(Qt.AlignmentFlag.AlignCenter)
            empty.setStyleSheet("color: #71717A; padding: 28px 12px;")
            self.content_layout.addWidget(empty)
            self.content_layout.addStretch()
            return

        # Keep the overall history model chronological, but present it in
        # device sections so it is immediately clear which machine produced
        # each retained cloud version. Registered devices are included even
        # when they have no retained version for this game.
        device_groups: dict[str, tuple[str, list[HistoryEntry]]] = {}
        for label in self._registered_device_labels:
            device_groups[label.casefold()] = (label, [])
        for entry in self._entries:
            key = entry.device_label.casefold()
            if key not in device_groups:
                device_groups[key] = (entry.device_label, [])
            device_groups[key][1].append(entry)

        registered_keys = {label.casefold() for label in self._registered_device_labels}
        extra_groups = sorted(
            (
                (key, label, entries)
                for key, (label, entries) in device_groups.items()
                if key not in registered_keys
            ),
            key=lambda item: (
                0 if item[2] and item[2][0].event_at else 1,
                -item[2][0].event_at if item[2] else 0,
                item[0],
            ),
        )
        ordered_groups = [
            (label, device_groups[label.casefold()][1])
            for label in self._registered_device_labels
        ] + [(label, entries) for _key, label, entries in extra_groups]

        for device_label, device_entries in ordered_groups:
            self.content_layout.addWidget(self._device_divider(device_label))
            if not device_entries:
                empty = QLabel("No cloud versions from this device.")
                empty.setStyleSheet("color: #71717A; padding: 4px 12px 8px;")
                empty.setAccessibleName(f"No cloud versions from {device_label}")
                self.content_layout.addWidget(empty)
                continue
            current_date = None
            for entry in device_entries:
                if entry.date_key != current_date:
                    current_date = entry.date_key
                    self.content_layout.addWidget(self._date_divider(entry.date_label))
                button = self._entry_button(entry)
                self._buttons[entry.identity] = button
                self._button_group.addButton(button)
                self.content_layout.addWidget(button)
        self.content_layout.addStretch()

        # Select the active/current entry first; otherwise the newest entry.
        if self._entries:
            selected = next((entry for entry in self._entries if entry.is_active), self._entries[0])
            self._select(selected, emit=False)

    def entries(self) -> tuple[HistoryEntry, ...]:
        return tuple(self._entries)

    def selected_entry(self) -> HistoryEntry | None:
        return self._selected

    # Compatibility read methods intentionally do not expose a second list
    # or selection owner.  They let older smoke tests/plugins inspect the
    # timeline while all real selection remains owned by this widget.
    def count(self) -> int:
        return len(self._entries)

    def item(self, index: int):
        try:
            return _HistoryItemCompatibilityView(self._entries[int(index)])
        except (IndexError, TypeError, ValueError):
            return None

    def currentItem(self):
        return _HistoryItemCompatibilityView(self._selected) if self._selected else None

    def select_identity(self, identity: str) -> None:
        entry = next((item for item in self._entries if item.identity == identity), None)
        if entry is not None:
            self._select(entry, emit=False)

    def _date_divider(self, label: str) -> QWidget:
        frame = QWidget()
        layout = QHBoxLayout(frame)
        layout.setContentsMargins(2, 10, 2, 2)
        layout.setSpacing(8)
        line_left = QFrame()
        line_left.setFrameShape(QFrame.Shape.HLine)
        line_left.setStyleSheet("color: #2D313A;")
        line_right = QFrame()
        line_right.setFrameShape(QFrame.Shape.HLine)
        line_right.setStyleSheet("color: #2D313A;")
        date = QLabel(label)
        date.setStyleSheet("color: #8E8E93; font-size: 10px; font-weight: 700; letter-spacing: 0.4px;")
        layout.addWidget(line_left, 1)
        layout.addWidget(date)
        layout.addWidget(line_right, 1)
        return frame

    def _device_divider(self, label: str) -> QWidget:
        frame = QWidget()
        frame.setAccessibleName(f"Cloud save device: {label}")
        layout = QHBoxLayout(frame)
        layout.setContentsMargins(2, 10, 2, 0)
        layout.setSpacing(8)
        device = QLabel(f"Device: {label}")
        device.setStyleSheet(
            "color: #D4D4D8; font-size: 11px; font-weight: 700; "
            "letter-spacing: 0.2px;"
        )
        line = QFrame()
        line.setFrameShape(QFrame.Shape.HLine)
        line.setStyleSheet("color: #2D313A;")
        layout.addWidget(device)
        layout.addWidget(line, 1)
        return frame

    def _entry_button(self, entry: HistoryEntry) -> QPushButton:
        source_color = "#3B9FE8" if entry.source == "cloud" else "#A1A1AA"
        status = " · Current on this PC" if entry.is_active else ""
        conflict = " · Conflict" if entry.has_conflict else ""
        details = f"{entry.time_label}  ·  {_format_bytes(entry.size_bytes)}  ·  {entry.device_text}"
        button = QPushButton(f"{entry.version_label}{status}{conflict}\n{details}")
        button.setCheckable(True)
        button.setCursor(Qt.CursorShape.PointingHandCursor)
        button.setMinimumHeight(52)
        button.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        button.setIcon(get_icon("ph.cloud-bold" if entry.source == "cloud" else "ph.archive-bold", source_color))
        button.setStyleSheet(f"""
            QPushButton {{
                text-align: left;
                padding: 8px 12px;
                background: #181A20;
                color: #E5E7EB;
                border: 1px solid #252A33;
                border-radius: 7px;
                font-size: 11px;
            }}
            QPushButton:hover {{ background: #202633; border-color: #3B9FE8; }}
            QPushButton:checked {{ background: #1E293B; border-color: #3B9FE8; color: #FFFFFF; }}
        """)
        button.setToolTip(
            f"{entry.title}\n{entry.date_label} at {entry.time_label}\n"
            f"{entry.device_text}\nSize: {_format_bytes(entry.size_bytes)}"
        )
        button.setAccessibleName(entry.title)
        button.setAccessibleDescription(
            f"{entry.version_label}. {entry.date_label} at {entry.time_label}. "
            f"{entry.device_text}. Size {_format_bytes(entry.size_bytes)}."
        )
        button.setProperty("historyIdentity", entry.identity)
        return button

    def _on_button_clicked(self, button: QPushButton) -> None:
        identity = str(button.property("historyIdentity") or "")
        entry = next((item for item in self._entries if item.identity == identity), None)
        if entry is not None:
            self._selected = entry
            self.entry_selected.emit(entry)

    def _select(self, entry: HistoryEntry, *, emit: bool) -> None:
        button = self._buttons.get(entry.identity)
        if button is None:
            return
        button.setChecked(True)
        self._selected = entry
        if emit:
            self.entry_selected.emit(entry)


__all__ = ["SaveHistoryTimeline"]
