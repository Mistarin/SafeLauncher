"""Dialog for selecting one avatar from the central developer catalog."""

from __future__ import annotations

from typing import Any

from PyQt6.QtCore import QTimer, Qt, pyqtSignal
from PyQt6.QtGui import QPixmap
from PyQt6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
)


class ProfileAvatarCatalogDialog(QDialog):
    """A bounded, keyboard-accessible table of cloud-backed avatars."""

    visible_avatar_ids = pyqtSignal(object)

    def __init__(self, catalog: list[dict[str, Any]], current_id: str = "", parent=None):
        super().__init__(parent)
        self.setWindowTitle("Choose profile picture")
        self.resize(620, 560)
        self._catalog = list(catalog)
        self._current_id = str(current_id or "")
        self._rows_by_id: dict[str, int] = {}
        self._preview_labels: dict[str, QLabel] = {}

        root = QVBoxLayout(self)
        intro = QLabel("Choose a picture from the SafeLauncher collection.")
        intro.setWordWrap(True)
        root.addWidget(intro)
        self.search = QLineEdit()
        self.search.setPlaceholderText("Search profile pictures…")
        self.search.textChanged.connect(self._filter_rows)
        root.addWidget(self.search)

        self.table = QTableWidget(0, 4)
        self.table.setHorizontalHeaderLabels(["Preview", "Name", "Group", "ID"])
        self.table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QTableWidget.SelectionMode.SingleSelection)
        self.table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.table.setAlternatingRowColors(False)
        self.table.verticalHeader().setVisible(False)
        self.table.horizontalHeader().setStretchLastSection(True)
        self.table.setColumnWidth(0, 86)
        self.table.setColumnWidth(1, 230)
        self.table.setColumnWidth(2, 100)
        self.table.cellDoubleClicked.connect(lambda *_: self.accept())
        self.table.itemSelectionChanged.connect(self._selection_changed)
        self.table.verticalScrollBar().valueChanged.connect(lambda *_: self._request_visible_thumbnails())
        root.addWidget(self.table, 1)

        self.buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Cancel | QDialogButtonBox.StandardButton.Ok,
        )
        self.buttons.accepted.connect(self.accept)
        self.buttons.rejected.connect(self.reject)
        self.buttons.button(QDialogButtonBox.StandardButton.Ok).setText("Select")
        root.addWidget(self.buttons)
        self._populate_rows()

    @property
    def selected_avatar_id(self) -> str:
        row = self.table.currentRow()
        item = self.table.item(row, 3) if row >= 0 else None
        return str(item.data(Qt.ItemDataRole.UserRole) or "") if item else ""

    def _populate_rows(self) -> None:
        self.table.setRowCount(0)
        self._rows_by_id.clear()
        self._preview_labels.clear()
        for avatar in self._catalog:
            row = self.table.rowCount()
            self.table.insertRow(row)
            avatar_id = str(avatar.get("id", ""))
            self._rows_by_id[avatar_id] = row
            preview = QLabel("Loading…")
            preview.setAlignment(Qt.AlignmentFlag.AlignCenter)
            preview.setFixedSize(72, 72)
            self.table.setCellWidget(row, 0, preview)
            self._preview_labels[avatar_id] = preview
            for column, value in enumerate((avatar.get("label", avatar_id), avatar.get("category", "Standard"), avatar_id), start=1):
                item = QTableWidgetItem(str(value))
                item.setData(Qt.ItemDataRole.UserRole, avatar_id)
                self.table.setItem(row, column, item)
            self.table.setRowHeight(row, 78)
        if self._current_id in self._rows_by_id:
            self.table.selectRow(self._rows_by_id[self._current_id])
        elif self.table.rowCount():
            self.table.selectRow(0)
        QTimer.singleShot(0, self._request_visible_thumbnails)

    def _filter_rows(self, text: str) -> None:
        needle = text.strip().casefold()
        for row in range(self.table.rowCount()):
            values = [self.table.item(row, column).text() for column in range(1, 4) if self.table.item(row, column)]
            self.table.setRowHidden(row, bool(needle) and needle not in " ".join(values).casefold())
        QTimer.singleShot(0, self._request_visible_thumbnails)

    def _selection_changed(self) -> None:
        self.buttons.button(QDialogButtonBox.StandardButton.Ok).setEnabled(bool(self.selected_avatar_id))

    def _request_visible_thumbnails(self) -> None:
        visible: list[str] = []
        viewport_rect = self.table.viewport().rect()
        for row, avatar_id in ((row, avatar_id) for avatar_id, row in self._rows_by_id.items()):
            if self.table.isRowHidden(row):
                continue
            rect = self.table.visualRect(self.table.model().index(row, 0))
            if rect.isValid() and rect.intersects(viewport_rect):
                visible.append(avatar_id)
        if visible:
            self.visible_avatar_ids.emit(visible)

    def set_thumbnail(self, avatar_id: str, pixmap: QPixmap) -> None:
        label = self._preview_labels.get(avatar_id)
        if label is None or pixmap.isNull():
            return
        label.setText("")
        label.setPixmap(pixmap.scaled(68, 68, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation))

