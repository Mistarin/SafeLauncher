"""Dialog for selecting one avatar from the central developer catalog."""

from __future__ import annotations

from typing import Any

from PyQt6.QtCore import QTimer, QSize, Qt, pyqtSignal
from PyQt6.QtGui import QIcon, QPixmap
from PyQt6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QToolButton,
    QVBoxLayout,
)
from ui.components.popup_shell import PopupDialog
from ui.theme import BORDER, POPUP_SURFACE, POPUP_SURFACE_ACTIVE


class ProfileAvatarCatalogDialog(PopupDialog):
    """A custom SafeLauncher popup for choosing a cloud-backed avatar."""

    _GRID_COLUMNS = 6
    _TILE_SIZE = 90

    visible_avatar_ids = pyqtSignal(object)
    catalog_avatar_ids = pyqtSignal(object)

    def __init__(self, catalog: list[dict[str, Any]], current_id: str = "", parent=None):
        super().__init__("Choose profile picture", parent)
        # PopupDialog normally owns deletion after close. This dialog is run
        # through exec() and the profile page reads the result immediately
        # afterwards, so the caller retains ownership and deletes it safely
        # after the modal loop returns.
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose, False)
        self.setMinimumSize(620, 560)
        self.resize(620, 560)
        self._catalog = list(catalog)
        self._catalog_by_id = {
            str(avatar.get("id", "")): avatar
            for avatar in self._catalog
            if str(avatar.get("id", ""))
        }
        self._current_id = str(current_id or "")
        self._rows_by_id: dict[str, int] = {}
        self._tiles_by_id: dict[str, QToolButton] = {}
        self._row_avatar_ids: list[list[str]] = []
        self._selected_avatar_id = ""

        self.setStyleSheet(
            f"""
            QDialog#safeLauncherPopup QTableWidget#avatarGrid {{
                background: {POPUP_SURFACE};
                border: none;
                gridline-color: transparent;
            }}
            QDialog#safeLauncherPopup QToolButton#avatarTile {{
                background: {POPUP_SURFACE};
                border: 1px solid transparent;
                border-radius: 8px;
                padding: 4px;
            }}
            QDialog#safeLauncherPopup QToolButton#avatarTile:hover {{
                background: {POPUP_SURFACE_ACTIVE};
                border-color: {BORDER};
            }}
            QDialog#safeLauncherPopup QToolButton#avatarTile:checked {{
                background: {POPUP_SURFACE_ACTIVE};
                border: 2px solid #3B9FE8;
            }}
            QDialog#safeLauncherPopup QToolButton#avatarTile:focus {{
                border: 2px solid #3B9FE8;
            }}
            """
        )

        root = self.popup_layout(margins=(20, 16, 20, 16), spacing=12)
        intro = QLabel("Choose a picture from the SafeLauncher collection.")
        intro.setObjectName("popupSubtitle")
        intro.setWordWrap(True)
        root.addWidget(intro)
        self.search = QLineEdit()
        self.search.setObjectName("popupInput")
        self.search.setPlaceholderText("Search profile pictures…")
        self.search.textChanged.connect(self._filter_rows)
        root.addWidget(self.search)

        self.table = QTableWidget(0, self._GRID_COLUMNS)
        self.table.setObjectName("avatarGrid")
        self.table.setSelectionMode(QTableWidget.SelectionMode.NoSelection)
        self.table.setEditTriggers(QTableWidget.EditTrigger.NoEditTriggers)
        self.table.setAlternatingRowColors(False)
        self.table.verticalHeader().setVisible(False)
        self.table.horizontalHeader().setVisible(False)
        self.table.horizontalHeader().setDefaultSectionSize(self._TILE_SIZE)
        self.table.verticalHeader().setDefaultSectionSize(self._TILE_SIZE)
        self.table.setIconSize(QSize(self._TILE_SIZE - 12, self._TILE_SIZE - 12))
        self.table.setShowGrid(False)
        self.table.verticalScrollBar().valueChanged.connect(lambda *_: self._request_visible_thumbnails())
        root.addWidget(self.table, 1)

        footer = QHBoxLayout()
        footer.setSpacing(8)
        footer.addStretch()
        self.btn_cancel = QPushButton("Cancel")
        self.btn_cancel.setObjectName("popupSecondary")
        self.btn_cancel.clicked.connect(self.reject)
        footer.addWidget(self.btn_cancel)
        self.btn_select = QPushButton("Select")
        self.btn_select.setObjectName("popupPrimary")
        self.btn_select.setDefault(True)
        self.btn_select.setEnabled(False)
        self.btn_select.clicked.connect(self.accept)
        footer.addWidget(self.btn_select)
        root.addLayout(footer)
        self._populate_rows()

    @property
    def selected_avatar_id(self) -> str:
        return self._selected_avatar_id

    def _populate_rows(self) -> None:
        self.table.setRowCount(0)
        self._rows_by_id.clear()
        self._tiles_by_id.clear()
        self._row_avatar_ids.clear()
        self._selected_avatar_id = ""
        for index, avatar in enumerate(self._catalog):
            row = index // self._GRID_COLUMNS
            column = index % self._GRID_COLUMNS
            if column == 0:
                self.table.insertRow(row)
                self._row_avatar_ids.append([])
            avatar_id = str(avatar.get("id", ""))
            self._rows_by_id[avatar_id] = row
            self._row_avatar_ids[row].append(avatar_id)
            item = QTableWidgetItem()
            item.setData(Qt.ItemDataRole.UserRole, avatar_id)
            self.table.setItem(row, column, item)
            tile = QToolButton()
            tile.setObjectName("avatarTile")
            tile.setCheckable(True)
            tile.setAutoRaise(True)
            tile.setFixedSize(self._TILE_SIZE, self._TILE_SIZE)
            tile.setIconSize(QSize(self._TILE_SIZE - 12, self._TILE_SIZE - 12))
            label = str(avatar.get("label", avatar_id) or avatar_id).strip()
            tile.setAccessibleName(f"Profile picture {label}")
            tile.setToolTip(label)
            tile.clicked.connect(
                lambda _checked=False, selected_id=avatar_id: self._select_avatar(selected_id)
            )
            self.table.setCellWidget(row, column, tile)
            self._tiles_by_id[avatar_id] = tile
            self.table.setRowHeight(row, self._TILE_SIZE)
        for column in range(self._GRID_COLUMNS):
            self.table.setColumnWidth(column, self._TILE_SIZE)
        if self._current_id in self._rows_by_id:
            self._select_avatar(self._current_id)
        elif self._rows_by_id:
            self._select_avatar(next(iter(self._rows_by_id)))
        QTimer.singleShot(0, self._request_catalog_thumbnails)
        QTimer.singleShot(0, self._request_visible_thumbnails)

    def _request_catalog_thumbnails(self) -> None:
        if self._rows_by_id:
            self.catalog_avatar_ids.emit(list(self._rows_by_id))

    def _filter_rows(self, text: str) -> None:
        needle = text.strip().casefold()
        for row, avatar_ids in enumerate(self._row_avatar_ids):
            row_visible = False
            for avatar_id in avatar_ids:
                avatar = self._catalog_by_id.get(avatar_id, {})
                searchable = " ".join(
                    str(avatar.get(key, "") or "")
                    for key in ("label", "category", "id")
                ).casefold()
                matches = not needle or needle in searchable
                tile = self._tiles_by_id.get(avatar_id)
                if tile is not None:
                    tile.setVisible(matches)
                row_visible = row_visible or matches
            self.table.setRowHidden(row, not row_visible)
        QTimer.singleShot(0, self._request_visible_thumbnails)

    def _select_avatar(self, avatar_id: str) -> None:
        if avatar_id not in self._tiles_by_id:
            return
        self._selected_avatar_id = avatar_id
        for candidate_id, tile in self._tiles_by_id.items():
            tile.setChecked(candidate_id == avatar_id)
        self.btn_select.setEnabled(True)

    def _request_visible_thumbnails(self) -> None:
        visible: list[str] = []
        viewport_rect = self.table.viewport().rect()
        for row, avatar_id in ((row, avatar_id) for avatar_id, row in self._rows_by_id.items()):
            if self.table.isRowHidden(row):
                continue
            rect = self.table.visualRect(self.table.model().index(row, 0))
            if rect.isValid() and rect.intersects(viewport_rect):
                tile = self._tiles_by_id.get(avatar_id)
                if tile is not None and tile.isVisible():
                    visible.append(avatar_id)
        if visible:
            self.visible_avatar_ids.emit(visible)

    def set_thumbnail(self, avatar_id: str, pixmap: QPixmap) -> None:
        tile = self._tiles_by_id.get(avatar_id)
        if tile is None or pixmap.isNull():
            return
        tile.setIcon(QIcon(pixmap))
