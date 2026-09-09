"""Shared host for SafeLauncher's library presentations.

The grid, list, virtualized grid, and compact presentation are renderers of
one library snapshot.  This host owns their lifetime, routing, and selection
surface so MainWindow does not need to know which widget is currently active.
"""

from __future__ import annotations

from typing import Optional, Set

from PyQt6 import sip
from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtWidgets import QLabel, QStackedWidget

from core.library_controller import LibrarySnapshot
from ui.library_list import LibraryListView
from ui.components.banner_card import GameBannerWidget
from ui.components.responsive_grid import ResponsiveGridContainer
from ui.components.virtual_grid import VirtualizedGameGridView
from ui.components.compact_game_page import CompactLayoutContainer


class LibraryViewHost(QStackedWidget):
    """Own and synchronize all library renderers.

    Renderers only render.  Product actions remain callbacks supplied by the
    application shell, while snapshot, selection, and view-mode routing live
    here.  The public API intentionally has one rendering entry point.
    """

    game_selected = pyqtSignal(int)
    game_double_clicked = pyqtSignal(int)
    game_launch_requested = pyqtSignal(int)
    favorite_requested = pyqtSignal(int)
    edit_requested = pyqtSignal(int)
    properties_requested = pyqtSignal(int)
    save_manager_requested = pyqtSignal(int)
    open_folder_requested = pyqtSignal(int)
    prefix_maintenance_requested = pyqtSignal(int)
    achievements_requested = pyqtSignal(int)
    steam_page_requested = pyqtSignal(str)
    filter_changed = pyqtSignal(str)
    sort_changed = pyqtSignal(int)
    search_changed = pyqtSignal(str)
    screenshots_requested = pyqtSignal(int)
    videos_requested = pyqtSignal(int)
    settings_requested = pyqtSignal()
    add_game_requested = pyqtSignal()

    GRID = 0
    LIST = 1
    VIRTUAL_GRID = 2
    COMPACT = 3

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setStyleSheet("QStackedWidget { background: transparent; background-color: transparent; }")

        self.grid_container = ResponsiveGridContainer(self, card_width=200, spacing=15)
        self.grid_container.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.grid_container.setStyleSheet("background: transparent; background-color: transparent;")
        self.list_view = LibraryListView(self)
        self.virtual_grid = VirtualizedGameGridView(self, card_width=200, spacing=15)
        self.compact_container = CompactLayoutContainer(self)
        self.steam_container = self.compact_container

        for widget in (self.grid_container, self.list_view, self.virtual_grid, self.compact_container):
            self.addWidget(widget)

        self._connect_renderer_events()
        self.snapshot: Optional[LibrarySnapshot] = None
        self.selected_ids: set[int] = set()
        self._empty_label: Optional[QLabel] = None

    def _connect_renderer_events(self) -> None:
        self.list_view.game_clicked.connect(self.game_selected.emit)
        self.list_view.game_double_clicked.connect(self.game_double_clicked.emit)
        self.list_view.game_launch_clicked.connect(self.game_launch_requested.emit)

        self.virtual_grid.game_clicked.connect(self.game_selected.emit)
        self.virtual_grid.game_double_clicked.connect(self.game_double_clicked.emit)
        self.virtual_grid.game_launch_clicked.connect(self.game_launch_requested.emit)
        self.virtual_grid.favorite_clicked.connect(self.favorite_requested.emit)

        compact = self.compact_container
        compact.game_selected.connect(self.game_selected.emit)
        compact.game_double_clicked.connect(self.game_double_clicked.emit)
        compact.play_requested.connect(self.game_launch_requested.emit)
        compact.edit_requested.connect(self.edit_requested.emit)
        compact.properties_requested.connect(self.properties_requested.emit)
        compact.save_manager_requested.connect(self.save_manager_requested.emit)
        compact.open_folder_requested.connect(self.open_folder_requested.emit)
        compact.prefix_maintenance_requested.connect(self.prefix_maintenance_requested.emit)
        compact.favorite_toggled.connect(self.favorite_requested.emit)
        compact.achievements_requested.connect(self.achievements_requested.emit)
        compact.steam_page_requested.connect(self.steam_page_requested.emit)
        compact.filter_changed.connect(self.filter_changed.emit)
        compact.sort_changed.connect(self.sort_changed.emit)
        compact.search_changed.connect(self.search_changed.emit)
        compact.screenshots_requested.connect(self.screenshots_requested.emit)
        compact.videos_requested.connect(self.videos_requested.emit)
        compact.settings_requested.connect(self.settings_requested.emit)
        compact.add_game_requested.connect(self.add_game_requested.emit)

    def render_snapshot(
        self,
        snapshot: LibrarySnapshot,
        cache_dir: Optional[str] = None,
        selected_ids: Optional[Set[int]] = None,
    ) -> None:
        """Render one immutable snapshot into every renderer."""
        self.snapshot = snapshot
        self.selected_ids = set(selected_ids or set())
        self.list_view.set_snapshot(snapshot, cache_dir, self.selected_ids)
        self.virtual_grid.set_snapshot(snapshot, self.selected_ids)
        self.compact_container.set_snapshot(snapshot, cache_dir, self.selected_ids)

        if not snapshot.items:
            message = snapshot.empty_message or "No games in this view."
            self.compact_container.game_page.set_empty_state(message, show_add=True)

    def set_grid_widgets(self, widgets: list) -> None:
        """Set standard-grid widgets without exposing the stack to callers."""
        if self._empty_label is not None and self._empty_label not in widgets:
            self._empty_label = None
        self.grid_container.set_banner_widgets(widgets)

    def set_empty_grid_message(self, message: str, show_add: bool = False) -> None:
        """Render an empty state in the standard grid renderer."""
        if self._empty_label is None or sip.isdeleted(self._empty_label):
            self._empty_label = QLabel(self.grid_container)
            self._empty_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
            self._empty_label.setStyleSheet("color: #777777; font-size: 14px; padding: 40px;")
        self._empty_label.setText(message)
        self.set_grid_widgets([self._empty_label])

    def set_mode(self, mode: str, use_virtual: bool = False) -> int:
        """Select the renderer for a logical mode and return its index."""
        if mode == "list":
            index = self.LIST
        elif mode in ("compact", "steam"):
            index = self.COMPACT
        elif use_virtual:
            index = self.VIRTUAL_GRID
        else:
            index = self.GRID
        self.setCurrentIndex(index)
        return index

    def visible_ids(self, mode: str) -> set[int]:
        """Return IDs visible in the active renderer from the shared model."""
        if self.snapshot is not None:
            return set(self.snapshot.visible_ids)
        return set()

    def update_cloud_status(self, game_id: int, status) -> None:
        self.list_view.update_cloud_status(game_id, status)
        self.virtual_grid.update_cloud_status(game_id, status)
        self.compact_container.update_cloud_status(game_id, status)

    def update_update_available(self, game_id: int, available: bool) -> None:
        self.list_view.update_update_available(game_id, available)
        self.virtual_grid.update_update_available(game_id, available)
        self.compact_container.update_update_available(game_id, available)

    def update_game_icon(self, game_id: int, icon_path: str) -> None:
        self.list_view.update_game_icon(game_id, icon_path)
        self.virtual_grid.update_icon(game_id, icon_path)
        self.compact_container.update_game_icon(game_id, icon_path)

    def update_missing(self, game_id: int, missing: bool) -> None:
        self.list_view.update_missing(game_id, missing)
        self.virtual_grid.update_missing(game_id, missing)

    def set_selected_game_ids(self, game_ids: set[int]) -> None:
        self.selected_ids = set(game_ids)
        self.list_view.set_selected_game_ids(self.selected_ids)
        self.virtual_grid.set_selected_game_ids(self.selected_ids)
        self.compact_container.set_selected_game_ids(self.selected_ids)
