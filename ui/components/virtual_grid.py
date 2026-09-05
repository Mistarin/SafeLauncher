"""
Virtualized Game Grid View for Large Game Libraries (500+ Games).
Uses Qt's QListView in IconMode with a custom QStyledItemDelegate to ensure only
currently visible cards are decoded and rendered in memory.
"""

from __future__ import annotations

import os
from typing import Optional, Set, Dict, Any, Tuple
from PyQt6.QtCore import (
    Qt, pyqtSignal, QPoint, QRect, QRectF, QSize, QModelIndex, QItemSelectionModel
)
from PyQt6.QtWidgets import (
    QListView, QStyledItemDelegate, QStyleOptionViewItem, QStyle
)
from PyQt6.QtGui import (
    QPainter, QColor, QFont, QFontMetrics, QPixmap, QPixmapCache,
    QPainterPath, QStandardItemModel, QStandardItem
)

from ui.icons import get_icon
from core.cloud_save_sync import SyncStatus


GAME_ID_ROLE = Qt.ItemDataRole.UserRole + 1
NAME_ROLE = Qt.ItemDataRole.UserRole + 2
PATH_ROLE = Qt.ItemDataRole.UserRole + 3
BANNER_PATH_ROLE = Qt.ItemDataRole.UserRole + 4
PLAYTIME_ROLE = Qt.ItemDataRole.UserRole + 5
VERSION_ROLE = Qt.ItemDataRole.UserRole + 6
ICON_PATH_ROLE = Qt.ItemDataRole.UserRole + 7
IS_MISSING_ROLE = Qt.ItemDataRole.UserRole + 8
IS_FAVORITE_ROLE = Qt.ItemDataRole.UserRole + 9
IS_UPDATE_ROLE = Qt.ItemDataRole.UserRole + 10
CLOUD_STATUS_ROLE = Qt.ItemDataRole.UserRole + 11
STEAM_ID_ROLE = Qt.ItemDataRole.UserRole + 12


def _format_playtime_str(seconds: int) -> str:
    """Format playtime seconds into human-readable string."""
    if not seconds or seconds < 60:
        return "Never played" if not seconds else f"{seconds}s"
    minutes = seconds // 60
    hours = minutes // 60
    remaining_mins = minutes % 60
    if hours > 0:
        return f"{hours}h {remaining_mins}m" if remaining_mins else f"{hours}h"
    return f"{minutes}m"


class GameCardItemDelegate(QStyledItemDelegate):
    """
    Renders 2:3 aspect ratio game banner cards with title and playtime footer.
    Only paints items visible within the active viewport.
    """

    def __init__(self, parent=None, card_width: int = 200):
        super().__init__(parent)
        self.card_width = card_width
        self.card_height = int(card_width * 1.5)
        self.footer_height = 55
        self.total_height = self.card_height + self.footer_height
        self._font_title = QFont("Arial", 10, QFont.Weight.Bold)
        self._font_subtitle = QFont("Arial", 8)
        self._font_badge = QFont("Arial", 8, QFont.Weight.Bold)

    def set_card_width(self, width: int) -> None:
        self.card_width = max(120, width)
        self.card_height = int(self.card_width * 1.5)
        self.total_height = self.card_height + self.footer_height

    def sizeHint(self, option: QStyleOptionViewItem, index: QModelIndex) -> QSize:
        return QSize(self.card_width, self.total_height)

    def paint(self, painter: QPainter, option: QStyleOptionViewItem, index: QModelIndex) -> None:
        if not index.isValid():
            return

        painter.save()
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, True)

        # Retrieve model item data
        game_id = index.data(GAME_ID_ROLE) or 0
        name = index.data(NAME_ROLE) or "Unknown Game"
        banner_path = index.data(BANNER_PATH_ROLE) or ""
        playtime_seconds = index.data(PLAYTIME_ROLE) or 0
        version = index.data(VERSION_ROLE) or ""
        is_missing = bool(index.data(IS_MISSING_ROLE))
        is_favorite = bool(index.data(IS_FAVORITE_ROLE))
        is_update = bool(index.data(IS_UPDATE_ROLE))
        cloud_status = index.data(CLOUD_STATUS_ROLE)

        is_selected = bool(option.state & QStyle.StateFlag.State_Selected)
        is_hovered = bool(option.state & QStyle.StateFlag.State_MouseOver)

        rect = option.rect
        card_x = rect.x() + (rect.width() - self.card_width) // 2
        card_y = rect.y()
        card_rect = QRect(card_x, card_y, self.card_width, self.total_height)
        cover_rect = QRect(card_x, card_y, self.card_width, self.card_height)
        footer_rect = QRect(card_x + 4, card_y + self.card_height, self.card_width - 8, self.footer_height)

        # 1. Base card container background and border
        bg_path = QPainterPath()
        bg_path.addRoundedRect(QRectF(card_rect), 8.0, 8.0)

        if is_selected:
            painter.fillPath(bg_path, QColor("#0D2A40"))
            painter.setPen(QColor("#3B9FE8"))
            painter.drawPath(bg_path)
        elif is_hovered:
            painter.fillPath(bg_path, QColor("#181B22"))
            painter.setPen(QColor("#3A4150"))
            painter.drawPath(bg_path)
        else:
            painter.fillPath(bg_path, QColor("#14171D"))
            painter.setPen(QColor("#252A33"))
            painter.drawPath(bg_path)

        # 2. Render cover art with rounded top corners
        painter.save()
        cover_clip = QPainterPath()
        cover_clip.addRoundedRect(
            QRectF(cover_rect.x(), cover_rect.y(), cover_rect.width(), cover_rect.height() + 8),
            8.0, 8.0
        )
        painter.setClipPath(cover_clip)

        cover_pixmap = self._get_cached_cover(game_id, banner_path, name, is_missing)
        if cover_pixmap and not cover_pixmap.isNull():
            painter.drawPixmap(cover_rect, cover_pixmap)

        # Missing game overlay or hover darkening
        if is_missing:
            painter.fillRect(cover_rect, QColor(20, 20, 20, 175))
        elif is_hovered:
            painter.fillRect(cover_rect, QColor(0, 0, 0, 70))

        painter.restore()

        # 3. Badges on cover
        # Update indicator (top-left)
        if is_update:
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QColor("#35C98A"))
            painter.drawEllipse(cover_rect.x() + 8, cover_rect.y() + 8, 9, 9)

        # Favorite star (top-right)
        fav_icon_rect = QRect(cover_rect.right() - 26, cover_rect.y() + 6, 20, 20)
        if is_favorite:
            star_icon = get_icon("ph.star-fill", color="#F5C451")
            star_icon.paint(painter, fav_icon_rect)
        elif is_hovered:
            star_icon = get_icon("ph.star-bold", color="#6F7682")
            star_icon.paint(painter, fav_icon_rect)

        # Quick-launch play button (center, on hover)
        if is_hovered and not is_missing:
            play_btn_size = 44
            play_x = cover_rect.x() + (self.card_width - play_btn_size) // 2
            play_y = cover_rect.y() + (self.card_height - play_btn_size) // 2
            play_rect = QRect(play_x, play_y, play_btn_size, play_btn_size)

            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QColor("#3B9FE8"))
            painter.drawEllipse(play_rect)

            play_icon = get_icon("ph.play-fill", color="#FFFFFF")
            icon_inner = QRect(play_x + 13, play_y + 13, 18, 18)
            play_icon.paint(painter, icon_inner)

        # Version badge (bottom-left of cover)
        if version:
            painter.setFont(self._font_badge)
            fm = QFontMetrics(self._font_badge)
            text_w = fm.horizontalAdvance(version)
            badge_rect = QRect(cover_rect.x() + 8, cover_rect.bottom() - 22, text_w + 12, 16)

            badge_path = QPainterPath()
            badge_path.addRoundedRect(QRectF(badge_rect), 4.0, 4.0)
            painter.fillPath(badge_path, QColor(20, 23, 29, 225))
            painter.setPen(QColor("#252A33"))
            painter.drawPath(badge_path)

            painter.setPen(QColor("#A7ADB8"))
            painter.drawText(badge_rect, Qt.AlignmentFlag.AlignCenter, version)

        # Cloud sync badge (bottom-right of cover)
        if cloud_status is not None:
            self._draw_cloud_badge(painter, cover_rect, cloud_status)

        # 4. Footer: Game Name & Playtime
        title_rect = QRect(footer_rect.x(), footer_rect.y() + 6, footer_rect.width(), 22)
        playtime_rect = QRect(footer_rect.x(), footer_rect.y() + 28, footer_rect.width(), 18)

        painter.setFont(self._font_title)
        fm_title = QFontMetrics(self._font_title)
        elided_title = fm_title.elidedText(name, Qt.TextElideMode.ElideRight, title_rect.width())

        if is_selected:
            painter.setPen(QColor("#3B9FE8"))
        elif is_missing:
            painter.setPen(QColor("#6F7682"))
        else:
            painter.setPen(QColor("#F5F7FA"))
        painter.drawText(title_rect, Qt.AlignmentFlag.AlignCenter, elided_title)

        painter.setFont(self._font_subtitle)
        painter.setPen(QColor("#A7ADB8"))
        playtime_str = _format_playtime_str(playtime_seconds)
        painter.drawText(playtime_rect, Qt.AlignmentFlag.AlignCenter, playtime_str)

        painter.restore()

    def _draw_cloud_badge(self, painter: QPainter, cover_rect: QRect, status: Any) -> None:
        """Draw small cloud save status pill at bottom-right of cover."""
        badge_x = cover_rect.right() - 28
        badge_y = cover_rect.bottom() - 24
        badge_rect = QRect(badge_x, badge_y, 22, 18)

        badge_path = QPainterPath()
        badge_path.addRoundedRect(QRectF(badge_rect), 4.0, 4.0)
        painter.fillPath(badge_path, QColor(20, 23, 29, 225))

        icon_rect = QRect(badge_x + 3, badge_y + 1, 16, 16)
        if status == SyncStatus.IN_SYNC:
            painter.setPen(QColor(53, 201, 138, 100))
            painter.drawPath(badge_path)
            get_icon("ph.cloud-check-fill", color="#35C98A").paint(painter, icon_rect)
        elif status == SyncStatus.LOCAL_NEWER:
            painter.setPen(QColor(59, 159, 232, 100))
            painter.drawPath(badge_path)
            get_icon("ph.cloud-arrow-up-fill", color="#3B9FE8").paint(painter, icon_rect)
        elif status == SyncStatus.CLOUD_NEWER:
            painter.setPen(QColor(229, 169, 61, 100))
            painter.drawPath(badge_path)
            get_icon("ph.cloud-arrow-down-fill", color="#E5A93D").paint(painter, icon_rect)
        elif status == SyncStatus.NO_SAVES:
            painter.setPen(QColor(240, 93, 108, 100))
            painter.drawPath(badge_path)
            get_icon("ph.cloud-slash-bold", color="#F05D6C").paint(painter, icon_rect)

    def _get_cached_cover(self, game_id: int, banner_path: str, name: str, is_missing: bool) -> QPixmap:
        """Fetch or generate and cache the scaled cover pixmap using QPixmapCache."""
        target_w, target_h = self.card_width, self.card_height
        cache_key = f"vgrid_{game_id}_{target_w}_{target_h}_{banner_path}"

        cached = QPixmapCache.find(cache_key)
        if cached and not cached.isNull():
            return cached

        if banner_path and banner_path != "none" and os.path.exists(banner_path):
            src = QPixmap(banner_path)
            if not src.isNull():
                scaled = src.scaled(
                    QSize(target_w, target_h),
                    Qt.AspectRatioMode.KeepAspectRatioByExpanding,
                    Qt.TransformationMode.SmoothTransformation
                )
                crop_x = max(0, (scaled.width() - target_w) // 2)
                crop_y = max(0, (scaled.height() - target_h) // 2)
                cropped = scaled.copy(crop_x, crop_y, target_w, target_h)
                QPixmapCache.insert(cache_key, cropped)
                return cropped

        # Fallback dark placeholder card
        placeholder = QPixmap(target_w, target_h)
        placeholder.fill(QColor("#18181F"))
        p = QPainter(placeholder)
        p.setPen(QColor("#6F7682"))
        p.setFont(QFont("Arial", 9, QFont.Weight.Bold))
        p.drawText(placeholder.rect(), Qt.AlignmentFlag.AlignCenter, name)
        p.end()
        QPixmapCache.insert(cache_key, placeholder)
        return placeholder


class VirtualizedGameGridView(QListView):
    """
    Virtualized 2:3 card grid presentation for massive game libraries (500+ games).
    Inherits QListView with IconMode to render only visible cards via GameCardItemDelegate.
    """
    game_clicked = pyqtSignal(int)
    game_double_clicked = pyqtSignal(int)
    game_launch_clicked = pyqtSignal(int)
    favorite_clicked = pyqtSignal(int)
    game_right_clicked = pyqtSignal(int, QPoint)

    def __init__(self, parent=None, card_width: int = 200, spacing: int = 15):
        super().__init__(parent)
        self.card_width = card_width
        self.spacing = spacing

        self.delegate = GameCardItemDelegate(self, card_width=card_width)
        self.setItemDelegate(self.delegate)

        self.model = QStandardItemModel(self)
        self.setModel(self.model)

        self.setViewMode(QListView.ViewMode.IconMode)
        self.setResizeMode(QListView.ResizeMode.Adjust)
        self.setWrapping(True)
        self.setUniformItemSizes(True)
        self.setWordWrap(True)
        self.setSpacing(spacing)
        self.setMovement(QListView.Movement.Static)
        self.setMouseTracking(True)
        self.setSelectionMode(QListView.SelectionMode.ExtendedSelection)

        self.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAsNeeded)
        self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setStyleSheet("""
            QListView {
                background: transparent;
                background-color: transparent;
                border: none;
                padding: 10px;
            }
            QListView::item {
                background: transparent;
                border: none;
            }
        """)

        self._items_by_game_id: Dict[int, QStandardItem] = {}
        self._hovered_row = -1

    def set_card_width(self, new_width: int) -> None:
        """Dynamically resize cards and refresh grid layout geometry."""
        self.card_width = new_width
        self.delegate.set_card_width(new_width)
        self.setGridSize(QSize(self.delegate.card_width + self.spacing, self.delegate.total_height + self.spacing))
        self.scheduleDelayedItemsLayout()
        self.viewport().update()

    def set_games(
        self,
        processed_items: list,
        selected_ids: Optional[Set[int]] = None,
        update_status_map: Optional[dict] = None,
        cloud_status_map: Optional[dict] = None
    ) -> None:
        """Populate the virtual model with games."""
        selected_ids = selected_ids or set()
        update_status_map = update_status_map or {}
        cloud_status_map = cloud_status_map or {}

        self.model.clear()
        self._items_by_game_id.clear()

        selection = self.selectionModel()

        for item_data in processed_items:
            if isinstance(item_data, tuple) and len(item_data) == 4 and not hasattr(item_data, "id") and hasattr(item_data[0], "__getitem__"):
                g, is_missing, playtime, is_fav = item_data
            else:
                g = item_data
                is_missing = False
                playtime = g[7] if len(g) > 7 and g[7] else 0
                is_fav = bool(g[8]) if len(g) > 8 and g[8] else False

            raw_id = g[0] if hasattr(g, "__getitem__") else getattr(g, "id", 0)
            if hasattr(raw_id, "id"):
                raw_id = raw_id.id
            if isinstance(raw_id, (tuple, list)) and len(raw_id) > 0:
                raw_id = raw_id[0]
            game_id = int(raw_id)

            name = g[1] if len(g) > 1 and g[1] else ""
            path = g[2] if len(g) > 2 and g[2] else ""
            banner_url = g[5] if len(g) > 5 and g[5] else ""
            steam_id = g[6] if len(g) > 6 and g[6] else ""
            version_override = g[15] if len(g) > 15 and g[15] else ""
            icon_url = g[18] if len(g) > 18 and g[18] else ""

            if banner_url and not os.path.exists(banner_url):
                banner_url = ""

            item = QStandardItem()
            item.setData(game_id, GAME_ID_ROLE)
            item.setData(name, NAME_ROLE)
            item.setData(path, PATH_ROLE)
            item.setData(banner_url, BANNER_PATH_ROLE)
            item.setData(playtime or 0, PLAYTIME_ROLE)
            item.setData(version_override, VERSION_ROLE)
            item.setData(icon_url, ICON_PATH_ROLE)
            item.setData(is_missing, IS_MISSING_ROLE)
            item.setData(is_fav, IS_FAVORITE_ROLE)
            item.setData(update_status_map.get(game_id, False), IS_UPDATE_ROLE)

            cached_cloud = cloud_status_map.get(game_id)
            status_val = cached_cloud[0] if cached_cloud else None
            item.setData(status_val, CLOUD_STATUS_ROLE)
            item.setData(str(steam_id or ""), STEAM_ID_ROLE)

            self.model.appendRow(item)
            self._items_by_game_id[game_id] = item

            if game_id in selected_ids and selection:
                selection.select(item.index(), QItemSelectionModel.SelectionFlag.Select)

        self.setGridSize(QSize(self.delegate.card_width + self.spacing, self.delegate.total_height + self.spacing))

    def update_banner(self, game_id: int, banner_path: str) -> None:
        item = self._items_by_game_id.get(game_id)
        if item:
            item.setData(banner_path, BANNER_PATH_ROLE)
            self.viewport().update(self.visualRect(item.index()))

    def update_icon(self, game_id: int, icon_path: str) -> None:
        item = self._items_by_game_id.get(game_id)
        if item:
            item.setData(icon_path, ICON_PATH_ROLE)
            self.viewport().update(self.visualRect(item.index()))

    def update_playtime(self, game_id: int, seconds: int) -> None:
        item = self._items_by_game_id.get(game_id)
        if item:
            item.setData(seconds, PLAYTIME_ROLE)
            self.viewport().update(self.visualRect(item.index()))

    def update_favorite(self, game_id: int, is_favorite: bool) -> None:
        item = self._items_by_game_id.get(game_id)
        if item:
            item.setData(is_favorite, IS_FAVORITE_ROLE)
            self.viewport().update(self.visualRect(item.index()))

    def update_missing(self, game_id: int, is_missing: bool) -> None:
        item = self._items_by_game_id.get(game_id)
        if item:
            item.setData(is_missing, IS_MISSING_ROLE)
            self.viewport().update(self.visualRect(item.index()))

    def update_update_available(self, game_id: int, is_available: bool) -> None:
        item = self._items_by_game_id.get(game_id)
        if item:
            item.setData(is_available, IS_UPDATE_ROLE)
            self.viewport().update(self.visualRect(item.index()))

    def update_cloud_status(self, game_id: int, status: Any) -> None:
        item = self._items_by_game_id.get(game_id)
        if item:
            item.setData(status, CLOUD_STATUS_ROLE)
            self.viewport().update(self.visualRect(item.index()))

    def set_game_selected(self, game_id: int, selected: bool) -> None:
        item = self._items_by_game_id.get(game_id)
        selection = self.selectionModel()
        if item and selection:
            flag = QItemSelectionModel.SelectionFlag.Select if selected else QItemSelectionModel.SelectionFlag.Deselect
            selection.select(item.index(), flag)

    def selected_game_ids(self) -> Set[int]:
        selected_indexes = self.selectedIndexes()
        ids = set()
        for idx in selected_indexes:
            gid = idx.data(GAME_ID_ROLE)
            if gid is not None:
                ids.add(int(gid))
        return ids

    def mousePressEvent(self, event) -> None:
        idx = self.indexAt(event.pos())
        if not idx.isValid():
            super().mousePressEvent(event)
            return

        game_id = idx.data(GAME_ID_ROLE)
        if game_id is None:
            super().mousePressEvent(event)
            return

        item_rect = self.visualRect(idx)
        rel_x = event.pos().x() - item_rect.x()
        rel_y = event.pos().y() - item_rect.y()

        # Handle right-click context menu
        if event.button() == Qt.MouseButton.RightButton:
            self.game_right_clicked.emit(int(game_id), event.globalPosition().toPoint())
            return

        # Check favorite star hit (top right area)
        card_w = self.delegate.card_width
        card_h = self.delegate.card_height
        if (card_w - 36) <= rel_x <= card_w and 0 <= rel_y <= 36:
            self.favorite_clicked.emit(int(game_id))
            return

        # Check play button hit (center area of cover)
        play_btn_size = 44
        play_x = (card_w - play_btn_size) // 2
        play_y = (card_h - play_btn_size) // 2
        if play_x <= rel_x <= (play_x + play_btn_size) and play_y <= rel_y <= (play_y + play_btn_size):
            self.game_launch_clicked.emit(int(game_id))
            return

        super().mousePressEvent(event)
        self.game_clicked.emit(int(game_id))

    def mouseDoubleClickEvent(self, event) -> None:
        idx = self.indexAt(event.pos())
        if idx.isValid():
            game_id = idx.data(GAME_ID_ROLE)
            if game_id is not None:
                self.game_double_clicked.emit(int(game_id))
                return
        super().mouseDoubleClickEvent(event)

    def mouseMoveEvent(self, event) -> None:
        super().mouseMoveEvent(event)
        idx = self.indexAt(event.pos())
        row = idx.row() if idx.isValid() else -1
        if row != self._hovered_row:
            old_row = self._hovered_row
            self._hovered_row = row
            if old_row != -1:
                old_idx = self.model.index(old_row, 0)
                self.viewport().update(self.visualRect(old_idx))
            if row != -1:
                self.viewport().update(self.visualRect(idx))

    def leaveEvent(self, event) -> None:
        super().leaveEvent(event)
        if self._hovered_row != -1:
            old_idx = self.model.index(self._hovered_row, 0)
            self._hovered_row = -1
            self.viewport().update(self.visualRect(old_idx))


class BannerProxy:
    """
    Zero-overhead proxy implementing GameBannerWidget's public interface.
    Allows MainWindow methods to update virtualized items transparently.
    """

    def __init__(self, game_id: int, grid_view: VirtualizedGameGridView):
        self.game_id = game_id
        self.grid_view = grid_view

    def set_banner(self, banner_path: str) -> None:
        self.grid_view.update_banner(self.game_id, banner_path)

    def set_icon(self, icon_path: str) -> None:
        self.grid_view.update_icon(self.game_id, icon_path)

    def set_missing(self, is_missing: bool) -> None:
        self.grid_view.update_missing(self.game_id, is_missing)

    def set_selected(self, selected: bool) -> None:
        self.grid_view.set_game_selected(self.game_id, selected)

    def set_favorite(self, is_favorite: bool) -> None:
        self.grid_view.update_favorite(self.game_id, is_favorite)

    def set_update_available(self, is_available: bool) -> None:
        self.grid_view.update_update_available(self.game_id, is_available)

    def set_cloud_status(self, status: Any) -> None:
        self.grid_view.update_cloud_status(self.game_id, status)

    def set_playtime(self, seconds: int) -> None:
        self.grid_view.update_playtime(self.game_id, seconds)

    def hide(self) -> None:
        pass

    def setParent(self, parent: Any) -> None:
        pass

    def deleteLater(self) -> None:
        pass
