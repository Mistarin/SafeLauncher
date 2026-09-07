"""
Compact / Steam-styled Game Detail Page component for SafeLauncher.
Implements the modern Compact Library presentation:
- Cinematic Hero Artwork Header with gradient fades and game title
- Action / Play button
- Horizontal Stats Bar (Cloud Status, Last Played, Playtime, Achievements ratio)
- Quick Action Tools (Settings, Game Folder, Save Manager, Favorite)
- Sub-Navigation Bar (Properties, Saves, Prefix, Screenshots, Steam)
- Two-Column Lower Dashboard:
  - Left: Activity Timeline (recent unlocks, build/installation specs)
  - Right: Achievements Showcase Card + Persistent Game Notes Widget
"""

import os
import datetime
from typing import Optional, List, Dict, Any, Tuple

from PyQt6.QtCore import Qt, QSize, pyqtSignal, QSettings
from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QProgressBar, QTextEdit, QFrame, QScrollArea, QSizePolicy,
    QListWidget, QListWidgetItem, QLineEdit, QSplitter
)
from PyQt6.QtGui import (
    QPixmap, QColor, QPainter, QLinearGradient, QFont, QIcon, QPainterPath
)

from ui.icons import get_icon
from core.cloud_save_sync import SyncStatus
from core.logger import get_logger

logger = get_logger("CompactGamePage")


def _format_playtime_hours(seconds: Any) -> str:
    """Format playtime into clean hours/minutes representation."""
    try:
        sec = int(seconds or 0)
    except (ValueError, TypeError):
        sec = 0
    if sec < 60:
        return "0.0 h" if not sec else f"{sec} s"
    minutes = sec // 60
    hours = minutes / 60.0
    if hours >= 1.0:
        return f"{hours:.1f} h"
    return f"{minutes} m"


def _format_last_played_date(timestamp: Any) -> str:
    """Format last played timestamp into human-readable English text."""
    try:
        ts = float(timestamp or 0)
    except (ValueError, TypeError):
        ts = 0.0
    if ts <= 0:
        return "Never"
    
    dt = datetime.datetime.fromtimestamp(ts)
    now = datetime.datetime.now()
    diff = now - dt

    if diff.days == 0:
        return "Today"
    elif diff.days == 1:
        return "Yesterday"
    elif diff.days < 7:
        days = ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]
        return days[dt.weekday()]
    elif dt.year == now.year:
        return dt.strftime("%b %d")
    else:
        return dt.strftime("%b %d, %Y")


def _create_rounded_icon(pixmap: QPixmap, size: QSize, radius: int = 6) -> QPixmap:
    """Helper to return an antialiased rounded pixmap."""
    if not pixmap or pixmap.isNull():
        return QPixmap()
    scaled = pixmap.scaled(
        size,
        Qt.AspectRatioMode.KeepAspectRatioByExpanding,
        Qt.TransformationMode.SmoothTransformation
    )
    out = QPixmap(size)
    out.fill(Qt.GlobalColor.transparent)
    painter = QPainter(out)
    if not painter.isActive():
        return scaled
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
    path = QPainterPath()
    path.addRoundedRect(0, 0, size.width(), size.height(), radius, radius)
    painter.setClipPath(path)
    x = max(0, (scaled.width() - size.width()) // 2)
    y = max(0, (scaled.height() - size.height()) // 2)
    painter.drawPixmap(0, 0, scaled, x, y, size.width(), size.height())
    painter.end()
    return out


class CompactHeroBanner(QWidget):
    """
    Cinematic full-width hero header banner.
    Renders 16:9 hero background with bottom and vignette gradient blends,
    along with large game title typography and category pills.
    """
    def __init__(self, parent=None):
        super().__init__(parent)
        self.hero_pixmap: Optional[QPixmap] = None
        self.game_title: str = "SafeLauncher"
        self.tags: str = ""
        self.setFixedHeight(280)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)

    def set_hero_data(self, image_path: Optional[str], title: str, tags: str = ""):
        self.game_title = title
        self.tags = tags
        if image_path and os.path.isfile(image_path):
            pix = QPixmap(image_path)
            self.hero_pixmap = pix if not pix.isNull() else None
        else:
            self.hero_pixmap = None
        self.update()

    def paintEvent(self, event):
        w = self.width()
        h = self.height()
        if w <= 0 or h <= 0:
            return

        painter = QPainter(self)
        if not painter.isActive():
            return

        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)

        # Base neutral dark background
        painter.fillRect(0, 0, w, h, QColor(18, 18, 20))

        if self.hero_pixmap and not self.hero_pixmap.isNull():
            scaled = self.hero_pixmap.scaled(
                w, h,
                Qt.AspectRatioMode.KeepAspectRatioByExpanding,
                Qt.TransformationMode.SmoothTransformation
            )
            crop_x = max(0, (scaled.width() - w) // 2)
            crop_y = max(0, (scaled.height() - h) // 4)
            painter.drawPixmap(0, 0, scaled, crop_x, crop_y, w, h)

        # Dark overlay gradients:
        # 1. Subtle top vignette
        top_grad = QLinearGradient(0, 0, 0, 80)
        top_grad.setColorAt(0.0, QColor(18, 18, 20, 190))
        top_grad.setColorAt(1.0, QColor(18, 18, 20, 0))
        painter.fillRect(0, 0, w, 80, top_grad)

        # 2. Bottom blend into action bar
        bottom_grad = QLinearGradient(0, h - 140, 0, h)
        bottom_grad.setColorAt(0.0, QColor(18, 18, 20, 0))
        bottom_grad.setColorAt(0.65, QColor(18, 18, 20, 210))
        bottom_grad.setColorAt(1.0, QColor(18, 18, 20, 255))
        painter.fillRect(0, h - 140, w, 140, bottom_grad)

        # Left shadow vignette for readable title text
        left_grad = QLinearGradient(0, 0, 500, 0)
        left_grad.setColorAt(0.0, QColor(18, 18, 20, 160))
        left_grad.setColorAt(1.0, QColor(18, 18, 20, 0))
        painter.fillRect(0, 0, 500, h, left_grad)

        # Draw Game Title with shadow
        painter.setPen(QColor(0, 0, 0, 180))
        title_font = QFont("Arial", 26, QFont.Weight.Bold)
        painter.setFont(title_font)
        text_rect = self.rect().adjusted(32, h - 82, -32, -18)
        painter.drawText(text_rect.adjusted(2, 2, 2, 2), Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, self.game_title)

        painter.setPen(QColor(255, 255, 255))
        painter.drawText(text_rect, Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter, self.game_title)

        painter.end()


class CompactActionBar(QFrame):
    """
    Action Play button, stats metrics columns (Cloud, Last Played, Playtime, Achievements),
    and quick action tools (Settings, Folder, Save Manager, Favorite).
    """
    play_clicked = pyqtSignal()
    settings_clicked = pyqtSignal()
    folder_clicked = pyqtSignal()
    save_manager_clicked = pyqtSignal()
    favorite_clicked = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("compactActionBar")
        self.setFixedHeight(72)
        self.setStyleSheet("""
            QFrame#compactActionBar {
                background-color: #161618;
                border-top: 1px solid rgba(255, 255, 255, 0.06);
                border-bottom: 1px solid rgba(255, 255, 255, 0.06);
            }
        """)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(32, 10, 32, 10)
        layout.setSpacing(28)

        # ── 1. Action Play Button ──
        self.btn_play = QPushButton("  PLAY")
        self.btn_play.setIcon(get_icon("fa5s.play", color="#FFFFFF"))
        self.btn_play.setIconSize(QSize(16, 16))
        self.btn_play.setFixedSize(140, 46)
        self.btn_play.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_play.setFont(QFont("Arial", 13, QFont.Weight.Bold))
        self.btn_play.setStyleSheet("""
            QPushButton {
                background: qlineargradient(x1:0, y1:0, x2:1, y2:0, stop:0 #3CD070, stop:1 #28A745);
                color: #FFFFFF;
                border: none;
                border-radius: 6px;
                padding: 0 16px;
                font-weight: 800;
                letter-spacing: 0.5px;
            }
            QPushButton:hover {
                background: qlineargradient(x1:0, y1:0, x2:1, y2:0, stop:0 #4AE080, stop:1 #32B852);
            }
            QPushButton:pressed {
                background: #238A3A;
            }
            QPushButton:disabled {
                background: #323236;
                color: #71717A;
            }
        """)
        self.btn_play.clicked.connect(self.play_clicked.emit)
        layout.addWidget(self.btn_play)

        # ── 2. Horizontal Stats Columns with Vertical Dividers ──
        # Column 1: Cloud Status
        self.cloud_container = QVBoxLayout()
        self.cloud_container.setSpacing(2)
        lbl_cloud_title = QLabel("CLOUD STATUS")
        lbl_cloud_title.setStyleSheet("color: #71717A; font-size: 10px; font-weight: 700; letter-spacing: 0.6px; background: transparent;")
        self.cloud_container.addWidget(lbl_cloud_title)

        cloud_row = QHBoxLayout()
        cloud_row.setSpacing(6)
        self.cloud_icon_lbl = QLabel()
        self.cloud_icon_lbl.setPixmap(get_icon("ph.cloud-check-fill", color="#3CD070").pixmap(14, 14))
        self.cloud_icon_lbl.setStyleSheet("background: transparent;")
        cloud_row.addWidget(self.cloud_icon_lbl)

        self.cloud_text_lbl = QLabel("Up to date")
        self.cloud_text_lbl.setStyleSheet("color: #F4F4F5; font-size: 12px; font-weight: 600; background: transparent;")
        cloud_row.addWidget(self.cloud_text_lbl)
        self.cloud_container.addLayout(cloud_row)
        layout.addLayout(self.cloud_container)

        layout.addWidget(self._create_divider())

        # Column 2: Last Played
        self.last_played_container = QVBoxLayout()
        self.last_played_container.setSpacing(2)
        lbl_last_title = QLabel("LAST PLAYED")
        lbl_last_title.setStyleSheet("color: #71717A; font-size: 10px; font-weight: 700; letter-spacing: 0.6px; background: transparent;")
        self.last_played_container.addWidget(lbl_last_title)

        self.last_played_val = QLabel("Today")
        self.last_played_val.setStyleSheet("color: #F4F4F5; font-size: 12px; font-weight: 600; background: transparent;")
        self.last_played_container.addWidget(self.last_played_val)
        layout.addLayout(self.last_played_container)

        layout.addWidget(self._create_divider())

        # Column 3: Play Time
        self.playtime_container = QVBoxLayout()
        self.playtime_container.setSpacing(2)
        lbl_playtime_title = QLabel("PLAY TIME")
        lbl_playtime_title.setStyleSheet("color: #71717A; font-size: 10px; font-weight: 700; letter-spacing: 0.6px; background: transparent;")
        self.playtime_container.addWidget(lbl_playtime_title)

        self.playtime_val = QLabel("0.0 h")
        self.playtime_val.setStyleSheet("color: #F4F4F5; font-size: 12px; font-weight: 600; background: transparent;")
        self.playtime_container.addWidget(self.playtime_val)
        layout.addLayout(self.playtime_container)

        layout.addWidget(self._create_divider())

        # Column 4: Achievements Progress Summary
        self.ach_container = QVBoxLayout()
        self.ach_container.setSpacing(2)
        lbl_ach_title = QLabel("ACHIEVEMENTS")
        lbl_ach_title.setStyleSheet("color: #71717A; font-size: 10px; font-weight: 700; letter-spacing: 0.6px; background: transparent;")
        self.ach_container.addWidget(lbl_ach_title)

        ach_sub_row = QHBoxLayout()
        ach_sub_row.setSpacing(6)
        self.ach_ratio_lbl = QLabel("0 / 0")
        self.ach_ratio_lbl.setStyleSheet("color: #F4F4F5; font-size: 12px; font-weight: 600; background: transparent;")
        ach_sub_row.addWidget(self.ach_ratio_lbl)

        self.ach_mini_progress = QProgressBar()
        self.ach_mini_progress.setFixedSize(80, 5)
        self.ach_mini_progress.setTextVisible(False)
        self.ach_mini_progress.setValue(0)
        self.ach_mini_progress.setStyleSheet("""
            QProgressBar {
                background-color: #242428;
                border: none;
                border-radius: 2px;
            }
            QProgressBar::chunk {
                background: #3B9FE8;
                border-radius: 2px;
            }
        """)
        ach_sub_row.addWidget(self.ach_mini_progress)
        self.ach_container.addLayout(ach_sub_row)
        layout.addLayout(self.ach_container)

        layout.addStretch()

        # ── 3. Quick Action Tool Buttons ──
        quick_btn_style = """
            QPushButton {
                background: #202024;
                border: 1px solid rgba(255, 255, 255, 0.08);
                border-radius: 6px;
            }
            QPushButton:hover {
                background: #28282E;
                border-color: rgba(255, 255, 255, 0.18);
            }
            QPushButton:pressed {
                background: #18181B;
            }
        """

        self.btn_settings = QPushButton()
        self.btn_settings.setIcon(get_icon("ph.gear-six-bold", color="#A1A1AA"))
        self.btn_settings.setIconSize(QSize(16, 16))
        self.btn_settings.setFixedSize(36, 36)
        self.btn_settings.setToolTip("Game properties")
        self.btn_settings.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_settings.setStyleSheet(quick_btn_style)
        self.btn_settings.clicked.connect(self.settings_clicked.emit)
        layout.addWidget(self.btn_settings)

        self.btn_folder = QPushButton()
        self.btn_folder.setIcon(get_icon("ph.folder-open-bold", color="#A1A1AA"))
        self.btn_folder.setIconSize(QSize(16, 16))
        self.btn_folder.setFixedSize(36, 36)
        self.btn_folder.setToolTip("Open game folder")
        self.btn_folder.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_folder.setStyleSheet(quick_btn_style)
        self.btn_folder.clicked.connect(self.folder_clicked.emit)
        layout.addWidget(self.btn_folder)

        self.btn_save = QPushButton()
        self.btn_save.setIcon(get_icon("ph.cloud-bold", color="#A1A1AA"))
        self.btn_save.setIconSize(QSize(16, 16))
        self.btn_save.setFixedSize(36, 36)
        self.btn_save.setToolTip("Save manager & backups")
        self.btn_save.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_save.setStyleSheet(quick_btn_style)
        self.btn_save.clicked.connect(self.save_manager_clicked.emit)
        layout.addWidget(self.btn_save)

        self.btn_fav = QPushButton()
        self.btn_fav.setIcon(get_icon("ph.heart-bold", color="#A1A1AA"))
        self.btn_fav.setIconSize(QSize(16, 16))
        self.btn_fav.setFixedSize(36, 36)
        self.btn_fav.setToolTip("Add to favorites")
        self.btn_fav.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_fav.setStyleSheet(quick_btn_style)
        self.btn_fav.clicked.connect(self.favorite_clicked.emit)
        layout.addWidget(self.btn_fav)

    def _create_divider(self) -> QFrame:
        div = QFrame()
        div.setFrameShape(QFrame.Shape.VLine)
        div.setFrameShadow(QFrame.Shadow.Plain)
        div.setFixedWidth(1)
        div.setFixedHeight(32)
        div.setStyleSheet("background-color: rgba(255, 255, 255, 0.08); border: none;")
        return div

    def update_cloud_status(self, status: Any):
        """Update the cloud icon and text based on SyncStatus."""
        if status == SyncStatus.IN_SYNC:
            self.cloud_icon_lbl.setPixmap(get_icon("ph.cloud-check-fill", color="#3CD070").pixmap(14, 14))
            self.cloud_text_lbl.setText("Up to date")
            self.cloud_text_lbl.setStyleSheet("color: #3CD070; font-size: 12px; font-weight: 600; background: transparent;")
        elif status == SyncStatus.LOCAL_NEWER:
            self.cloud_icon_lbl.setPixmap(get_icon("ph.cloud-arrow-up-fill", color="#3B9FE8").pixmap(14, 14))
            self.cloud_text_lbl.setText("Ready to upload")
            self.cloud_text_lbl.setStyleSheet("color: #3B9FE8; font-size: 12px; font-weight: 600; background: transparent;")
        elif status == SyncStatus.CLOUD_NEWER:
            self.cloud_icon_lbl.setPixmap(get_icon("ph.cloud-arrow-down-fill", color="#FF9F0A").pixmap(14, 14))
            self.cloud_text_lbl.setText("Newer in cloud")
            self.cloud_text_lbl.setStyleSheet("color: #FF9F0A; font-size: 12px; font-weight: 600; background: transparent;")
        elif status == SyncStatus.CONFLICT:
            self.cloud_icon_lbl.setPixmap(get_icon("ph.cloud-warning-fill", color="#FF453A").pixmap(14, 14))
            self.cloud_text_lbl.setText("Cloud conflict")
            self.cloud_text_lbl.setStyleSheet("color: #FF453A; font-size: 12px; font-weight: 600; background: transparent;")
        elif status == SyncStatus.CLOUD_OFFLINE:
            self.cloud_icon_lbl.setPixmap(get_icon("ph.cloud-slash-fill", color="#71717A").pixmap(14, 14))
            self.cloud_text_lbl.setText("Cloud offline")
            self.cloud_text_lbl.setStyleSheet("color: #71717A; font-size: 12px; font-weight: 600; background: transparent;")
        else:
            self.cloud_icon_lbl.setPixmap(get_icon("ph.cloud-bold", color="#71717A").pixmap(14, 14))
            self.cloud_text_lbl.setText("No cloud saves")
            self.cloud_text_lbl.setStyleSheet("color: #71717A; font-size: 12px; font-weight: 600; background: transparent;")

    def set_favorite_active(self, is_fav: bool):
        if is_fav:
            self.btn_fav.setIcon(get_icon("ph.heart-fill", color="#FF453A"))
            self.btn_fav.setToolTip("Remove from favorites")
        else:
            self.btn_fav.setIcon(get_icon("ph.heart-bold", color="#A1A1AA"))
            self.btn_fav.setToolTip("Add to favorites")


class CompactSubNavBar(QFrame):
    """
    Sub-navigation links strip:
    Game Properties | Save Manager | Game Folder | Wine Prefix | Screenshots | Steam Page
    """
    properties_clicked = pyqtSignal()
    save_manager_clicked = pyqtSignal()
    open_folder_clicked = pyqtSignal()
    prefix_clicked = pyqtSignal()
    screenshots_clicked = pyqtSignal()
    steam_page_clicked = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("compactSubNavBar")
        self.setFixedHeight(40)
        self.setStyleSheet("""
            QFrame#compactSubNavBar {
                background-color: #161618;
                border-bottom: 1px solid rgba(255, 255, 255, 0.06);
            }
        """)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(32, 0, 32, 0)
        layout.setSpacing(20)

        nav_style = """
            QPushButton {
                background: transparent;
                color: #A1A1AA;
                border: none;
                font-size: 12px;
                font-weight: 600;
                padding: 4px 0;
            }
            QPushButton:hover {
                color: #FFFFFF;
            }
            QPushButton:pressed {
                color: #3B9FE8;
            }
        """

        self.btn_prop = QPushButton("Game Properties")
        self.btn_prop.setStyleSheet(nav_style)
        self.btn_prop.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_prop.clicked.connect(self.properties_clicked.emit)
        layout.addWidget(self.btn_prop)

        self.btn_save = QPushButton("Save Manager")
        self.btn_save.setStyleSheet(nav_style)
        self.btn_save.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_save.clicked.connect(self.save_manager_clicked.emit)
        layout.addWidget(self.btn_save)

        self.btn_folder = QPushButton("Game Folder")
        self.btn_folder.setStyleSheet(nav_style)
        self.btn_folder.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_folder.clicked.connect(self.open_folder_clicked.emit)
        layout.addWidget(self.btn_folder)

        self.btn_prefix = QPushButton("Wine Prefix")
        self.btn_prefix.setStyleSheet(nav_style)
        self.btn_prefix.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_prefix.clicked.connect(self.prefix_clicked.emit)
        layout.addWidget(self.btn_prefix)

        self.btn_shots = QPushButton("Screenshots")
        self.btn_shots.setStyleSheet(nav_style)
        self.btn_shots.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_shots.clicked.connect(self.screenshots_clicked.emit)
        layout.addWidget(self.btn_shots)

        self.btn_steam = QPushButton("Steam Page")
        self.btn_steam.setStyleSheet(nav_style)
        self.btn_steam.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_steam.clicked.connect(self.steam_page_clicked.emit)
        layout.addWidget(self.btn_steam)

        layout.addStretch()


class CompactActivityTimelineCard(QFrame):
    """Card displaying recent achievement unlocks or game updates."""
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setStyleSheet("""
            QFrame {
                background-color: #18181B;
                border: 1px solid rgba(255, 255, 255, 0.06);
                border-radius: 8px;
            }
        """)
        self.vbox = QVBoxLayout(self)
        self.vbox.setContentsMargins(16, 14, 16, 14)
        self.vbox.setSpacing(12)

        # Header
        hdr = QHBoxLayout()
        hdr.setSpacing(8)
        self.hdr_title = QLabel("ACTIVITY")
        self.hdr_title.setStyleSheet("color: #71717A; font-size: 11px; font-weight: 700; letter-spacing: 0.8px; background: transparent;")
        hdr.addWidget(self.hdr_title)
        hdr.addStretch()
        self.vbox.addLayout(hdr)

        # Activity Content Layout
        self.items_layout = QVBoxLayout()
        self.items_layout.setSpacing(10)
        self.vbox.addLayout(self.items_layout)

    def set_recent_achievements(self, achievements: List[dict]):
        # Clear existing items
        while self.items_layout.count():
            item = self.items_layout.takeAt(0)
            w = item.widget()
            if w:
                w.deleteLater()

        if not achievements:
            empty_lbl = QLabel("No recent activity. Launch the game to unlock achievements.")
            empty_lbl.setStyleSheet("color: #71717A; font-size: 12px; font-style: italic; background: transparent; padding: 12px 0;")
            self.items_layout.addWidget(empty_lbl)
            return

        for ach in achievements[:4]:
            row = QFrame()
            row.setStyleSheet("""
                QFrame {
                    background-color: rgba(255, 255, 255, 0.03);
                    border: 1px solid rgba(255, 255, 255, 0.05);
                    border-radius: 6px;
                    padding: 4px;
                }
            """)
            r_layout = QHBoxLayout(row)
            r_layout.setContentsMargins(10, 8, 10, 8)
            r_layout.setSpacing(12)

            icon_lbl = QLabel()
            icon_lbl.setFixedSize(48, 48)
            icon_lbl.setStyleSheet("background-color: #202024; border-radius: 6px; border: 1px solid rgba(255, 255, 255, 0.08);")
            icon_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)

            icon_path = ach.get("icon_path", "")
            if icon_path and os.path.isfile(icon_path):
                pix = QPixmap(icon_path)
                if not pix.isNull():
                    icon_lbl.setPixmap(_create_rounded_icon(pix, QSize(48, 48), radius=6))
            else:
                icon_lbl.setPixmap(get_icon("ph.trophy-fill", color="#FFD60A").pixmap(24, 24))

            r_layout.addWidget(icon_lbl)

            info_vbox = QVBoxLayout()
            info_vbox.setSpacing(2)
            title = QLabel(ach.get("display_name") or ach.get("api_name") or "Achievement")
            title.setStyleSheet("color: #FFFFFF; font-size: 13px; font-weight: 700; background: transparent;")
            info_vbox.addWidget(title)

            desc = QLabel(ach.get("description") or "Hidden achievement unlocked.")
            desc.setStyleSheet("color: #A1A1AA; font-size: 11px; background: transparent;")
            desc.setWordWrap(True)
            info_vbox.addWidget(desc)

            unlock_ts = ach.get("unlock_time", 0)
            if unlock_ts > 0:
                dt = datetime.datetime.fromtimestamp(unlock_ts)
                time_str = dt.strftime("Unlocked %b %d, %Y at %H:%M")
                time_lbl = QLabel(time_str)
                time_lbl.setStyleSheet("color: #71717A; font-size: 10px; font-weight: 500; background: transparent;")
                info_vbox.addWidget(time_lbl)

            r_layout.addLayout(info_vbox, 1)
            self.items_layout.addWidget(row)


class CompactAchievementsShowcaseWidget(QFrame):
    """
    Achievements Showcase widget:
    - Unlocked ratio and percentage
    - Subtle progress bar
    - Most recently unlocked achievement highlight
    - Locked achievements preview
    - 'View All Achievements' button
    """
    view_all_clicked = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setStyleSheet("""
            QFrame {
                background-color: #18181B;
                border: 1px solid rgba(255, 255, 255, 0.06);
                border-radius: 8px;
            }
        """)
        self.vbox = QVBoxLayout(self)
        self.vbox.setContentsMargins(16, 14, 16, 14)
        self.vbox.setSpacing(12)

        # Header Row
        hdr = QHBoxLayout()
        hdr.setSpacing(8)
        lbl_ach = QLabel("ACHIEVEMENTS")
        lbl_ach.setStyleSheet("color: #71717A; font-size: 11px; font-weight: 700; letter-spacing: 0.8px; background: transparent;")
        hdr.addWidget(lbl_ach)
        hdr.addStretch()

        self.ratio_lbl = QLabel("0 / 0")
        self.ratio_lbl.setStyleSheet("color: #A1A1AA; font-size: 11px; font-weight: 600; background: transparent;")
        hdr.addWidget(self.ratio_lbl)
        self.vbox.addLayout(hdr)

        # Progress bar
        self.progress = QProgressBar()
        self.progress.setFixedHeight(6)
        self.progress.setTextVisible(False)
        self.progress.setValue(0)
        self.progress.setStyleSheet("""
            QProgressBar {
                background-color: #242428;
                border: none;
                border-radius: 3px;
            }
            QProgressBar::chunk {
                background: qlineargradient(x1:0, y1:0, x2:1, y2:0, stop:0 #3B9FE8, stop:1 #2789D0);
                border-radius: 3px;
            }
        """)
        self.vbox.addWidget(self.progress)

        # Most recent unlock container
        self.recent_card = QFrame()
        self.recent_card.setStyleSheet("""
            QFrame {
                background-color: rgba(255, 255, 255, 0.03);
                border: 1px solid rgba(255, 255, 255, 0.05);
                border-radius: 6px;
            }
        """)
        rc_layout = QHBoxLayout(self.recent_card)
        rc_layout.setContentsMargins(10, 8, 10, 8)
        rc_layout.setSpacing(10)

        self.recent_icon = QLabel()
        self.recent_icon.setFixedSize(42, 42)
        self.recent_icon.setStyleSheet("background-color: #202024; border-radius: 4px;")
        self.recent_icon.setAlignment(Qt.AlignmentFlag.AlignCenter)
        rc_layout.addWidget(self.recent_icon)

        rc_text = QVBoxLayout()
        rc_text.setSpacing(2)
        self.recent_title = QLabel("No achievements unlocked yet")
        self.recent_title.setStyleSheet("color: #FFFFFF; font-size: 12px; font-weight: 700; background: transparent;")
        rc_text.addWidget(self.recent_title)

        self.recent_desc = QLabel("Keep playing to unlock your first achievements!")
        self.recent_desc.setStyleSheet("color: #A1A1AA; font-size: 11px; background: transparent;")
        rc_text.addWidget(self.recent_desc)
        rc_layout.addLayout(rc_text, 1)

        self.vbox.addWidget(self.recent_card)

        # Locked Thumbnails Row
        self.thumbs_row = QHBoxLayout()
        self.thumbs_row.setSpacing(8)
        self.vbox.addLayout(self.thumbs_row)

        # View All Button
        self.btn_view_all = QPushButton("View All Achievements")
        self.btn_view_all.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_view_all.setFixedHeight(30)
        self.btn_view_all.setStyleSheet("""
            QPushButton {
                background-color: #242428;
                color: #D4D4D8;
                border: 1px solid rgba(255, 255, 255, 0.06);
                border-radius: 4px;
                font-size: 11px;
                font-weight: 600;
            }
            QPushButton:hover {
                background-color: #2E2E36;
                color: #FFFFFF;
                border-color: rgba(255, 255, 255, 0.14);
            }
            QPushButton:pressed {
                background-color: #1C1C20;
            }
        """)
        self.btn_view_all.clicked.connect(self.view_all_clicked.emit)
        self.vbox.addWidget(self.btn_view_all)

    def set_achievements_data(self, unlocked: int, total: int, percentage: float, recent_unlocked: List[dict], locked_sample: List[dict]):
        self.ratio_lbl.setText(f"{unlocked} / {total} ({percentage:.0f} %)")
        self.progress.setValue(int(percentage))

        if recent_unlocked:
            first = recent_unlocked[0]
            self.recent_title.setText(first.get("display_name") or first.get("api_name") or "Achievement")
            self.recent_desc.setText(first.get("description") or "Unlocked achievement")
            icon_path = first.get("icon_path", "")
            if icon_path and os.path.isfile(icon_path):
                pix = QPixmap(icon_path)
                if not pix.isNull():
                    self.recent_icon.setPixmap(_create_rounded_icon(pix, QSize(42, 42), radius=4))
                else:
                    self.recent_icon.setPixmap(get_icon("ph.trophy-fill", color="#FFD60A").pixmap(20, 20))
            else:
                self.recent_icon.setPixmap(get_icon("ph.trophy-fill", color="#FFD60A").pixmap(20, 20))
            self.recent_card.setVisible(True)
        else:
            self.recent_title.setText("No achievements unlocked yet")
            self.recent_desc.setText("Keep playing to unlock your first achievements!")
            self.recent_icon.setPixmap(get_icon("ph.trophy-bold", color="#71717A").pixmap(20, 20))

        # Re-populate locked thumbnails
        while self.thumbs_row.count():
            item = self.thumbs_row.takeAt(0)
            w = item.widget()
            if w:
                w.deleteLater()

        for ach in locked_sample[:5]:
            thumb = QLabel()
            thumb.setFixedSize(36, 36)
            thumb.setStyleSheet("background-color: #202024; border-radius: 4px; border: 1px solid rgba(255, 255, 255, 0.06);")
            thumb.setAlignment(Qt.AlignmentFlag.AlignCenter)
            icongray = ach.get("icongray_path", "")
            if icongray and os.path.isfile(icongray):
                p = QPixmap(icongray)
                if not p.isNull():
                    thumb.setPixmap(_create_rounded_icon(p, QSize(36, 36), radius=4))
                else:
                    thumb.setPixmap(get_icon("ph.lock-simple-bold", color="#71717A").pixmap(16, 16))
            else:
                thumb.setPixmap(get_icon("ph.lock-simple-bold", color="#71717A").pixmap(16, 16))
            thumb.setToolTip(ach.get("display_name") or "Locked achievement")
            self.thumbs_row.addWidget(thumb)

        if total > 5:
            remaining = total - unlocked - 5
            if remaining > 0:
                more_lbl = QLabel(f"+{remaining}")
                more_lbl.setStyleSheet("color: #71717A; font-size: 11px; font-weight: 700; background: transparent; padding-left: 4px;")
                self.thumbs_row.addWidget(more_lbl)

        self.thumbs_row.addStretch()


class CompactNotesWidget(QFrame):
    """Interactive Game Notes widget persisted in QSettings per game ID."""
    def __init__(self, parent=None):
        super().__init__(parent)
        self.current_game_id: Optional[int] = None
        self.settings = QSettings("SafeLauncher", "SafeLauncher")
        self.setStyleSheet("""
            QFrame {
                background-color: #18181B;
                border: 1px solid rgba(255, 255, 255, 0.06);
                border-radius: 8px;
            }
        """)
        vbox = QVBoxLayout(self)
        vbox.setContentsMargins(16, 14, 16, 14)
        vbox.setSpacing(8)

        hdr = QHBoxLayout()
        hdr.setSpacing(6)
        title = QLabel("NOTES")
        title.setStyleSheet("color: #71717A; font-size: 11px; font-weight: 700; letter-spacing: 0.8px; background: transparent;")
        hdr.addWidget(title)
        hdr.addStretch()

        self.lbl_status = QLabel("Saved")
        self.lbl_status.setStyleSheet("color: #71717A; font-size: 10px; font-weight: 500; background: transparent;")
        hdr.addWidget(self.lbl_status)
        vbox.addLayout(hdr)

        self.text_edit = QTextEdit()
        self.text_edit.setPlaceholderText("Take personal notes, tips, or guides for this game...")
        self.text_edit.setFixedHeight(120)
        self.text_edit.setStyleSheet("""
            QTextEdit {
                background-color: rgba(255, 255, 255, 0.03);
                border: 1px solid rgba(255, 255, 255, 0.06);
                border-radius: 6px;
                color: #E4E4E7;
                font-size: 12px;
                padding: 8px;
            }
            QTextEdit:focus {
                background-color: rgba(255, 255, 255, 0.05);
                border: 1px solid #3B9FE8;
            }
        """)
        self.text_edit.textChanged.connect(self._on_text_changed)
        vbox.addWidget(self.text_edit)

    def load_notes_for_game(self, game_id: Optional[int]):
        self.current_game_id = game_id
        if not game_id:
            self.text_edit.blockSignals(True)
            self.text_edit.setPlainText("")
            self.text_edit.blockSignals(False)
            self.setEnabled(False)
            return

        self.setEnabled(True)
        saved = self.settings.value(f"game_notes/{game_id}", "", type=str)
        self.text_edit.blockSignals(True)
        self.text_edit.setPlainText(saved)
        self.text_edit.blockSignals(False)
        self.lbl_status.setText("Saved")

    def _on_text_changed(self):
        if not self.current_game_id:
            return
        content = self.text_edit.toPlainText()
        self.settings.setValue(f"game_notes/{self.current_game_id}", content)
        self.lbl_status.setText("Auto-saved")


class CompactGamePageWidget(QWidget):
    """
    Complete Compact Game Detail View.
    Integrates Hero Banner, Action Bar, Sub-Navigation, Activity Feed,
    Achievements Showcase, and Notes.
    """
    play_requested = pyqtSignal(int)
    properties_requested = pyqtSignal(int)
    save_manager_requested = pyqtSignal(int)
    open_folder_requested = pyqtSignal(int)
    prefix_maintenance_requested = pyqtSignal(int)
    favorite_toggled = pyqtSignal(int)
    achievements_requested = pyqtSignal(int)
    steam_page_requested = pyqtSignal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.current_game_id: Optional[int] = None
        self.current_game_record: Any = None
        self.current_steam_id: str = ""

        self.setStyleSheet("""
            QWidget {
                background-color: #121214;
                color: #FFFFFF;
            }
        """)

        # Main scrollable layout
        outer_layout = QVBoxLayout(self)
        outer_layout.setContentsMargins(0, 0, 0, 0)
        outer_layout.setSpacing(0)

        self.scroll_area = QScrollArea(self)
        self.scroll_area.setWidgetResizable(True)
        self.scroll_area.setFrameShape(QFrame.Shape.NoFrame)
        self.scroll_area.setStyleSheet("""
            QScrollArea {
                background: #121214;
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

        content_widget = QWidget()
        content_widget.setStyleSheet("background-color: #121214;")
        self.content_layout = QVBoxLayout(content_widget)
        self.content_layout.setContentsMargins(0, 0, 0, 32)
        self.content_layout.setSpacing(0)

        # 1. Cinematic Hero Banner
        self.hero_banner = CompactHeroBanner(content_widget)
        self.content_layout.addWidget(self.hero_banner)

        # 2. Action & Stats Bar
        self.action_bar = CompactActionBar(content_widget)
        self.action_bar.play_clicked.connect(self._on_play)
        self.action_bar.settings_clicked.connect(self._on_settings)
        self.action_bar.folder_clicked.connect(self._on_folder)
        self.action_bar.save_manager_clicked.connect(self._on_save_manager)
        self.action_bar.favorite_clicked.connect(self._on_favorite)
        self.content_layout.addWidget(self.action_bar)

        # 3. Sub-Navigation Bar
        self.sub_nav = CompactSubNavBar(content_widget)
        self.sub_nav.properties_clicked.connect(self._on_settings)
        self.sub_nav.save_manager_clicked.connect(self._on_save_manager)
        self.sub_nav.open_folder_clicked.connect(self._on_folder)
        self.sub_nav.prefix_clicked.connect(self._on_prefix)
        self.sub_nav.screenshots_clicked.connect(self._on_screenshots)
        self.sub_nav.steam_page_clicked.connect(self._on_steam_page)
        self.content_layout.addWidget(self.sub_nav)

        # 4. Two-Column Lower Dashboard
        dashboard_row = QHBoxLayout()
        dashboard_row.setContentsMargins(32, 24, 32, 0)
        dashboard_row.setSpacing(24)
        dashboard_row.setAlignment(Qt.AlignmentFlag.AlignTop)

        # Left Column (Activity & Details) - 62% width
        left_col = QVBoxLayout()
        left_col.setSpacing(18)

        self.activity_card = CompactActivityTimelineCard(content_widget)
        left_col.addWidget(self.activity_card)

        # System & Build Specs Card
        self.specs_card = QFrame(content_widget)
        self.specs_card.setStyleSheet("""
            QFrame {
                background-color: #18181B;
                border: 1px solid rgba(255, 255, 255, 0.06);
                border-radius: 8px;
            }
        """)
        specs_layout = QVBoxLayout(self.specs_card)
        specs_layout.setContentsMargins(16, 14, 16, 14)
        specs_layout.setSpacing(8)

        specs_title = QLabel("INSTALLATION & RUNNER DETAILS")
        specs_title.setStyleSheet("color: #71717A; font-size: 11px; font-weight: 700; letter-spacing: 0.8px; background: transparent;")
        specs_layout.addWidget(specs_title)

        self.lbl_path = QLabel("Executable: -")
        self.lbl_path.setStyleSheet("color: #A1A1AA; font-size: 12px; background: transparent;")
        specs_layout.addWidget(self.lbl_path)

        self.lbl_mode = QLabel("Runner: UMU / Wine")
        self.lbl_mode.setStyleSheet("color: #A1A1AA; font-size: 12px; background: transparent;")
        specs_layout.addWidget(self.lbl_mode)

        self.lbl_version = QLabel("Version: -")
        self.lbl_version.setStyleSheet("color: #A1A1AA; font-size: 12px; background: transparent;")
        specs_layout.addWidget(self.lbl_version)

        left_col.addWidget(self.specs_card)
        dashboard_row.addLayout(left_col, 62)

        # Right Column (Achievements & Notes) - 38% width
        right_col = QVBoxLayout()
        right_col.setSpacing(18)

        self.ach_widget = CompactAchievementsShowcaseWidget(content_widget)
        self.ach_widget.view_all_clicked.connect(self._on_view_achievements)
        right_col.addWidget(self.ach_widget)

        self.notes_widget = CompactNotesWidget(content_widget)
        right_col.addWidget(self.notes_widget)

        dashboard_row.addLayout(right_col, 38)
        self.content_layout.addLayout(dashboard_row)

        self.scroll_area.setWidget(content_widget)
        outer_layout.addWidget(self.scroll_area)

    def set_game(
        self,
        game_record: Any,
        ach_stats: Tuple[int, int, float],
        recent_achievements: List[dict],
        locked_achievements: List[dict],
        cloud_status: Any = None,
        hero_image_path: Optional[str] = None,
        is_running: bool = False
    ):
        """Bind all game attributes and stats into the Compact view."""
        if not game_record:
            return

        self.current_game_record = game_record
        if hasattr(game_record, "name"):
            g_id = game_record.id
            g_name = game_record.name
            g_path = game_record.path
            g_exe = game_record.executable
            g_mode = game_record.mode
            s_id = str(game_record.steam_id or "")
            last_played = game_record.last_played
            tags = game_record.tags
            is_fav = bool(game_record.is_favorite)
            ver_override = game_record.version_override
            playtime = game_record.playtime_seconds
        else:
            g_id = game_record[0]
            g_name = game_record[1] if len(game_record) > 1 else ""
            g_path = game_record[2] if len(game_record) > 2 else ""
            g_exe = game_record[3] if len(game_record) > 3 else ""
            g_mode = game_record[4] if len(game_record) > 4 else "umu"
            s_id = str(game_record[6] or "") if len(game_record) > 6 else ""
            last_played = game_record[8] if len(game_record) > 8 else None
            tags = game_record[10] if len(game_record) > 10 else ""
            is_fav = bool(game_record[13]) if len(game_record) > 13 else False
            ver_override = game_record[15] if len(game_record) > 15 else ""
            p7 = game_record[7] if len(game_record) > 7 and isinstance(game_record[7], (int, float)) else 0
            p19 = game_record[19] if len(game_record) > 19 and isinstance(game_record[19], (int, float)) else 0
            playtime = p7 or p19 or 0

        self.current_game_id = g_id
        self.current_steam_id = s_id

        # 1. Hero Banner
        self.hero_banner.set_hero_data(hero_image_path, g_name, tags)

        # 2. Action Bar
        if is_running:
            self.action_bar.btn_play.setText("  RUNNING")
            self.action_bar.btn_play.setEnabled(False)
        else:
            self.action_bar.btn_play.setText("  PLAY")
            self.action_bar.btn_play.setEnabled(True)

        self.action_bar.update_cloud_status(cloud_status)
        self.action_bar.last_played_val.setText(_format_last_played_date(last_played))
        self.action_bar.playtime_val.setText(_format_playtime_hours(playtime))

        unlocked, total, pct = ach_stats
        self.action_bar.ach_ratio_lbl.setText(f"{unlocked}/{total}")
        self.action_bar.ach_mini_progress.setValue(int(pct))
        self.action_bar.set_favorite_active(is_fav)

        # 3. Activity Feed
        self.activity_card.set_recent_achievements(recent_achievements)

        # 4. System Specs
        full_exe = os.path.join(g_path, g_exe) if (g_path and g_exe) else g_exe
        self.lbl_path.setText(f"Executable: {full_exe}")
        self.lbl_mode.setText(f"Runner mode: {g_mode.upper()} (Firejail sandbox active)")
        ver_str = ver_override if ver_override else (f"Steam AppID: {s_id}" if s_id else "Local game")
        self.lbl_version.setText(f"Version: {ver_str}")

        # 5. Achievements Showcase
        self.ach_widget.set_achievements_data(unlocked, total, pct, recent_achievements, locked_achievements)

        # 6. Notes
        self.notes_widget.load_notes_for_game(g_id)

    def _on_play(self):
        if self.current_game_id is not None:
            self.play_requested.emit(self.current_game_id)

    def _on_settings(self):
        if self.current_game_id is not None:
            self.properties_requested.emit(self.current_game_id)

    def _on_folder(self):
        if self.current_game_id is not None:
            self.open_folder_requested.emit(self.current_game_id)

    def _on_save_manager(self):
        if self.current_game_id is not None:
            self.save_manager_requested.emit(self.current_game_id)

    def _on_favorite(self):
        if self.current_game_id is not None:
            self.favorite_toggled.emit(self.current_game_id)

    def _on_prefix(self):
        if self.current_game_id is not None:
            self.prefix_maintenance_requested.emit(self.current_game_id)

    def _on_screenshots(self):
        if self.current_game_id is not None:
            self.properties_requested.emit(self.current_game_id)

    def _on_steam_page(self):
        if self.current_steam_id:
            self.steam_page_requested.emit(self.current_steam_id)

    def _on_view_achievements(self):
        if self.current_game_id is not None:
            self.achievements_requested.emit(self.current_game_id)


class CompactSidebarListItemWidget(QWidget):
    """Compact 36px game row for the left sidebar game list."""
    def __init__(
        self,
        game_tuple: tuple,
        cache_dir: Optional[str] = None,
        cloud_status: Any = None,
        is_favorite: bool = False,
        parent=None
    ):
        super().__init__(parent)
        self.game_id = game_tuple[0]
        self.name = game_tuple[1]
        self.path = game_tuple[2] if len(game_tuple) > 2 else ""
        self.executable = game_tuple[3] if len(game_tuple) > 3 else ""
        self.icon_url = game_tuple[18] if len(game_tuple) > 18 and game_tuple[18] else ""
        self.cache_dir = cache_dir
        self.cloud_status = cloud_status
        self.is_favorite = is_favorite

        self.setFixedHeight(36)
        self._init_ui()

    def _init_ui(self):
        layout = QHBoxLayout(self)
        layout.setContentsMargins(8, 2, 8, 2)
        layout.setSpacing(8)

        # 1. 24x24 Game Icon
        self.icon_lbl = QLabel()
        self.icon_lbl.setFixedSize(24, 24)
        self.icon_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.icon_lbl.setStyleSheet("background: transparent; border-radius: 4px;")
        self._load_icon()
        layout.addWidget(self.icon_lbl)

        # 2. Game Title
        self.title_lbl = QLabel(self.name)
        self.title_lbl.setFont(QFont("Arial", 11, QFont.Weight.Medium))
        self.title_lbl.setStyleSheet("color: #E4E4E7; background: transparent;")
        layout.addWidget(self.title_lbl, 1)

        # 3. Favorite Star
        if self.is_favorite:
            fav_lbl = QLabel()
            fav_lbl.setPixmap(get_icon("ph.star-fill", color="#FFD60A").pixmap(12, 12))
            fav_lbl.setStyleSheet("background: transparent;")
            layout.addWidget(fav_lbl)

        # 4. Cloud Status Icon
        self.cloud_lbl = QLabel()
        self.cloud_lbl.setStyleSheet("background: transparent;")
        self._set_cloud_icon()
        layout.addWidget(self.cloud_lbl)

    def _set_cloud_icon(self):
        if self.cloud_status == SyncStatus.IN_SYNC:
            self.cloud_lbl.setPixmap(get_icon("ph.cloud-check-fill", color="#3CD070").pixmap(12, 12))
            self.cloud_lbl.setToolTip("Cloud: Up to date")
        elif self.cloud_status == SyncStatus.LOCAL_NEWER:
            self.cloud_lbl.setPixmap(get_icon("ph.cloud-arrow-up-fill", color="#3B9FE8").pixmap(12, 12))
            self.cloud_lbl.setToolTip("Cloud: Ready to upload")
        elif self.cloud_status == SyncStatus.CLOUD_NEWER:
            self.cloud_lbl.setPixmap(get_icon("ph.cloud-arrow-down-fill", color="#FF9F0A").pixmap(12, 12))
            self.cloud_lbl.setToolTip("Cloud: Newer in cloud")
        elif self.cloud_status == SyncStatus.CONFLICT:
            self.cloud_lbl.setPixmap(get_icon("ph.cloud-warning-fill", color="#FF453A").pixmap(12, 12))
            self.cloud_lbl.setToolTip("Cloud: Conflict")
        else:
            self.cloud_lbl.clear()

    def _load_icon(self):
        pix: Optional[QPixmap] = None
        if self.icon_url and os.path.exists(self.icon_url):
            loaded = QPixmap(self.icon_url)
            if not loaded.isNull():
                pix = loaded

        if pix is None and self.cache_dir:
            icons_dir = os.path.join(os.path.dirname(self.cache_dir), "icons")
            for ext in (".png", ".ico", ".jpg"):
                icon_path = os.path.join(icons_dir, f"icon_{self.game_id}{ext}")
                if os.path.exists(icon_path):
                    loaded = QPixmap(icon_path)
                    if not loaded.isNull():
                        pix = loaded
                        break

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

        if pix is None:
            pix = get_icon("ph.game-controller-bold", color="#3B9FE8").pixmap(24, 24)

        self.icon_lbl.setPixmap(_create_rounded_icon(pix, QSize(24, 24), radius=4))


class CompactSidebarListWidget(QFrame):
    """
    Vertical games list for the compact layout sidebar.
    Features instant search filter and clean hover/selection highlights.
    """
    game_selected = pyqtSignal(int)
    game_double_clicked = pyqtSignal(int)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("compactSidebarList")
        self.setMinimumWidth(220)
        self.setMaximumWidth(360)
        self.games_data: List[tuple] = []
        self.cache_dir: Optional[str] = None
        self.cloud_status_cache: Dict[int, Any] = {}

        self.setStyleSheet("""
            QFrame#compactSidebarList {
                background-color: #161618;
                border: none;
                border-right: 1px solid rgba(255, 255, 255, 0.06);
            }
        """)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(10, 12, 10, 12)
        layout.setSpacing(10)

        # ── Search Input ──
        search_box = QHBoxLayout()
        search_box.setSpacing(6)
        
        self.search_edit = QLineEdit()
        self.search_edit.setPlaceholderText("Search games...")
        self.search_edit.setFixedHeight(30)
        self.search_edit.setStyleSheet("""
            QLineEdit {
                background-color: #1C1C20;
                color: #FFFFFF;
                border: 1px solid rgba(255, 255, 255, 0.08);
                border-radius: 6px;
                padding: 0 10px;
                font-size: 11px;
            }
            QLineEdit:focus {
                border-color: #3B9FE8;
                background-color: #222228;
            }
        """)
        self.search_edit.textChanged.connect(self._on_search_changed)
        search_box.addWidget(self.search_edit)
        layout.addLayout(search_box)

        # ── Games Count / Header ──
        self.lbl_count = QLabel("GAMES (0)")
        self.lbl_count.setStyleSheet("color: #71717A; font-size: 10px; font-weight: 700; letter-spacing: 0.8px; padding-left: 4px; background: transparent;")
        layout.addWidget(self.lbl_count)

        # ── List Widget ──
        self.list_widget = QListWidget()
        self.list_widget.setFrameShape(QFrame.Shape.NoFrame)
        self.list_widget.setVerticalScrollMode(QListWidget.ScrollMode.ScrollPerPixel)
        self.list_widget.setStyleSheet("""
            QListWidget {
                background: transparent;
                border: none;
                outline: none;
            }
            QListWidget::item {
                background-color: transparent;
                border-radius: 6px;
                margin-bottom: 2px;
                border-left: 3px solid transparent;
            }
            QListWidget::item:hover {
                background-color: #202026;
            }
            QListWidget::item:selected {
                background-color: #272730;
                border-left: 3px solid #3B9FE8;
            }
            QScrollBar:vertical {
                background: transparent;
                width: 6px;
                margin: 0;
            }
            QScrollBar::handle:vertical {
                background: rgba(255, 255, 255, 0.12);
                border-radius: 3px;
                min-height: 20px;
            }
            QScrollBar::handle:vertical:hover {
                background: rgba(255, 255, 255, 0.22);
            }
        """)
        self.list_widget.itemClicked.connect(self._on_item_clicked)
        self.list_widget.itemDoubleClicked.connect(self._on_item_double_clicked)
        layout.addWidget(self.list_widget)

    def set_games(
        self,
        games: List[tuple],
        selected_ids: set,
        update_status_by_game_id: dict = None,
        cache_dir: Optional[str] = None,
        cloud_status_cache: dict = None
    ):
        self.games_data = games
        self.cache_dir = cache_dir
        self.cloud_status_cache = cloud_status_cache or {}
        self.lbl_count.setText(f"GAMES ({len(games)})")
        self._populate_list(self.search_edit.text().strip().lower(), selected_ids)

    def _populate_list(self, query: str = "", selected_ids: set = None):
        self.list_widget.blockSignals(True)
        self.list_widget.clear()

        for item_data in self.games_data:
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
            g_id = int(raw_id)
            g_name = g[1] if len(g) > 1 and g[1] else ""

            if query and query not in g_name.lower():
                continue

            item = QListWidgetItem(self.list_widget)
            item.setSizeHint(QSize(200, 38))
            item.setData(Qt.ItemDataRole.UserRole, g_id)

            c_entry = self.cloud_status_cache.get(g_id) if self.cloud_status_cache else None
            c_status = c_entry[0] if (c_entry and isinstance(c_entry, (tuple, list)) and len(c_entry) > 0) else (c_entry or SyncStatus.NO_SAVES)

            row_widget = CompactSidebarListItemWidget(
                g,
                cache_dir=self.cache_dir,
                cloud_status=c_status,
                is_favorite=is_fav,
                parent=self.list_widget
            )
            self.list_widget.setItemWidget(item, row_widget)

            if selected_ids and g_id in selected_ids:
                item.setSelected(True)

        self.list_widget.blockSignals(False)

    def select_game(self, game_id: int):
        for i in range(self.list_widget.count()):
            item = self.list_widget.item(i)
            if item and item.data(Qt.ItemDataRole.UserRole) == game_id:
                self.list_widget.setCurrentItem(item)
                break

    def _on_search_changed(self, text: str):
        self._populate_list(text.strip().lower())

    def _on_item_clicked(self, item: QListWidgetItem):
        if not item:
            return
        game_id = item.data(Qt.ItemDataRole.UserRole)
        if game_id is not None:
            self.game_selected.emit(game_id)

    def _on_item_double_clicked(self, item: QListWidgetItem):
        if not item:
            return
        game_id = item.data(Qt.ItemDataRole.UserRole)
        if game_id is not None:
            self.game_double_clicked.emit(game_id)


class CompactLayoutContainer(QWidget):
    """
    Two-Pane Compact Presentation:
    - Left: Compact Sidebar Games List (search, icons, status badges)
    - Right: Compact Game Page Widget (hero artwork, Play button, stats, achievements, notes)
    """
    game_selected = pyqtSignal(int)
    game_double_clicked = pyqtSignal(int)
    play_requested = pyqtSignal(int)
    properties_requested = pyqtSignal(int)
    save_manager_requested = pyqtSignal(int)
    open_folder_requested = pyqtSignal(int)
    prefix_maintenance_requested = pyqtSignal(int)
    favorite_toggled = pyqtSignal(int)
    achievements_requested = pyqtSignal(int)
    steam_page_requested = pyqtSignal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setStyleSheet("background-color: #121214;")

        main_layout = QHBoxLayout(self)
        main_layout.setContentsMargins(0, 0, 0, 0)
        main_layout.setSpacing(0)

        self.splitter = QSplitter(Qt.Orientation.Horizontal, self)
        self.splitter.setStyleSheet("""
            QSplitter {
                background: transparent;
            }
            QSplitter::handle {
                background: rgba(255, 255, 255, 0.04);
                width: 1px;
            }
        """)

        # Left: Games Sidebar List
        self.sidebar_list = CompactSidebarListWidget(self.splitter)
        self.sidebar_list.game_selected.connect(self.game_selected.emit)
        self.sidebar_list.game_double_clicked.connect(self.game_double_clicked.emit)
        self.splitter.addWidget(self.sidebar_list)

        # Right: Compact Game Detail Page
        self.game_page = CompactGamePageWidget(self.splitter)
        self.game_page.play_requested.connect(self.play_requested.emit)
        self.game_page.properties_requested.connect(self.properties_requested.emit)
        self.game_page.save_manager_requested.connect(self.save_manager_requested.emit)
        self.game_page.open_folder_requested.connect(self.open_folder_requested.emit)
        self.game_page.prefix_maintenance_requested.connect(self.prefix_maintenance_requested.emit)
        self.game_page.favorite_toggled.connect(self.favorite_toggled.emit)
        self.game_page.achievements_requested.connect(self.achievements_requested.emit)
        self.game_page.steam_page_requested.connect(self.steam_page_requested.emit)
        self.splitter.addWidget(self.game_page)

        self.splitter.setSizes([260, 920])
        main_layout.addWidget(self.splitter)

    def set_games(
        self,
        games: List[tuple],
        selected_ids: set,
        update_status_by_game_id: dict = None,
        cache_dir: Optional[str] = None,
        cloud_status_cache: dict = None
    ):
        self.sidebar_list.set_games(
            games,
            selected_ids,
            update_status_by_game_id,
            cache_dir,
            cloud_status_cache
        )

    def select_game(self, game_id: int):
        self.sidebar_list.select_game(game_id)


# Backward-compatible aliases
SteamHeroBanner = CompactHeroBanner
SteamActionBar = CompactActionBar
SteamSubNavBar = CompactSubNavBar
SteamActivityTimelineCard = CompactActivityTimelineCard
SteamAchievementsShowcaseWidget = CompactAchievementsShowcaseWidget
SteamNotesWidget = CompactNotesWidget
SteamSidebarListItemWidget = CompactSidebarListItemWidget
SteamSidebarListWidget = CompactSidebarListWidget
SteamGamePageWidget = CompactGamePageWidget
SteamLayoutContainer = CompactLayoutContainer
