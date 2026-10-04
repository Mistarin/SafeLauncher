from PyQt6.QtWidgets import QWidget, QVBoxLayout, QHBoxLayout, QPushButton, QGridLayout, QLabel, QScrollArea, QFrame, QProgressBar, QSizePolicy
from PyQt6.QtCore import Qt, QSize
from PyQt6.QtGui import QFont
from ui.icons import get_icon
from ui.components.loading_spinner import LoadingSpinner
from dataclasses import dataclass
from typing import Callable
@dataclass(frozen=True)
class GameInspectorActions:
    animate_left_panel: Callable
    on_edit: Callable
    on_launch: Callable
    on_remove: Callable
    open_achievements_dialog: Callable
    open_game_properties: Callable
    open_screenshot_gallery: Callable
    open_video_gallery: Callable
    restore_selected_game_cloud_save: Callable
    retry_steam_check: Callable


class GameInspectorWidget(QFrame):
    """Inspector controls and layout. Application workflows are injected actions."""
    def __init__(self, actions: GameInspectorActions, parent=None):
        super().__init__(parent)
        self.setObjectName("detailPanel")
        self.setMinimumWidth(260)
        self.setMaximumWidth(480)
        self.setStyleSheet("""
            QFrame#detailPanel {
                background-color: #18181B;
                border: none;
                border-left: 1px solid rgba(255, 255, 255, 0.06);
            }
            QLabel {
                color: #F4F4F5;
            }
        """)
        # Keep the inspector as a solid docked surface.  The library behind it
        # can remain translucent, but the right-side edit panel should not
        # reveal that background while it is opening or closing.
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, False)

        self.setVisible(False)

        # Internal scrollable container for seamless scaling
        panel_outer_layout = QVBoxLayout(self)
        panel_outer_layout.setContentsMargins(0, 0, 0, 0)
        panel_outer_layout.setSpacing(0)

        self.detail_scroll = QScrollArea()
        self.detail_scroll.setWidgetResizable(True)
        self.detail_scroll.setStyleSheet("QScrollArea { background: transparent; border: none; }")

        detail_content = QWidget()
        detail_content.setStyleSheet("background: transparent;")
        detail_layout = QVBoxLayout(detail_content)
        # Keep the glassmorphism breathing room balanced vertically.
        detail_layout.setContentsMargins(16, 16, 16, 16)
        detail_layout.setSpacing(10)
        detail_layout.setAlignment(Qt.AlignmentFlag.AlignTop)
        self.detail_scroll.setWidget(detail_content)
        panel_outer_layout.addWidget(self.detail_scroll)

        # Top Bar with Inspector Header and Close button
        top_bar = QHBoxLayout()
        top_bar.setContentsMargins(2, 0, 0, 2)

        lbl_inspector_hdr = QLabel("INSPECTOR")
        lbl_inspector_hdr.setStyleSheet("color: #71717A; font-size: 10px; font-weight: 700; letter-spacing: 0.8px; background: transparent;")
        top_bar.addWidget(lbl_inspector_hdr)
        top_bar.addStretch()

        self.btn_hide_detail = QPushButton()
        self.btn_hide_detail.setIcon(get_icon("ph.x-bold", color="#A1A1AA"))
        self.btn_hide_detail.setIconSize(QSize(12, 12))
        self.btn_hide_detail.setFixedSize(24, 24)
        self.btn_hide_detail.setToolTip("Close inspector")
        self.btn_hide_detail.setStyleSheet("""
            QPushButton {
                background: transparent;
                border: none;
                border-radius: 12px;
            }
            QPushButton:hover {
                background: #202024;
            }
        """)
        self.btn_hide_detail.clicked.connect(lambda: actions.animate_left_panel(False))
        top_bar.addWidget(self.btn_hide_detail)
        detail_layout.addLayout(top_bar)

        # Selected Game Cover Art Preview
        self.detail_cover = QLabel()
        self.detail_cover.setFixedSize(QSize(180, 270))
        self.detail_cover.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.detail_cover.setStyleSheet("""
            QLabel {
                border: 1px solid rgba(255, 255, 255, 0.08);
                border-radius: 12px;
                background-color: #202024;
            }
        """)

        cover_row = QHBoxLayout()
        cover_row.setContentsMargins(0, 2, 0, 4)
        cover_row.setAlignment(Qt.AlignmentFlag.AlignCenter)
        cover_row.addWidget(self.detail_cover)
        detail_layout.addLayout(cover_row)

        # Selected Game Title Header Row
        title_row = QHBoxLayout()
        title_row.setContentsMargins(0, 0, 0, 0)

        self.detail_title = QLabel("Select a Game")
        self.detail_title.setFont(QFont("Arial", 14, QFont.Weight.Bold))
        self.detail_title.setWordWrap(True)
        self.detail_title.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.detail_title.setStyleSheet("color: #FFFFFF; background: transparent; letter-spacing: -0.2px;")
        title_row.addWidget(self.detail_title, 1)

        detail_layout.addLayout(title_row)

        # Steam Tags Badge Container
        self.tags_widget = QWidget()
        self.tags_layout = QHBoxLayout(self.tags_widget)
        self.tags_layout.setContentsMargins(0, 0, 0, 0)
        self.tags_layout.setSpacing(6)
        self.tags_layout.setAlignment(Qt.AlignmentFlag.AlignCenter)
        detail_layout.addWidget(self.tags_widget)

        # ── Unified Game Specs Card (Playtime, Last Played, Size, Cloud Save) ──
        self.detail_spec_card = QFrame()
        self.detail_spec_card.setObjectName("detailSpecCard")
        self.detail_spec_card.setStyleSheet("""
            QFrame#detailSpecCard {
                background-color: #121214;
                border: 1px solid rgba(255, 255, 255, 0.05);
                border-radius: 10px;
            }
        """)
        spec_layout = QGridLayout(self.detail_spec_card)
        spec_layout.setContentsMargins(12, 10, 12, 10)
        spec_layout.setHorizontalSpacing(14)
        spec_layout.setVerticalSpacing(6)

        # Col 0: Playtime
        lbl_pt_h = QLabel("PLAYTIME")
        lbl_pt_h.setStyleSheet("color: #71717A; font-size: 9px; font-weight: 700; letter-spacing: 0.6px; background: transparent;")
        spec_layout.addWidget(lbl_pt_h, 0, 0)
        self.detail_playtime = QLabel("--")
        self.detail_playtime.setStyleSheet("color: #F4F4F5; font-size: 11px; font-weight: 600; background: transparent;")
        spec_layout.addWidget(self.detail_playtime, 1, 0)

        # Col 1: Last Played
        lbl_lp_h = QLabel("LAST PLAYED")
        lbl_lp_h.setStyleSheet("color: #71717A; font-size: 9px; font-weight: 700; letter-spacing: 0.6px; background: transparent;")
        spec_layout.addWidget(lbl_lp_h, 0, 1)
        self.detail_last_played = QLabel("--")
        self.detail_last_played.setStyleSheet("color: #F4F4F5; font-size: 11px; font-weight: 600; background: transparent;")
        spec_layout.addWidget(self.detail_last_played, 1, 1)

        # Row 2, Col 0: Disk Size
        lbl_ds_h = QLabel("DISK SIZE")
        lbl_ds_h.setStyleSheet("color: #71717A; font-size: 9px; font-weight: 700; letter-spacing: 0.6px; background: transparent;")
        spec_layout.addWidget(lbl_ds_h, 2, 0)
        self.detail_disk_size = QLabel("--")
        self.detail_disk_size.setStyleSheet("color: #A1A1AA; font-size: 11px; font-weight: 500; background: transparent;")
        spec_layout.addWidget(self.detail_disk_size, 3, 0)

        # Row 2, Col 1: Cloud Sync
        lbl_cs_h = QLabel("CLOUD SAVE")
        lbl_cs_h.setStyleSheet("color: #71717A; font-size: 9px; font-weight: 700; letter-spacing: 0.6px; background: transparent;")
        spec_layout.addWidget(lbl_cs_h, 2, 1)

        cloud_box = QWidget()
        cloud_box.setStyleSheet("background: transparent;")
        cloud_box_layout = QVBoxLayout(cloud_box)
        cloud_box_layout.setContentsMargins(0, 0, 0, 0)
        cloud_box_layout.setSpacing(2)

        cloud_status_row = QHBoxLayout()
        cloud_status_row.setContentsMargins(0, 0, 0, 0)
        cloud_status_row.setSpacing(6)

        self.detail_cloud_spinner = LoadingSpinner(cloud_box, size=14)
        cloud_status_row.addWidget(self.detail_cloud_spinner)
        self.detail_cloud_status = QLabel("--")
        self.detail_cloud_status.setStyleSheet("color: #A1A1AA; font-size: 11px; font-weight: 500; background: transparent;")
        cloud_status_row.addWidget(self.detail_cloud_status)

        self.btn_detail_cloud_restore = QPushButton("Restore latest cloud save")
        self.btn_detail_cloud_restore.setAccessibleName("Restore latest cloud save")
        self.btn_detail_cloud_restore.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_detail_cloud_restore.setToolTip("Restore latest cloud save for this game")
        self.btn_detail_cloud_restore.setStyleSheet(
            "QPushButton { background: #3B9FE8; color: #FFFFFF; border: none; border-radius: 4px; "
            "padding: 2px 8px; font-size: 10px; font-weight: bold; } "
            "QPushButton:hover { background: #3B9FE8; }"
        )
        self.btn_detail_cloud_restore.hide()
        self.btn_detail_cloud_restore.clicked.connect(actions.restore_selected_game_cloud_save)
        cloud_status_row.addWidget(self.btn_detail_cloud_restore)

        cloud_status_row.addStretch()
        cloud_box_layout.addLayout(cloud_status_row)

        self.detail_cloud_metadata = QLabel("")
        self.detail_cloud_metadata.setStyleSheet(
            "color: #71717A; font-size: 9px; font-weight: 500; background: transparent;"
        )
        self.detail_cloud_metadata.setAccessibleName("Cloud save time and device")
        self.detail_cloud_metadata.setVisible(False)
        cloud_box_layout.addWidget(self.detail_cloud_metadata)

        spec_layout.addWidget(cloud_box, 3, 1)

        detail_layout.addWidget(self.detail_spec_card)

        # Steam update status and version details
        self.detail_update_widget = QWidget()
        self.detail_update_widget.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Preferred)
        self.detail_update_layout = QVBoxLayout(self.detail_update_widget)
        self.detail_update_layout.setContentsMargins(0, 0, 0, 0)
        self.detail_update_layout.setSpacing(4)
        self.detail_update_layout.setAlignment(Qt.AlignmentFlag.AlignCenter)

        self.detail_update_spinner = LoadingSpinner(self.detail_update_widget, size=14)
        self.detail_update_layout.addWidget(self.detail_update_spinner, 0, Qt.AlignmentFlag.AlignCenter)

        self.lbl_detail_update = QLabel("")
        self.lbl_detail_update.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.lbl_detail_update.setFixedHeight(22)
        self.detail_update_layout.addWidget(self.lbl_detail_update)

        self.lbl_update_dates = QLabel("")
        self.lbl_update_dates.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.lbl_update_dates.setStyleSheet(
            "QLabel { color: #F4F4F5; background: transparent; "
            "font-size: 10px; padding: 0 4px; }"
        )
        self.lbl_update_dates.setVisible(False)
        self.detail_update_layout.addWidget(self.lbl_update_dates)

        self.lbl_detail_versions = QLabel("")
        self.lbl_detail_versions.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.lbl_detail_versions.setWordWrap(True)
        self.lbl_detail_versions.setOpenExternalLinks(True)
        self.lbl_detail_versions.setStyleSheet("QLabel { color: #A1A1AA; background: #18181B; border: 1px solid rgba(255, 255, 255, 0.06); border-radius: 6px; font-size: 10px; padding: 3px 8px; }")
        self.detail_update_layout.addWidget(self.lbl_detail_versions)

        self.btn_retry_steam = QPushButton("Retry")
        self.btn_retry_steam.setVisible(False)
        self.btn_retry_steam.setToolTip("Retry the Steam build check")
        self.btn_retry_steam.clicked.connect(actions.retry_steam_check)
        self.detail_update_layout.addWidget(self.btn_retry_steam)

        self.detail_update_widget.setVisible(False)
        detail_layout.addWidget(self.detail_update_widget)

        # Primary Launch Game Button
        detail_layout.addSpacing(2)
        self.btn_detail_launch = QPushButton("Launch Game")
        self.btn_detail_launch.setObjectName("detailLaunch")
        self.btn_detail_launch.setIcon(get_icon("ph.play-bold", color="#FFFFFF"))
        self.btn_detail_launch.setIconSize(QSize(15, 15))
        self.btn_detail_launch.setFixedHeight(40)
        self.btn_detail_launch.setStyleSheet("""
            QPushButton#detailLaunch {
                background-color: #3B9FE8;
                color: #FFFFFF;
                font-weight: 600;
                font-size: 13px;
                border: none;
                border-radius: 8px;
                padding: 0 16px;
                text-align: center;
                letter-spacing: 0.2px;
            }
            QPushButton#detailLaunch:hover {
                background-color: #55ACED;
            }
            QPushButton#detailLaunch:pressed {
                background-color: #2789D0;
            }
        """)
        self.btn_detail_launch.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_detail_launch.clicked.connect(actions.on_launch)
        detail_layout.addWidget(self.btn_detail_launch)

        sec_btn_style = """
            QPushButton {
                background-color: #18181B;
                color: #F4F4F5;
                border: 1px solid rgba(255, 255, 255, 0.06);
                border-radius: 8px;
                padding: 0 10px;
                font-weight: 500;
                font-size: 11px;
                text-align: center;
            }
            QPushButton:hover {
                background-color: #202024;
                border-color: rgba(255, 255, 255, 0.12);
                color: #FFFFFF;
            }
            QPushButton:pressed {
                background-color: #121214;
            }
        """

        # ── Secondary Action Buttons (2x2 Grid) ──
        actions_grid = QGridLayout()
        actions_grid.setContentsMargins(0, 0, 0, 0)
        actions_grid.setSpacing(6)

        self.btn_detail_edit = QPushButton("Edit Game")
        self.btn_detail_edit.setIcon(get_icon("ph.pencil-simple-bold", color="#3B9FE8"))
        self.btn_detail_edit.setIconSize(QSize(14, 14))
        self.btn_detail_edit.setFixedHeight(32)
        self.btn_detail_edit.setStyleSheet(sec_btn_style)
        self.btn_detail_edit.clicked.connect(actions.on_edit)
        actions_grid.addWidget(self.btn_detail_edit, 0, 0)

        self.btn_detail_properties = QPushButton("Properties")
        self.btn_detail_properties.setIcon(get_icon("ph.sliders-horizontal-bold", color="#A1A1AA"))
        self.btn_detail_properties.setIconSize(QSize(14, 14))
        self.btn_detail_properties.setFixedHeight(32)
        self.btn_detail_properties.setStyleSheet(sec_btn_style)
        self.btn_detail_properties.clicked.connect(actions.open_game_properties)
        actions_grid.addWidget(self.btn_detail_properties, 0, 1)

        self.btn_detail_screenshots = QPushButton("Screenshots")
        self.btn_detail_screenshots.setIcon(get_icon("ph.image-bold", color="#A1A1AA"))
        self.btn_detail_screenshots.setIconSize(QSize(14, 14))
        self.btn_detail_screenshots.setFixedHeight(32)
        self.btn_detail_screenshots.setStyleSheet(sec_btn_style)
        self.btn_detail_screenshots.clicked.connect(actions.open_screenshot_gallery)
        actions_grid.addWidget(self.btn_detail_screenshots, 1, 0)

        self.btn_detail_videos = QPushButton("Videos")
        self.btn_detail_videos.setIcon(get_icon("ph.video-camera-bold", color="#A1A1AA"))
        self.btn_detail_videos.setIconSize(QSize(14, 14))
        self.btn_detail_videos.setFixedHeight(32)
        self.btn_detail_videos.setStyleSheet(sec_btn_style)
        self.btn_detail_videos.clicked.connect(actions.open_video_gallery)
        actions_grid.addWidget(self.btn_detail_videos, 1, 1)

        detail_layout.addLayout(actions_grid)

        self.btn_detail_achievements = QPushButton("Achievements")
        self.btn_detail_achievements.setAccessibleName("Open achievements")
        self.btn_detail_achievements.setToolTip("Open the full achievement list for this game")
        self.btn_detail_achievements.setIcon(get_icon("ph.trophy-bold", color="#35C98A"))
        self.btn_detail_achievements.setIconSize(QSize(14, 14))
        self.btn_detail_achievements.setFixedHeight(32)
        self.btn_detail_achievements.setStyleSheet(sec_btn_style)
        self.btn_detail_achievements.clicked.connect(actions.open_achievements_dialog)
        self.btn_detail_achievements.setVisible(False)
        detail_layout.addWidget(self.btn_detail_achievements)

        # Apple-styled Achievement Preview Card in Inspector Detail Panel
        self.detail_ach_card = QFrame()
        self.detail_ach_card.setObjectName("detailAchCard")
        self.detail_ach_card.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Minimum)
        self.detail_ach_card.setStyleSheet("""
            QFrame#detailAchCard {
                background-color: #121214;
                border: 1px solid rgba(255, 255, 255, 0.05);
                border-radius: 10px;
                padding: 10px;
            }
            QFrame#detailAchCard:hover {
                background-color: #18181B;
                border-color: rgba(48, 209, 88, 0.3);
            }
        """)
        self.detail_ach_card.setCursor(Qt.CursorShape.PointingHandCursor)
        self.detail_ach_card.mousePressEvent = lambda e: actions.open_achievements_dialog()

        ach_card_layout = QVBoxLayout(self.detail_ach_card)
        ach_card_layout.setContentsMargins(10, 8, 10, 8)
        ach_card_layout.setSpacing(6)

        ach_hdr_row = QHBoxLayout()
        ach_hdr_row.setContentsMargins(0, 0, 0, 0)
        ach_hdr_row.setSpacing(6)

        ach_title = QLabel("ACHIEVEMENTS")
        ach_title.setStyleSheet("color: #71717A; font-size: 9px; font-weight: 700; letter-spacing: 0.8px; background: transparent;")
        ach_hdr_row.addWidget(ach_title)
        ach_hdr_row.addStretch()

        self.lbl_detail_ach_count = QLabel("0 / 0 (0%)")
        self.lbl_detail_ach_count.setStyleSheet("color: #35C98A; font-size: 11px; font-weight: 700; background: transparent;")
        ach_hdr_row.addWidget(self.lbl_detail_ach_count)
        ach_card_layout.addLayout(ach_hdr_row)

        self.detail_ach_progress = QProgressBar()
        self.detail_ach_progress.setFixedHeight(4)
        self.detail_ach_progress.setTextVisible(False)
        self.detail_ach_progress.setRange(0, 100)
        self.detail_ach_progress.setValue(0)
        self.detail_ach_progress.setStyleSheet("""
            QProgressBar {
                background-color: #202024;
                border: none;
                border-radius: 2px;
            }
            QProgressBar::chunk {
                background: qlineargradient(x1:0, y1:0, x2:1, y2:0, stop:0 #35C98A, stop:1 #35C98A);
                border-radius: 2px;
            }
        """)
        ach_card_layout.addWidget(self.detail_ach_progress)

        # Mini badge icons preview container
        self.detail_ach_badges_container = QWidget()
        self.detail_ach_badges_layout = QHBoxLayout(self.detail_ach_badges_container)
        self.detail_ach_badges_layout.setContentsMargins(0, 2, 0, 0)
        self.detail_ach_badges_layout.setSpacing(6)
        ach_card_layout.addWidget(self.detail_ach_badges_container)

        self.detail_ach_card.setVisible(False)
        detail_layout.addWidget(self.detail_ach_card)

        # Game lifecycle button: opens the shared uninstall/delete chooser.
        self.btn_detail_remove = QPushButton("Uninstall / Delete")
        self.btn_detail_remove.setIcon(get_icon("ph.trash-bold", color="#FF453A"))
        self.btn_detail_remove.setIconSize(QSize(13, 13))
        self.btn_detail_remove.setFixedHeight(28)
        self.btn_detail_remove.setStyleSheet("""
            QPushButton {
                background-color: transparent;
                color: #FF453A;
                border: none;
                border-radius: 6px;
                padding: 0 12px;
                font-weight: 500;
                font-size: 11px;
                text-align: center;
            }
            QPushButton:hover {
                background-color: rgba(255, 69, 58, 0.1);
                color: #FF6961;
            }
            QPushButton:pressed {
                background-color: rgba(255, 69, 58, 0.18);
            }
        """)
        self.btn_detail_remove.clicked.connect(actions.on_remove)
        detail_layout.addWidget(self.btn_detail_remove)

        detail_layout.addStretch()
