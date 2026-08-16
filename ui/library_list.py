"""Rich visual list presentation for the library view with large left-aligned game icons."""

import os
from typing import Optional, Set
from PyQt6.QtCore import pyqtSignal, Qt, QSize
from PyQt6.QtWidgets import (
    QListWidget, QListWidgetItem, QWidget, QHBoxLayout, QVBoxLayout, QLabel, QFrame, QPushButton
)
from PyQt6.QtGui import QFont, QPixmap, QPainter, QColor, QIcon

from core.disk_utils import get_dir_size, format_size
from ui.icons import get_app_icon, get_icon


def _format_playtime_str(seconds: int) -> str:
    if not seconds or seconds < 60:
        return "Never played" if not seconds else f"{seconds}s"
    minutes = seconds // 60
    hours = minutes // 60
    remaining_mins = minutes % 60
    if hours > 0:
        return f"{hours}h {remaining_mins}m" if remaining_mins else f"{hours}h"
    return f"{minutes}m"


class LibraryListItemWidget(QWidget):
    """Custom widget for a library list item with large left-aligned icon and rich details."""
    launch_requested = pyqtSignal(int)

    def __init__(
        self,
        game_tuple: tuple,
        is_missing: bool = False,
        playtime_seconds: int = 0,
        is_favorite: bool = False,
        is_update_available: bool = False,
        cache_dir: Optional[str] = None,
        parent=None
    ):
        super().__init__(parent)
        self.game_id = game_tuple[0]
        self.name = game_tuple[1]
        self.path = game_tuple[2]
        self.executable = game_tuple[3]
        self.mode = game_tuple[4]
        self.banner_url = game_tuple[5]
        self.steam_id = game_tuple[6]
        self.tags = game_tuple[10] if len(game_tuple) > 10 else ""
        self.version = str(game_tuple[15]).strip() if len(game_tuple) > 15 and game_tuple[15] else ""
        self.icon_url = game_tuple[18] if len(game_tuple) > 18 and game_tuple[18] else ""
        self.is_missing = is_missing
        self.playtime_seconds = playtime_seconds
        self.is_favorite = is_favorite
        self.is_update_available = is_update_available
        self.cache_dir = cache_dir

        self.setFixedHeight(68)
        self._init_ui()

    def _init_ui(self):
        layout = QHBoxLayout(self)
        layout.setContentsMargins(10, 6, 14, 6)
        layout.setSpacing(14)

        # ── 1. Big Nice Game Icon on the Left (52x52) ──
        self.icon_label = QLabel(self)
        self.icon_label.setFixedSize(52, 52)
        self.icon_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.icon_label.setStyleSheet("""
            QLabel {
                background: #14171D;
                border: 1px solid #252A33;
                border-radius: 8px;
            }
        """)
        self._load_game_icon()
        layout.addWidget(self.icon_label)

        # ── 2. Information Stack to the Right ──
        info_layout = QVBoxLayout()
        info_layout.setContentsMargins(0, 2, 0, 2)
        info_layout.setSpacing(4)

        # Top line: Title + Version + Favorite + Update + Playtime
        top_line = QHBoxLayout()
        top_line.setSpacing(8)

        title_lbl = QLabel(self.name)
        title_lbl.setFont(QFont("Arial", 11, QFont.Weight.Bold))
        if self.is_missing:
            title_lbl.setStyleSheet("color: #6F7682; font-weight: 600; background: transparent;")
        else:
            title_lbl.setStyleSheet("color: #F5F7FA; font-weight: 600; background: transparent;")
        top_line.addWidget(title_lbl)

        if self.version:
            ver_badge = QLabel(self.version)
            ver_badge.setFont(QFont("Arial", 8, QFont.Weight.Bold))
            ver_badge.setStyleSheet("""
                QLabel {
                    background: #1A1E26;
                    color: #A7ADB8;
                    border: 1px solid #252A33;
                    border-radius: 4px;
                    padding: 1px 6px;
                }
            """)
            top_line.addWidget(ver_badge)

        if self.is_favorite:
            fav_lbl = QLabel("★")
            fav_lbl.setStyleSheet("color: #F5C451; font-size: 13px; font-weight: bold; background: transparent;")
            fav_lbl.setToolTip("Favorite")
            top_line.addWidget(fav_lbl)

        if self.is_update_available:
            upd_lbl = QLabel("● Update")
            upd_lbl.setFont(QFont("Arial", 8, QFont.Weight.Bold))
            upd_lbl.setStyleSheet("""
                QLabel {
                    background: rgba(53, 201, 138, 0.12);
                    color: #35C98A;
                    border: 1px solid rgba(53, 201, 138, 0.3);
                    border-radius: 4px;
                    padding: 1px 6px;
                }
            """)
            top_line.addWidget(upd_lbl)

        top_line.addStretch()

        playtime_lbl = QLabel(_format_playtime_str(self.playtime_seconds))
        playtime_lbl.setFont(QFont("Arial", 9))
        playtime_lbl.setStyleSheet("color: #A7ADB8; background: transparent;")
        top_line.addWidget(playtime_lbl)

        info_layout.addLayout(top_line)

        # Bottom line: Runner mode + Size + Executable / AppID / Tags
        bottom_line = QHBoxLayout()
        bottom_line.setSpacing(8)

        mode_lbl = QLabel(self.mode.upper() if self.mode else "SANDBOX")
        mode_lbl.setFont(QFont("Arial", 8, QFont.Weight.Bold))
        mode_lbl.setStyleSheet("""
            QLabel {
                background: #0D2A40;
                color: #3B9FE8;
                border: 1px solid rgba(59, 159, 232, 0.25);
                border-radius: 4px;
                padding: 1px 6px;
            }
        """)
        bottom_line.addWidget(mode_lbl)

        size_str = format_size(get_dir_size(self.path)) if self.path else "0 B"
        meta_items = [size_str]
        if self.executable:
            meta_items.append(self.executable)
        if self.steam_id:
            meta_items.append(f"AppID {self.steam_id}")
        if self.tags:
            meta_items.append(self.tags)

        meta_lbl = QLabel("  •  ".join(meta_items))
        meta_lbl.setFont(QFont("Arial", 9))
        meta_lbl.setStyleSheet("color: #6F7682; background: transparent;")
        bottom_line.addWidget(meta_lbl)
        bottom_line.addStretch()

        info_layout.addLayout(bottom_line)
        layout.addLayout(info_layout, 1)

        # ── 3. Quick-Launch Action Button on Far-Right ──
        if not self.is_missing:
            self.btn_row_launch = QPushButton("Launch")
            self.btn_row_launch.setFixedHeight(30)
            self.btn_row_launch.setCursor(Qt.CursorShape.PointingHandCursor)
            self.btn_row_launch.setStyleSheet("""
                QPushButton {
                    background-color: #3B9FE8;
                    color: #FFFFFF;
                    border: 1px solid #3B9FE8;
                    border-radius: 6px;
                    padding: 0 14px;
                    font-size: 11px;
                    font-weight: 600;
                    text-align: center;
                }
                QPushButton:hover {
                    background-color: #55ACED;
                    border-color: #55ACED;
                }
                QPushButton:pressed {
                    background-color: #2789D0;
                }
            """)
            self.btn_row_launch.clicked.connect(lambda: self.launch_requested.emit(self.game_id))
            layout.addWidget(self.btn_row_launch)

    def _load_game_icon(self):
        """Render the authentic game .exe icon on the left of each row in list view."""
        pix: Optional[QPixmap] = None

        # 1. Try explicit icon_url from DB
        if self.icon_url and os.path.exists(self.icon_url):
            loaded = QPixmap(self.icon_url)
            if not loaded.isNull():
                pix = loaded

        # 2. Try cached icon file from ~/.cache/safelauncher/icons/
        if pix is None and self.cache_dir:
            icons_dir = os.path.join(os.path.dirname(self.cache_dir), "icons")
            for ext in (".png", ".ico", ".jpg"):
                icon_path = os.path.join(icons_dir, f"icon_{self.game_id}{ext}")
                if os.path.exists(icon_path):
                    loaded = QPixmap(icon_path)
                    if not loaded.isNull():
                        pix = loaded
                        break

        # 3. Direct on-the-fly extraction from .exe if not yet cached
        if pix is None and self.path and self.executable:
            full_exe = os.path.join(self.path, self.executable)
            if os.path.isfile(full_exe):
                try:
                    from core.icon_extractor import extract_exe_icon
                    tmp_icon = f"/tmp/icon_{self.game_id}.png"
                    if extract_exe_icon(full_exe, tmp_icon):
                        loaded = QPixmap(tmp_icon)
                        if not loaded.isNull():
                            pix = loaded
                except Exception:
                    pass

        # 4. Fallback vector controller icon
        if pix is None:
            icon_color = "#475569" if self.is_missing else "#38bdf8"
            pix = get_app_icon("library", color=icon_color).pixmap(40, 40)

        # Scale and render with rounded 52x52 appearance
        target_size = 44
        scaled = pix.scaled(
            QSize(target_size, target_size),
            Qt.AspectRatioMode.KeepAspectRatio,
            Qt.TransformationMode.SmoothTransformation
        )

        final_pix = QPixmap(52, 52)
        final_pix.fill(Qt.GlobalColor.transparent)
        p = QPainter(final_pix)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)

        offset_x = (52 - scaled.width()) // 2
        offset_y = (52 - scaled.height()) // 2
        p.drawPixmap(offset_x, offset_y, scaled)

        if self.is_missing:
            # Dim / grey out icon for missing games
            p.fillRect(final_pix.rect(), QColor(15, 15, 18, 170))

        p.end()
        self.icon_label.setPixmap(final_pix)


class LibraryListView(QListWidget):
    """Operational list presentation with rich custom row widgets."""
    game_clicked = pyqtSignal(int)
    game_double_clicked = pyqtSignal(int)
    game_launch_clicked = pyqtSignal(int)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setSelectionMode(QListWidget.SelectionMode.ExtendedSelection)
        self.setAlternatingRowColors(False)
        self.setUniformItemSizes(False)
        self.setSpacing(4)
        self.setStyleSheet("""
            QListWidget {
                background: transparent;
                color: #F5F7FA;
                border: none;
                padding: 4px;
            }
            QListWidget::item {
                background: #14171D;
                border: 1px solid #252A33;
                border-radius: 8px;
                margin-bottom: 4px;
                padding: 0;
            }
            QListWidget::item:hover {
                background: #1A1E26;
                border: 1px solid #6F7682;
            }
            QListWidget::item:selected {
                background: #0D2A40;
                border: 1px solid #3B9FE8;
            }
        """)
        self.itemClicked.connect(self._on_item_clicked)
        self.itemDoubleClicked.connect(self._on_item_double_clicked)

    def _on_item_clicked(self, item: QListWidgetItem):
        data = item.data(Qt.ItemDataRole.UserRole)
        if data is not None:
            self.game_clicked.emit(int(data))

    def _on_item_double_clicked(self, item: QListWidgetItem):
        data = item.data(Qt.ItemDataRole.UserRole)
        if data is not None:
            self.game_double_clicked.emit(int(data))

    def set_games(
        self,
        processed_items: list,
        selected_ids: Optional[Set[int]] = None,
        update_status_map: Optional[dict] = None,
        cache_dir: Optional[str] = None
    ):
        """Populate the list view with custom rich game item widgets."""
        selected_ids = selected_ids or set()
        update_status_map = update_status_map or {}
        self.clear()

        for item_data in processed_items:
            # Check if item_data is wrapped as (g, is_missing, playtime, is_fav)
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
            is_update = update_status_map.get(game_id, False)

            item = QListWidgetItem(self)
            item.setSizeHint(QSize(0, 72))
            item.setData(Qt.ItemDataRole.UserRole, game_id)
            item.setData(Qt.ItemDataRole.ToolTipRole, g[2] or "")
            item.setSelected(game_id in selected_ids)

            row_widget = LibraryListItemWidget(
                g,
                is_missing=is_missing,
                playtime_seconds=playtime or 0,
                is_favorite=is_fav,
                is_update_available=is_update,
                cache_dir=cache_dir,
                parent=self
            )
            row_widget.launch_requested.connect(self.game_launch_clicked.emit)
            self.addItem(item)
            self.setItemWidget(item, row_widget)

    def selected_game_ids(self) -> Set[int]:
        return {int(item.data(Qt.ItemDataRole.UserRole)) for item in self.selectedItems() if item.data(Qt.ItemDataRole.UserRole) is not None}
