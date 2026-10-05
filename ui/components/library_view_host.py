"""Shared host for SafeLauncher's library presentations.

The grid, virtualized grid, compact presentation, and game detail page are renderers
of the library. This host owns their lifetime, routing, and selection surface so
MainWindow does not need to know which widget is currently active.
"""

from __future__ import annotations

from typing import Optional, Set

from PyQt6 import sip
from PyQt6.QtCore import Qt, pyqtSignal, QPoint, QRect
from PyQt6.QtWidgets import QLabel, QPushButton, QStackedWidget, QVBoxLayout, QWidget

from core.library_controller import LibrarySnapshot
from ui.components.banner_card import GameBannerWidget
from ui.components.responsive_grid import ResponsiveGridContainer
from ui.components.virtual_grid import VirtualizedGameGridView
from ui.components.compact_game_page import CompactLayoutContainer
from ui.components.game_detail_page import GameDetailPageWidget


class LibraryViewHost(QStackedWidget):
    """Own and synchronize all library renderers.

    Renderers only render. Product actions remain callbacks supplied by the
    application shell, while snapshot, selection, and view-mode routing live
    here. The public API intentionally has one rendering entry point.
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
    cloud_menu_requested = pyqtSignal(int, QPoint)
    cloud_action_requested = pyqtSignal(int, str)
    back_requested = pyqtSignal()
    remove_requested = pyqtSignal(int)

    GRID = 0
    VIRTUAL_GRID = 1
    COMPACT = 2
    DETAIL = 3

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setStyleSheet("QStackedWidget { background: transparent; background-color: transparent; }")

        self.grid_container = ResponsiveGridContainer(self, card_width=200, spacing=15)
        self.grid_container.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.grid_container.setStyleSheet("background: transparent; background-color: transparent;")
        self.virtual_grid = VirtualizedGameGridView(self, card_width=200, spacing=15)
        self.compact_container = CompactLayoutContainer(self)
        self.steam_container = self.compact_container
        self.detail_page = GameDetailPageWidget(self)

        for widget in (self.grid_container, self.virtual_grid, self.compact_container, self.detail_page):
            self.addWidget(widget)

        self._connect_renderer_events()
        self.snapshot: Optional[LibrarySnapshot] = None
        self.selected_ids: set[int] = set()
        self.running_game_ids: set[int] = set()
        self._empty_widget: Optional[QWidget] = None
        self._empty_label: Optional[QLabel] = None
        self._empty_add_button: Optional[QPushButton] = None

    def _connect_renderer_events(self) -> None:
        self.virtual_grid.game_clicked.connect(self.game_selected.emit)
        self.virtual_grid.game_double_clicked.connect(self.game_double_clicked.emit)
        self.virtual_grid.game_launch_clicked.connect(self.game_launch_requested.emit)
        self.virtual_grid.favorite_clicked.connect(self.favorite_requested.emit)
        self.virtual_grid.game_right_clicked.connect(self.cloud_menu_requested.emit)
        self.virtual_grid.cloud_action_requested.connect(self.cloud_action_requested.emit)

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
        compact.cloud_action_requested.connect(self._emit_compact_cloud_action)

        # Detail Page events
        detail = self.detail_page
        detail.back_requested.connect(self.back_requested.emit)
        detail.play_requested.connect(self.game_launch_requested.emit)
        detail.edit_requested.connect(self.edit_requested.emit)
        detail.properties_requested.connect(self.properties_requested.emit)
        detail.save_manager_requested.connect(self.save_manager_requested.emit)
        detail.open_folder_requested.connect(self.open_folder_requested.emit)
        detail.prefix_maintenance_requested.connect(self.prefix_maintenance_requested.emit)
        detail.favorite_toggled.connect(self.favorite_requested.emit)
        detail.achievements_requested.connect(self.achievements_requested.emit)
        detail.screenshots_requested.connect(self.screenshots_requested.emit)
        detail.videos_requested.connect(self.videos_requested.emit)
        detail.remove_requested.connect(self.remove_requested.emit)
        detail.cloud_action_requested.connect(self._emit_compact_cloud_action)

    def _emit_compact_cloud_action(self, game_id: int, action: str) -> None:
        """Forward compact Cloud actions using the host's normal action bus."""
        self.cloud_action_requested.emit(int(game_id), str(action))

    def render_snapshot(
        self,
        snapshot: LibrarySnapshot,
        cache_dir: Optional[str] = None,
        selected_ids: Optional[Set[int]] = None,
    ) -> None:
        """Render one immutable snapshot into every renderer."""
        self.snapshot = snapshot
        self.selected_ids = set(selected_ids or set())
        self.virtual_grid.set_snapshot(snapshot, self.selected_ids)
        for game_id in self.running_game_ids:
            self.virtual_grid.update_running(game_id, True)
        self.compact_container.set_snapshot(snapshot, cache_dir, self.selected_ids)

        if not snapshot.items:
            message = snapshot.empty_message or "No games in this view."
            self.compact_container.game_page.set_empty_state(message, show_add=True)

    def set_grid_widgets(self, widgets: list) -> None:
        """Set standard-grid widgets without exposing the stack to callers."""
        if self._empty_widget is not None and self._empty_widget not in widgets:
            self._empty_widget = None
            self._empty_label = None
            self._empty_add_button = None
        self.grid_container.set_banner_widgets(widgets)

    def set_empty_grid_message(self, message: str, show_add: bool = False) -> None:
        """Render an empty state and optional Add Game action in the grid."""
        if self._empty_widget is None or sip.isdeleted(self._empty_widget):
            self._empty_widget = QWidget(self.grid_container)
            layout = QVBoxLayout(self._empty_widget)
            layout.setContentsMargins(24, 24, 24, 24)
            layout.setSpacing(12)
            layout.setAlignment(Qt.AlignmentFlag.AlignCenter)
            self._empty_label = QLabel(self._empty_widget)
            self._empty_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
            self._empty_label.setWordWrap(True)
            self._empty_label.setStyleSheet("color: #777777; font-size: 14px; padding: 40px;")
            layout.addWidget(self._empty_label)
            self._empty_add_button = QPushButton("Add Game", self._empty_widget)
            self._empty_add_button.setAccessibleName("Add a game to your library")
            self._empty_add_button.clicked.connect(self.add_game_requested.emit)
            layout.addWidget(self._empty_add_button, 0, Qt.AlignmentFlag.AlignHCenter)
        self._empty_label.setText(message)
        self._empty_add_button.setVisible(bool(show_add))
        self.set_grid_widgets([self._empty_widget])

    def set_mode(self, mode: str, use_virtual: bool = False) -> int:
        """Select the renderer for a logical mode and return its index."""
        if mode == "detail":
            index = self.DETAIL
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

    def visible_game_ids(self, *, lookahead_rows: int = 2) -> set[int]:
        """Return cards near the active grid viewport, including scroll lookahead."""
        viewport = None
        parent = self.parentWidget()
        if parent is not None and hasattr(parent, "viewport"):
            try:
                viewport = parent.viewport()
            except RuntimeError:
                viewport = None
        if self.currentIndex() == self.VIRTUAL_GRID:
            view = self.virtual_grid
            rect = view.viewport().rect()
            row_height = max(1, view.delegate.total_height + view.spacing())
            expanded = rect.adjusted(0, -row_height * lookahead_rows, 0, row_height * lookahead_rows)
            visible = set()
            for row in range(view.model.rowCount()):
                index = view.model.index(row, 0)
                if view.visualRect(index).intersects(expanded):
                    game_id = index.data(Qt.ItemDataRole.UserRole + 1)
                    if game_id is not None:
                        visible.add(int(game_id))
            return visible
        if self.currentIndex() == self.GRID:
            container = self.grid_container
            if viewport is None:
                visible_rect = container.rect()
                map_widget = container
            else:
                visible_rect = viewport.rect()
                map_widget = viewport
            row_height = max(1, container.card_width * 3 // 2 + container.spacing)
            expanded = visible_rect.adjusted(0, -row_height * lookahead_rows, 0, row_height * lookahead_rows)
            visible = set()
            for index, card in enumerate(container.widgets):
                try:
                    top_left = card.mapTo(map_widget, QPoint(0, 0))
                    if QRect(top_left, card.size()).intersects(expanded):
                        visible.add(int(card.game_id))
                except (RuntimeError, AttributeError, TypeError):
                    continue
            return visible
        return set()

    def update_cloud_status(self, game_id: int, status, local_stats=None, cloud_stats=None) -> None:
        self.virtual_grid.update_cloud_status(game_id, status)
        self.compact_container.update_cloud_status(game_id, status)
        if self.detail_page.current_game_id == game_id:
            self.detail_page.set_cloud_status(status, local_stats, cloud_stats)

    def update_update_available(self, game_id: int, available: bool) -> None:
        self.virtual_grid.update_update_available(game_id, available)
        self.compact_container.update_update_available(game_id, available)

    def update_update_state(self, game_id: int, state) -> None:
        """Fan out availability and live/cached provenance together."""
        self.virtual_grid.update_update_state(game_id, state)
        self.compact_container.update_update_state(game_id, state)

    def update_game_icon(self, game_id: int, icon_path: str) -> None:
        self.virtual_grid.update_icon(game_id, icon_path)
        self.compact_container.update_game_icon(game_id, icon_path)

    def update_favorite(self, game_id: int, is_favorite: bool) -> None:
        """Fan out one favorite change without rebuilding any presentation."""
        self.virtual_grid.update_favorite(game_id, is_favorite)
        self.compact_container.update_favorite(game_id, is_favorite)
        if self.detail_page.current_game_id == game_id:
            self.detail_page.update_favorite(is_favorite)

    def set_running_game_ids(self, game_ids) -> None:
        """Synchronize active-game actions across every library renderer."""
        previous_ids = self.running_game_ids
        self.running_game_ids = {int(game_id) for game_id in (game_ids or ())}
        for game_id in previous_ids - self.running_game_ids:
            self.virtual_grid.update_running(game_id, False)
        for game_id in self.running_game_ids:
            self.virtual_grid.update_running(game_id, True)

    def update_game_running(self, game_id: int, is_running: bool) -> None:
        game_id = int(game_id)
        if is_running:
            self.running_game_ids.add(game_id)
        else:
            self.running_game_ids.discard(game_id)
        self.virtual_grid.update_running(game_id, is_running)

    def update_missing(self, game_id: int, missing: bool) -> None:
        self.virtual_grid.update_missing(game_id, missing)

    def set_selected_game_ids(self, game_ids: set[int]) -> None:
        self.selected_ids = set(game_ids)
        self.virtual_grid.set_selected_game_ids(self.selected_ids)
        self.compact_container.set_selected_game_ids(self.selected_ids)
