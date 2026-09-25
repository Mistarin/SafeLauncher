"""
Game Detail Page component for SafeLauncher Grid View.
Displays a dedicated detailed view when a game banner in Grid view is clicked:
- Back arrow navigation to return to the library grid view
- Big banner cover on the left
- Game title, tags, description, action buttons, specs, and achievements on the right
"""

import os
from html import escape
from typing import Optional, Any, Dict, List

from PyQt6.QtCore import Qt, QSize, pyqtSignal, QEvent
from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QProgressBar, QFrame, QScrollArea, QSizePolicy, QGridLayout
)
from PyQt6.QtGui import (
    QPixmap, QColor, QPainter, QFont, QIcon, QPainterPath, QLinearGradient
)

from ui.icons import get_icon
from ui.dialogs.achievements_dialog import create_rounded_pixmap
from core.date_formatting import format_timestamp, format_datetime_timestamp
from core.disk_utils import format_size, peek_dir_size
from core.game_status import cloud_indicator
from core.cloud_models import SyncStatus
from core.logger import get_logger

logger = get_logger("GameDetailPage")


class GameDetailPageWidget(QWidget):
    """
    Dedicated game detail page opened when clicking a banner in Grid view.
    Features:
    - Back button with arrow (and Escape key handling)
    - Big banner artwork on the left
    - Game title, tags, description, action buttons, specs, and achievements on the right
    """
    back_requested = pyqtSignal()
    play_requested = pyqtSignal(int)
    edit_requested = pyqtSignal(int)
    properties_requested = pyqtSignal(int)
    save_manager_requested = pyqtSignal(int)
    open_folder_requested = pyqtSignal(int)
    prefix_maintenance_requested = pyqtSignal(int)
    favorite_toggled = pyqtSignal(int)
    achievements_requested = pyqtSignal(int)
    screenshots_requested = pyqtSignal(int)
    videos_requested = pyqtSignal(int)
    remove_requested = pyqtSignal(int)
    cloud_action_requested = pyqtSignal(int, str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.current_game_id: Optional[int] = None
        self.current_game_data: Optional[tuple] = None
        self.is_favorite: bool = False
        self._cloud_status = None

        self.setObjectName("gameDetailPageRoot")
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self.setStyleSheet("""
            QWidget#gameDetailPageRoot {
                background: transparent;
            }
            QLabel {
                color: #FFFFFF;
            }
        """)

        # Main layout
        root_layout = QVBoxLayout(self)
        root_layout.setContentsMargins(0, 0, 0, 0)
        root_layout.setSpacing(0)

        # Top Navigation Bar with Back Button
        self.nav_bar = QWidget()
        self.nav_bar.setFixedHeight(50)
        self.nav_bar.setStyleSheet("background: transparent;")
        nav_layout = QHBoxLayout(self.nav_bar)
        nav_layout.setContentsMargins(20, 8, 20, 4)
        nav_layout.setSpacing(12)

        # Back Button with arrow icon
        self.btn_back = QPushButton(" Back to Library")
        self.btn_back.setIcon(get_icon("ph.arrow-left-bold", color="#FFFFFF"))
        self.btn_back.setIconSize(QSize(16, 16))
        self.btn_back.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_back.setToolTip("Back to library grid (Esc)")
        self.btn_back.setFixedHeight(34)
        self.btn_back.setStyleSheet("""
            QPushButton {
                background-color: rgba(255, 255, 255, 0.08);
                color: #FFFFFF;
                border: 1px solid rgba(255, 255, 255, 0.08);
                border-radius: 8px;
                padding: 0 14px;
                font-size: 12px;
                font-weight: 600;
            }
            QPushButton:hover {
                background-color: rgba(255, 255, 255, 0.16);
                border-color: rgba(255, 255, 255, 0.18);
            }
            QPushButton:pressed {
                background-color: rgba(255, 255, 255, 0.06);
            }
        """)
        self.btn_back.clicked.connect(self.back_requested.emit)
        nav_layout.addWidget(self.btn_back)

        nav_layout.addStretch()

        # Favorite button in top nav
        self.btn_fav = QPushButton()
        self.btn_fav.setFixedSize(34, 34)
        self.btn_fav.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_fav.setToolTip("Toggle favorite")
        self.btn_fav.setStyleSheet("""
            QPushButton {
                background-color: rgba(255, 255, 255, 0.08);
                border: 1px solid rgba(255, 255, 255, 0.08);
                border-radius: 8px;
            }
            QPushButton:hover {
                background-color: rgba(255, 255, 255, 0.16);
            }
        """)
        self.btn_fav.clicked.connect(self._on_favorite_clicked)
        nav_layout.addWidget(self.btn_fav)

        root_layout.addWidget(self.nav_bar)

        # Scrollable content area
        self.scroll_area = QScrollArea(self)
        self.scroll_area.setWidgetResizable(True)
        self.scroll_area.setFrameShape(QFrame.Shape.NoFrame)
        self.scroll_area.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.scroll_area.setStyleSheet("""
            QScrollArea {
                background: transparent;
                border: none;
            }
            QScrollBar:vertical {
                background: transparent;
                width: 8px;
                margin: 0px;
            }
            QScrollBar::handle:vertical {
                background: rgba(255, 255, 255, 0.15);
                border-radius: 4px;
                min-height: 24px;
            }
            QScrollBar::handle:vertical:hover {
                background: rgba(255, 255, 255, 0.25);
            }
        """)
        if self.scroll_area.viewport():
            self.scroll_area.viewport().setAutoFillBackground(False)
            self.scroll_area.viewport().setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
            self.scroll_area.viewport().setStyleSheet("background: transparent; border: none;")

        self.content_widget = QWidget()
        self.content_widget.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.content_widget.setStyleSheet("background: transparent;")
        
        # Horizontal layout: Left Big Banner, Right Details
        content_h_layout = QHBoxLayout(self.content_widget)
        content_h_layout.setContentsMargins(24, 8, 24, 32)
        content_h_layout.setSpacing(32)
        content_h_layout.setAlignment(Qt.AlignmentFlag.AlignTop)

        # -----------------------------------------------------------------
        # LEFT COLUMN: Big Banner & Artwork
        # -----------------------------------------------------------------
        left_column = QVBoxLayout()
        left_column.setContentsMargins(0, 0, 0, 0)
        left_column.setSpacing(14)
        left_column.setAlignment(Qt.AlignmentFlag.AlignTop)

        self.banner_label = QLabel()
        self.banner_label.setFixedSize(QSize(320, 480))
        self.banner_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.banner_label.setStyleSheet("""
            QLabel {
                background-color: #161A22;
                border: 1px solid rgba(255, 255, 255, 0.1);
                border-radius: 14px;
            }
        """)
        left_column.addWidget(self.banner_label)

        content_h_layout.addLayout(left_column, 0)

        # -----------------------------------------------------------------
        # RIGHT COLUMN: Title, Tags, Description, Actions, Specs, Achievements
        # -----------------------------------------------------------------
        right_column = QVBoxLayout()
        right_column.setContentsMargins(0, 0, 0, 0)
        right_column.setSpacing(18)
        right_column.setAlignment(Qt.AlignmentFlag.AlignTop)

        # 1. Title Header & Collection
        title_box = QVBoxLayout()
        title_box.setContentsMargins(0, 0, 0, 0)
        title_box.setSpacing(4)

        self.lbl_title = QLabel("Game Title")
        self.lbl_title.setFont(QFont("Arial", 24, QFont.Weight.Bold))
        self.lbl_title.setWordWrap(True)
        self.lbl_title.setStyleSheet("color: #FFFFFF; letter-spacing: -0.4px; background: transparent;")
        title_box.addWidget(self.lbl_title)

        self.lbl_collection = QLabel("")
        self.lbl_collection.setStyleSheet("color: #0A84FF; font-size: 11px; font-weight: 600; text-transform: uppercase; letter-spacing: 0.8px; background: transparent;")
        self.lbl_collection.setVisible(False)
        title_box.addWidget(self.lbl_collection)

        right_column.addLayout(title_box)

        # 2. Tags Row (Pill badges)
        self.tags_container = QWidget()
        self.tags_container.setStyleSheet("background: transparent;")
        self.tags_layout = QHBoxLayout(self.tags_container)
        self.tags_layout.setContentsMargins(0, 0, 0, 0)
        self.tags_layout.setSpacing(8)
        self.tags_layout.setAlignment(Qt.AlignmentFlag.AlignLeft)
        right_column.addWidget(self.tags_container)

        # 3. Primary & Secondary Actions Bar
        actions_box = QVBoxLayout()
        actions_box.setContentsMargins(0, 0, 0, 0)
        actions_box.setSpacing(10)

        # Primary Launch Game Button
        self.btn_launch = QPushButton(" Launch Game")
        self.btn_launch.setObjectName("detailPrimaryLaunch")
        self.btn_launch.setIcon(get_icon("ph.play-fill", color="#FFFFFF"))
        self.btn_launch.setIconSize(QSize(18, 18))
        self.btn_launch.setFixedHeight(44)
        self.btn_launch.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_launch.setStyleSheet("""
            QPushButton#detailPrimaryLaunch {
                background-color: #0A84FF;
                color: #FFFFFF;
                font-weight: 700;
                font-size: 14px;
                border: none;
                border-radius: 10px;
                padding: 0 24px;
                text-align: center;
                letter-spacing: 0.2px;
            }
            QPushButton#detailPrimaryLaunch:hover {
                background-color: #0071E3;
            }
            QPushButton#detailPrimaryLaunch:pressed {
                background-color: #005BB5;
            }
        """)
        self.btn_launch.clicked.connect(self._on_launch_clicked)
        actions_box.addWidget(self.btn_launch)

        # Secondary Actions Grid
        sec_btn_style = """
            QPushButton {
                background-color: #1A1F2C;
                color: #D1D5DB;
                border: 1px solid rgba(255, 255, 255, 0.08);
                border-radius: 8px;
                padding: 0 12px;
                font-weight: 500;
                font-size: 11px;
                text-align: center;
            }
            QPushButton:hover {
                background-color: #242B3D;
                border-color: rgba(255, 255, 255, 0.16);
                color: #FFFFFF;
            }
            QPushButton:pressed {
                background-color: #121620;
            }
        """

        sec_grid = QGridLayout()
        sec_grid.setContentsMargins(0, 0, 0, 0)
        sec_grid.setSpacing(8)

        self.btn_edit = QPushButton("Edit Game")
        self.btn_edit.setIcon(get_icon("ph.pencil-simple-bold", color="#0A84FF"))
        self.btn_edit.setIconSize(QSize(14, 14))
        self.btn_edit.setFixedHeight(34)
        self.btn_edit.setStyleSheet(sec_btn_style)
        self.btn_edit.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_edit.clicked.connect(lambda: self.current_game_id and self.edit_requested.emit(self.current_game_id))
        sec_grid.addWidget(self.btn_edit, 0, 0)

        self.btn_properties = QPushButton("Properties")
        self.btn_properties.setIcon(get_icon("ph.sliders-horizontal-bold", color="#8E8E93"))
        self.btn_properties.setIconSize(QSize(14, 14))
        self.btn_properties.setFixedHeight(34)
        self.btn_properties.setStyleSheet(sec_btn_style)
        self.btn_properties.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_properties.clicked.connect(lambda: self.current_game_id and self.properties_requested.emit(self.current_game_id))
        sec_grid.addWidget(self.btn_properties, 0, 1)

        self.btn_saves = QPushButton("Local Saves")
        self.btn_saves.setIcon(get_icon("ph.floppy-disk-bold", color="#8E8E93"))
        self.btn_saves.setIconSize(QSize(14, 14))
        self.btn_saves.setFixedHeight(34)
        self.btn_saves.setStyleSheet(sec_btn_style)
        self.btn_saves.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_saves.clicked.connect(lambda: self.current_game_id and self.save_manager_requested.emit(self.current_game_id))
        sec_grid.addWidget(self.btn_saves, 0, 2)

        self.btn_folder = QPushButton("Game Folder")
        self.btn_folder.setIcon(get_icon("ph.folder-open-bold", color="#8E8E93"))
        self.btn_folder.setIconSize(QSize(14, 14))
        self.btn_folder.setFixedHeight(34)
        self.btn_folder.setStyleSheet(sec_btn_style)
        self.btn_folder.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_folder.clicked.connect(lambda: self.current_game_id and self.open_folder_requested.emit(self.current_game_id))
        sec_grid.addWidget(self.btn_folder, 1, 0)

        self.btn_screenshots = QPushButton("Screenshots")
        self.btn_screenshots.setIcon(get_icon("ph.image-bold", color="#8E8E93"))
        self.btn_screenshots.setIconSize(QSize(14, 14))
        self.btn_screenshots.setFixedHeight(34)
        self.btn_screenshots.setStyleSheet(sec_btn_style)
        self.btn_screenshots.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_screenshots.clicked.connect(lambda: self.current_game_id and self.screenshots_requested.emit(self.current_game_id))
        sec_grid.addWidget(self.btn_screenshots, 1, 1)

        self.btn_delete = QPushButton("Uninstall / Delete")
        self.btn_delete.setIcon(get_icon("ph.trash-bold", color="#FF453A"))
        self.btn_delete.setIconSize(QSize(14, 14))
        self.btn_delete.setFixedHeight(34)
        self.btn_delete.setStyleSheet("""
            QPushButton {
                background-color: rgba(255, 69, 58, 0.08);
                color: #FF453A;
                border: 1px solid rgba(255, 69, 58, 0.2);
                border-radius: 8px;
                padding: 0 12px;
                font-weight: 500;
                font-size: 11px;
                text-align: center;
            }
            QPushButton:hover {
                background-color: rgba(255, 69, 58, 0.16);
                border-color: rgba(255, 69, 58, 0.35);
                color: #FF6961;
            }
            QPushButton:pressed {
                background-color: rgba(255, 69, 58, 0.22);
            }
        """)
        self.btn_delete.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_delete.clicked.connect(lambda: self.current_game_id and self.remove_requested.emit(self.current_game_id))
        sec_grid.addWidget(self.btn_delete, 1, 2)

        actions_box.addLayout(sec_grid)
        right_column.addLayout(actions_box)

        # 4. Description / About Section
        desc_box = QVBoxLayout()
        desc_box.setContentsMargins(0, 4, 0, 0)
        desc_box.setSpacing(6)

        lbl_desc_hdr = QLabel("ABOUT THIS GAME")
        lbl_desc_hdr.setStyleSheet("color: #8E8E93; font-size: 11px; font-weight: 700; letter-spacing: 0.8px; background: transparent;")
        desc_box.addWidget(lbl_desc_hdr)

        self.lbl_description = QLabel("No description available.")
        self.lbl_description.setWordWrap(True)
        self.lbl_description.setTextFormat(Qt.TextFormat.RichText)
        self.lbl_description.setStyleSheet("""
            QLabel {
                color: #D1D5DB;
                font-size: 13px;
                line-height: 1.5;
                background: rgba(255, 255, 255, 0.03);
                border: 1px solid rgba(255, 255, 255, 0.05);
                border-radius: 10px;
                padding: 14px 16px;
            }
        """)
        desc_box.addWidget(self.lbl_description)
        right_column.addLayout(desc_box)

        # 5. Specifications & Stats Card
        self.spec_card = QFrame()
        self.spec_card.setObjectName("detailSpecsCard")
        self.spec_card.setStyleSheet("""
            QFrame#detailSpecsCard {
                background-color: #141822;
                border: 1px solid rgba(255, 255, 255, 0.06);
                border-radius: 12px;
            }
        """)
        spec_layout = QGridLayout(self.spec_card)
        spec_layout.setContentsMargins(18, 14, 18, 14)
        spec_layout.setHorizontalSpacing(24)
        spec_layout.setVerticalSpacing(10)

        # Column 0: Playtime & Last Played
        lbl_pt_h = QLabel("PLAYTIME")
        lbl_pt_h.setStyleSheet("color: #636366; font-size: 9px; font-weight: 700; letter-spacing: 0.6px; background: transparent;")
        spec_layout.addWidget(lbl_pt_h, 0, 0)
        self.lbl_playtime = QLabel("--")
        self.lbl_playtime.setStyleSheet("color: #F5F7FA; font-size: 12px; font-weight: 600; background: transparent;")
        spec_layout.addWidget(self.lbl_playtime, 1, 0)

        lbl_lp_h = QLabel("LAST PLAYED")
        lbl_lp_h.setStyleSheet("color: #636366; font-size: 9px; font-weight: 700; letter-spacing: 0.6px; background: transparent;")
        spec_layout.addWidget(lbl_lp_h, 2, 0)
        self.lbl_last_played = QLabel("--")
        self.lbl_last_played.setStyleSheet("color: #F5F7FA; font-size: 12px; font-weight: 600; background: transparent;")
        spec_layout.addWidget(self.lbl_last_played, 3, 0)

        # Column 1: Disk Size & Cloud Save
        lbl_ds_h = QLabel("DISK SIZE")
        lbl_ds_h.setStyleSheet("color: #636366; font-size: 9px; font-weight: 700; letter-spacing: 0.6px; background: transparent;")
        spec_layout.addWidget(lbl_ds_h, 0, 1)
        self.lbl_disk_size = QLabel("--")
        self.lbl_disk_size.setStyleSheet("color: #A1A1A6; font-size: 12px; font-weight: 500; background: transparent;")
        spec_layout.addWidget(self.lbl_disk_size, 1, 1)

        lbl_cs_h = QLabel("CLOUD SAVE")
        lbl_cs_h.setStyleSheet("color: #636366; font-size: 9px; font-weight: 700; letter-spacing: 0.6px; background: transparent;")
        spec_layout.addWidget(lbl_cs_h, 2, 1)

        cloud_row = QHBoxLayout()
        cloud_row.setContentsMargins(0, 0, 0, 0)
        cloud_row.setSpacing(6)
        self.lbl_cloud_status = QLabel("--")
        self.lbl_cloud_status.setStyleSheet("color: #A1A1A6; font-size: 12px; font-weight: 500; background: transparent;")
        cloud_row.addWidget(self.lbl_cloud_status)

        self.btn_cloud_action = QPushButton("Sync")
        self.btn_cloud_action.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_cloud_action.setStyleSheet("""
            QPushButton {
                background: #2563EB;
                color: #FFFFFF;
                border: none;
                border-radius: 4px;
                padding: 2px 8px;
                font-size: 10px;
                font-weight: bold;
            }
            QPushButton:hover {
                background: #3B82F6;
            }
        """)
        self.btn_cloud_action.setVisible(False)
        self.btn_cloud_action.clicked.connect(self._on_cloud_action_clicked)
        cloud_row.addWidget(self.btn_cloud_action)
        cloud_row.addStretch()
        spec_layout.addLayout(cloud_row, 3, 1)

        self.lbl_cloud_metadata = QLabel("")
        self.lbl_cloud_metadata.setStyleSheet(
            "color: #6F7682; font-size: 10px; font-weight: 500; background: transparent;"
        )
        self.lbl_cloud_metadata.setAccessibleName("Cloud save time and device")
        self.lbl_cloud_metadata.setVisible(False)
        spec_layout.addWidget(self.lbl_cloud_metadata, 4, 1)

        # Column 2: Launch Mode & Executable
        lbl_mode_h = QLabel("LAUNCH MODE")
        lbl_mode_h.setStyleSheet("color: #636366; font-size: 9px; font-weight: 700; letter-spacing: 0.6px; background: transparent;")
        spec_layout.addWidget(lbl_mode_h, 0, 2)
        self.lbl_mode = QLabel("--")
        self.lbl_mode.setStyleSheet("color: #A1A1A6; font-size: 12px; font-weight: 500; background: transparent;")
        spec_layout.addWidget(self.lbl_mode, 1, 2)

        lbl_exe_h = QLabel("EXECUTABLE")
        lbl_exe_h.setStyleSheet("color: #636366; font-size: 9px; font-weight: 700; letter-spacing: 0.6px; background: transparent;")
        spec_layout.addWidget(lbl_exe_h, 2, 2)
        self.lbl_exe = QLabel("--")
        self.lbl_exe.setStyleSheet("color: #A1A1A6; font-size: 12px; font-weight: 500; background: transparent;")
        spec_layout.addWidget(self.lbl_exe, 3, 2)

        right_column.addWidget(self.spec_card)

        # 6. Achievements Card (if game has achievements)
        self.ach_card = QFrame()
        self.ach_card.setObjectName("detailAchCard")
        self.ach_card.setStyleSheet("""
            QFrame#detailAchCard {
                background-color: #141822;
                border: 1px solid rgba(255, 255, 255, 0.06);
                border-radius: 12px;
            }
            QFrame#detailAchCard:hover {
                background-color: #1A202D;
                border-color: rgba(48, 209, 88, 0.35);
            }
        """)
        self.ach_card.setCursor(Qt.CursorShape.PointingHandCursor)
        self.ach_card.mousePressEvent = lambda e: self.current_game_id and self.achievements_requested.emit(self.current_game_id)

        ach_layout = QVBoxLayout(self.ach_card)
        ach_layout.setContentsMargins(18, 12, 18, 12)
        ach_layout.setSpacing(8)

        ach_hdr = QHBoxLayout()
        ach_hdr.setContentsMargins(0, 0, 0, 0)
        ach_title = QLabel("ACHIEVEMENTS")
        ach_title.setStyleSheet("color: #636366; font-size: 9px; font-weight: 700; letter-spacing: 0.8px; background: transparent;")
        ach_hdr.addWidget(ach_title)
        ach_hdr.addStretch()

        self.lbl_ach_ratio = QLabel("0 / 0 (0%)")
        self.lbl_ach_ratio.setStyleSheet("color: #30D158; font-size: 11px; font-weight: 700; background: transparent;")
        ach_hdr.addWidget(self.lbl_ach_ratio)
        ach_layout.addLayout(ach_hdr)

        self.ach_progress = QProgressBar()
        self.ach_progress.setFixedHeight(4)
        self.ach_progress.setTextVisible(False)
        self.ach_progress.setRange(0, 100)
        self.ach_progress.setValue(0)
        self.ach_progress.setStyleSheet("""
            QProgressBar {
                background-color: #1A1F28;
                border: none;
                border-radius: 2px;
            }
            QProgressBar::chunk {
                background: qlineargradient(x1:0, y1:0, x2:1, y2:0, stop:0 #30D158, stop:1 #34C759);
                border-radius: 2px;
            }
        """)
        ach_layout.addWidget(self.ach_progress)

        self.ach_badges_container = QWidget()
        self.ach_badges_layout = QHBoxLayout(self.ach_badges_container)
        self.ach_badges_layout.setContentsMargins(0, 2, 0, 0)
        self.ach_badges_layout.setSpacing(6)
        ach_layout.addWidget(self.ach_badges_container)

        self.ach_card.setVisible(False)
        right_column.addWidget(self.ach_card)

        right_column.addStretch()
        content_h_layout.addLayout(right_column, 1)

        self.scroll_area.setWidget(self.content_widget)
        root_layout.addWidget(self.scroll_area)

    def keyPressEvent(self, event):
        if event.key() == Qt.Key.Key_Escape:
            self.back_requested.emit()
            event.accept()
            return
        super().keyPressEvent(event)

    def load_game(self, game_tuple: tuple, cache_dir: Optional[str] = None):
        """Populate the detail page with game information."""
        self.current_game_data = game_tuple
        if not game_tuple:
            return

        game_id, name, path, exe, mode = game_tuple[:5]
        banner_url = game_tuple[5] if len(game_tuple) > 5 else ""
        steam_id = game_tuple[6] if len(game_tuple) > 6 else ""
        playtime_seconds = game_tuple[7] if len(game_tuple) > 7 and game_tuple[7] else 0
        is_fav = bool(game_tuple[8]) if len(game_tuple) > 8 and game_tuple[8] else False
        last_played_ts = game_tuple[9] if len(game_tuple) > 9 and game_tuple[9] else 0
        tags_str = game_tuple[10] if len(game_tuple) > 10 and game_tuple[10] else ""
        collection = game_tuple[12] if len(game_tuple) > 12 and game_tuple[12] else ""
        version_override = game_tuple[14] if len(game_tuple) > 14 and game_tuple[14] else ""

        self.current_game_id = game_id
        self.is_favorite = is_fav

        # Update Title & Collection
        self.lbl_title.setText(name)
        if collection:
            self.lbl_collection.setText(f"COLLECTION: {collection}")
            self.lbl_collection.setVisible(True)
        else:
            self.lbl_collection.setVisible(False)

        # Update Favorite Star Button
        self._update_favorite_icon(is_fav)

        # Update Big Banner Art (320x480)
        target_size = QSize(320, 480)
        loaded = False
        if banner_url and os.path.exists(banner_url):
            pix = QPixmap(banner_url)
            if not pix.isNull():
                rounded = create_rounded_pixmap(pix, target_size, radius=14)
                self.banner_label.setPixmap(rounded)
                self.banner_label.setText("")
                loaded = True
        
        if not loaded:
            # Fallback placeholder with antialiased rounded corners & vector controller icon
            placeholder = QPixmap(target_size)
            placeholder.fill(Qt.GlobalColor.transparent)
            p = QPainter(placeholder)
            p.setRenderHint(QPainter.RenderHint.Antialiasing)
            
            # Rounded background card
            grad = QLinearGradient(0, 0, 0, target_size.height())
            grad.setColorAt(0.0, QColor("#1E2430"))
            grad.setColorAt(1.0, QColor("#121620"))
            p.setBrush(grad)
            p.setPen(QColor(255, 255, 255, 24))
            p.drawRoundedRect(1, 1, target_size.width() - 2, target_size.height() - 2, 14, 14)
            
            # Controller icon centered
            ico_pix = get_icon("ph.game-controller-bold", color="#4A5568").pixmap(64, 64)
            ico_x = (target_size.width() - 64) // 2
            ico_y = (target_size.height() // 2) - 50
            p.drawPixmap(ico_x, ico_y, ico_pix)
            
            # Title text centered below icon
            p.setPen(QColor("#E2E8F0"))
            p.setFont(QFont("Arial", 15, QFont.Weight.Bold))
            text_rect = placeholder.rect().adjusted(24, (target_size.height() // 2) + 24, -24, -24)
            p.drawText(text_rect, Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignTop | Qt.TextFlag.TextWordWrap, name)
            p.end()
            self.banner_label.setPixmap(placeholder)
            self.banner_label.setText("")

        # Update Tags
        self.set_tags([t.strip() for t in tags_str.split(",") if t.strip()] if tags_str else [])

        # Specs
        self.lbl_playtime.setText(self._format_playtime(playtime_seconds))
        self.lbl_last_played.setText(self._format_last_played(last_played_ts))
        self.lbl_mode.setText(str(mode).upper() if mode else "DEFAULT")
        self.lbl_exe.setText(os.path.basename(exe) if exe else "--")
        self.lbl_exe.setToolTip(exe or "")

        # Disk size initial peek
        cached_size = peek_dir_size(path) if (path and os.path.exists(path)) else None
        if cached_size is not None:
            self.lbl_disk_size.setText(format_size(cached_size))
        elif path and os.path.exists(path):
            self.lbl_disk_size.setText("Calculating...")
        else:
            self.lbl_disk_size.setText("--")

        # Initial description
        self.lbl_description.setText("No description available.")

    def set_description(self, text: str):
        """Set the formatted description text."""
        if not text or not str(text).strip():
            self.lbl_description.setText("No description available.")
        else:
            clean = str(text).strip()
            self.lbl_description.setText(clean)

    def set_tags(self, tags: List[str]):
        """Populate tags pill badges."""
        while self.tags_layout.count() > 0:
            item = self.tags_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

        if not tags:
            self.tags_container.setVisible(False)
            return

        self.tags_container.setVisible(True)
        for tag in tags[:6]:
            badge = QLabel(escape(str(tag).strip()))
            badge.setStyleSheet("""
                QLabel {
                    background: rgba(255, 255, 255, 0.07);
                    color: #98989D;
                    border: 1px solid rgba(255, 255, 255, 0.08);
                    border-radius: 6px;
                    padding: 3px 10px;
                    font-size: 11px;
                    font-weight: 500;
                }
            """)
            self.tags_layout.addWidget(badge)
        self.tags_layout.addStretch()

    def set_disk_size(self, size_bytes: int):
        self.lbl_disk_size.setText(format_size(size_bytes))

    def set_cloud_status(self, status, local_stats=None, cloud_stats=None):
        self._cloud_status = status
        ind = cloud_indicator(status)
        text = ind.label if hasattr(ind, "label") else str(status)
        self.lbl_cloud_status.setText(text)
        if hasattr(ind, "color"):
            self.lbl_cloud_status.setStyleSheet(f"color: {ind.color}; font-size: 12px; font-weight: 600; background: transparent;")

        metadata_stats = local_stats if status == SyncStatus.LOCAL_NEWER else cloud_stats
        if metadata_stats is not None and getattr(metadata_stats, "exists", False):
            saved_at = format_datetime_timestamp(
                getattr(metadata_stats, "last_modified", 0.0),
                "%H:%M",
                fallback="Unknown time",
            )
            device_name = escape(
                str(getattr(metadata_stats, "device_name", "") or "Unknown device")
            )
            self.lbl_cloud_metadata.setText(
                f"Saved: {escape(saved_at)} · Device: {device_name}"
            )
            self.lbl_cloud_metadata.setVisible(True)
        else:
            self.lbl_cloud_metadata.clear()
            self.lbl_cloud_metadata.setVisible(False)

        if status == SyncStatus.CLOUD_ONLY:
            self.btn_cloud_action.setText(" Restore Cloud Save")
            self.btn_cloud_action.setIcon(get_icon("ph.cloud-arrow-down-bold", color="#FFFFFF"))
            self.btn_cloud_action.setVisible(True)
        elif status == SyncStatus.LOCAL_NEWER:
            self.btn_cloud_action.setText(" Upload Local Save")
            self.btn_cloud_action.setIcon(get_icon("ph.cloud-arrow-up-bold", color="#FFFFFF"))
            self.btn_cloud_action.setVisible(True)
        elif status == SyncStatus.CONFLICT:
            self.btn_cloud_action.setText(" Resolve Conflict")
            self.btn_cloud_action.setIcon(get_icon("ph.warning-circle-bold", color="#FFFFFF"))
            self.btn_cloud_action.setVisible(True)
        else:
            self.btn_cloud_action.setVisible(False)

    def set_achievements(self, unlocked: int, total: int, badges: Optional[List[QPixmap]] = None):
        """Update achievements card with progress and preview badges."""
        if total <= 0:
            self.ach_card.setVisible(False)
            return

        self.ach_card.setVisible(True)
        pct = int((unlocked / total) * 100) if total > 0 else 0
        self.lbl_ach_ratio.setText(f"{unlocked} / {total} ({pct}%)")
        self.ach_progress.setValue(pct)

        while self.ach_badges_layout.count() > 0:
            item = self.ach_badges_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()

        if badges:
            for pix in badges[:6]:
                if pix and not pix.isNull():
                    lbl = QLabel()
                    lbl.setFixedSize(28, 28)
                    lbl.setScaledContents(True)
                    lbl.setPixmap(pix)
                    lbl.setStyleSheet("border-radius: 4px;")
                    self.ach_badges_layout.addWidget(lbl)
            self.ach_badges_layout.addStretch()

    def update_favorite(self, is_favorite: bool):
        self.is_favorite = is_favorite
        self._update_favorite_icon(is_favorite)

    def _update_favorite_icon(self, is_favorite: bool):
        if is_favorite:
            self.btn_fav.setIcon(get_icon("ph.heart-fill", color="#FF453A"))
        else:
            self.btn_fav.setIcon(get_icon("ph.heart-bold", color="#8E8E93"))
        self.btn_fav.setIconSize(QSize(16, 16))

    def _on_favorite_clicked(self):
        if self.current_game_id is not None:
            self.favorite_toggled.emit(self.current_game_id)

    def _on_launch_clicked(self):
        if self.current_game_id is not None:
            self.play_requested.emit(self.current_game_id)

    def _on_cloud_action_clicked(self):
        if self.current_game_id is not None and self._cloud_status is not None:
            action = "restore" if self._cloud_status == SyncStatus.CLOUD_ONLY else "upload"
            self.cloud_action_requested.emit(self.current_game_id, action)

    @staticmethod
    def _format_playtime(seconds: int) -> str:
        if not seconds or seconds < 60:
            return "Never played" if not seconds else f"{seconds}s"
        minutes = seconds // 60
        hours = minutes / 60.0
        if hours >= 1.0:
            return f"{hours:.1f} hours"
        return f"{minutes} mins"

    @staticmethod
    def _format_last_played(timestamp: float) -> str:
        if not timestamp or timestamp <= 0:
            return "Never"
        return format_timestamp(timestamp)
