"""
Compact Game Detail Page component for SafeLauncher.
Implements the modern Compact Library presentation:
- Cinematic Hero Artwork Header with gradient fades and game title
- Action / Play button
- Horizontal Stats Bar (Cloud Status, Last Played, Playtime, Achievements ratio)
- Quick Action Tools (Edit Game, Settings, Game Folder, Save Manager, Favorite)
- Sub-Navigation Bar (Edit Game, Properties, Saves, Folder, Prefix, Screenshots, Store Page)
- Two-Column Lower Dashboard:
  - Left: Activity Timeline (recent unlocks, build/installation specs)
  - Right: Achievements Showcase Card + Persistent Game Notes Widget
"""

import os
import re
import datetime
from typing import Optional, List, Dict, Any, Tuple

from PyQt6.QtCore import Qt, QSize, pyqtSignal, QSettings
from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QPushButton,
    QProgressBar, QTextEdit, QFrame, QScrollArea, QSizePolicy,
    QListWidget, QListWidgetItem, QLineEdit, QSplitter, QGridLayout,
    QComboBox
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
    date_diff = (now.date() - dt.date()).days

    if date_diff <= 0:
        return "Today"
    elif date_diff == 1:
        return "Yesterday"
    elif 1 < date_diff < 7:
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
    along with large game title typography and embedded glassmorphic action and sub-nav bars.
    """
    def __init__(self, parent=None):
        super().__init__(parent)
        self.hero_pixmap: Optional[QPixmap] = None
        self.game_title: str = "SafeLauncher"
        self.tags: str = ""
        self.setFixedHeight(440)
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)

        self._banner_layout = QVBoxLayout(self)
        self._banner_layout.setContentsMargins(0, 0, 0, 0)
        self._banner_layout.setSpacing(0)
        self._banner_layout.addStretch(1)

    def set_action_bar(self, action_bar: QWidget):
        """Embed action bar in hero banner so hero artwork extends behind it."""
        self._banner_layout.addWidget(action_bar)

    def set_sub_nav(self, sub_nav: QWidget):
        """Embed sub-nav bar directly below action bar for seamless zero-gap glassmorphism."""
        self._banner_layout.addWidget(sub_nav)

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
            crop_y = max(0, int((scaled.height() - h) * 0.35))
            painter.drawPixmap(0, 0, scaled, crop_x, crop_y, w, h)

            # Frosted glass blur effect behind the action and sub-nav bars (112px combined):
            glass_h = 128
            slice_y = h - glass_h
            if slice_y >= 0 and w > 0:
                bar_slice = scaled.copy(crop_x, crop_y + slice_y, w, glass_h)
                if not bar_slice.isNull():
                    blur_factor = 14
                    small_slice = bar_slice.scaled(
                        max(1, w // blur_factor),
                        max(1, glass_h // blur_factor),
                        Qt.AspectRatioMode.IgnoreAspectRatio,
                        Qt.TransformationMode.SmoothTransformation
                    )
                    blurred_slice = small_slice.scaled(
                        w, glass_h,
                        Qt.AspectRatioMode.IgnoreAspectRatio,
                        Qt.TransformationMode.SmoothTransformation
                    )
                    painter.drawPixmap(0, slice_y, blurred_slice)

        # Dark overlay gradients:
        # 1. Subtle top vignette
        top_grad = QLinearGradient(0, 0, 0, 70)
        top_grad.setColorAt(0.0, QColor(18, 18, 20, 140))
        top_grad.setColorAt(1.0, QColor(18, 18, 20, 0))
        painter.fillRect(0, 0, w, 70, top_grad)

        # 2. Smooth multi-stop upward fade from the bottom edge into and behind both bars
        fade_h = 200
        bottom_grad = QLinearGradient(0, h - fade_h, 0, h)
        bottom_grad.setColorAt(0.0, QColor(18, 18, 20, 0))
        bottom_grad.setColorAt(0.35, QColor(18, 18, 20, 25))
        bottom_grad.setColorAt(0.55, QColor(18, 18, 20, 70))
        bottom_grad.setColorAt(0.75, QColor(18, 18, 20, 130))
        bottom_grad.setColorAt(0.90, QColor(18, 18, 20, 195))
        bottom_grad.setColorAt(1.0, QColor(18, 18, 20, 245))
        painter.fillRect(0, h - fade_h, w, fade_h, bottom_grad)

        # Left shadow vignette for readable title text
        left_grad = QLinearGradient(0, 0, 520, 0)
        left_grad.setColorAt(0.0, QColor(18, 18, 20, 150))
        left_grad.setColorAt(0.7, QColor(18, 18, 20, 80))
        left_grad.setColorAt(1.0, QColor(18, 18, 20, 0))
        painter.fillRect(0, 0, 520, h, left_grad)

        # Draw Game Title above action bar with drop shadow
        title_y = h - 112 - 64
        if title_y > 0:
            from PyQt6.QtCore import QRect
            text_rect = QRect(36, title_y, max(100, w - 72), 56)
            painter.setPen(QColor(0, 0, 0, 210))
            title_font = QFont("Arial", 28, QFont.Weight.Bold)
            painter.setFont(title_font)
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
    edit_clicked = pyqtSignal()
    settings_clicked = pyqtSignal()
    folder_clicked = pyqtSignal()
    save_manager_clicked = pyqtSignal()
    favorite_clicked = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("compactActionBar")
        self.setFixedHeight(72)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setStyleSheet("""
            QFrame#compactActionBar {
                background: qlineargradient(x1:0, y1:0, x2:0, y2:1,
                    stop:0 rgba(28, 28, 34, 0.70),
                    stop:0.04 rgba(22, 22, 26, 0.60),
                    stop:0.65 rgba(18, 18, 22, 0.72),
                    stop:1 rgba(14, 14, 18, 0.85)
                );
                border-top: 1px solid rgba(255, 255, 255, 0.14);
                border-bottom: 1px solid rgba(255, 255, 255, 0.05);
                border-left: none;
                border-right: none;
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
        self.btn_play.clicked.connect(self.play_clicked.emit)
        layout.addWidget(self.btn_play, 0, Qt.AlignmentFlag.AlignVCenter)

        # ── 2. Horizontal Stats Columns with Vertical Dividers ──
        # Column 1: Cloud Status
        self.cloud_container = QVBoxLayout()
        self.cloud_container.setSpacing(2)
        self.cloud_container.setAlignment(Qt.AlignmentFlag.AlignVCenter)
        lbl_cloud_title = QLabel("CLOUD STATUS")
        lbl_cloud_title.setStyleSheet("color: #71717A; font-size: 10px; font-weight: 700; letter-spacing: 0.6px; background: transparent;")
        self.cloud_container.addWidget(lbl_cloud_title)

        cloud_row = QHBoxLayout()
        cloud_row.setSpacing(6)
        cloud_row.setAlignment(Qt.AlignmentFlag.AlignVCenter)
        self.cloud_icon_lbl = QLabel()
        self.cloud_icon_lbl.setPixmap(get_icon("ph.cloud-check-fill", color="#3CD070").pixmap(14, 14))
        self.cloud_icon_lbl.setStyleSheet("background: transparent;")
        cloud_row.addWidget(self.cloud_icon_lbl, 0, Qt.AlignmentFlag.AlignVCenter)

        self.update_dot = QLabel()
        self.update_dot.setVisible(False)

        self.cloud_text_lbl = QLabel("Up to date")
        self.cloud_text_lbl.setStyleSheet("color: #F4F4F5; font-size: 12px; font-weight: 600; background: transparent;")
        cloud_row.addWidget(self.cloud_text_lbl, 0, Qt.AlignmentFlag.AlignVCenter)
        self.cloud_container.addLayout(cloud_row)
        layout.addLayout(self.cloud_container)

        layout.addWidget(self._create_divider(), 0, Qt.AlignmentFlag.AlignVCenter)

        # Column 2: Last Played
        self.last_played_container = QVBoxLayout()
        self.last_played_container.setSpacing(2)
        self.last_played_container.setAlignment(Qt.AlignmentFlag.AlignVCenter)
        lbl_last_title = QLabel("LAST PLAYED")
        lbl_last_title.setStyleSheet("color: #71717A; font-size: 10px; font-weight: 700; letter-spacing: 0.6px; background: transparent;")
        self.last_played_container.addWidget(lbl_last_title)

        self.last_played_val = QLabel("Today")
        self.last_played_val.setStyleSheet("color: #F4F4F5; font-size: 12px; font-weight: 600; background: transparent;")
        self.last_played_container.addWidget(self.last_played_val)
        layout.addLayout(self.last_played_container)

        layout.addWidget(self._create_divider(), 0, Qt.AlignmentFlag.AlignVCenter)

        # Column 3: Play Time
        self.playtime_container = QVBoxLayout()
        self.playtime_container.setSpacing(2)
        self.playtime_container.setAlignment(Qt.AlignmentFlag.AlignVCenter)
        lbl_playtime_title = QLabel("PLAY TIME")
        lbl_playtime_title.setStyleSheet("color: #71717A; font-size: 10px; font-weight: 700; letter-spacing: 0.6px; background: transparent;")
        self.playtime_container.addWidget(lbl_playtime_title)

        self.playtime_val = QLabel("0.0 h")
        self.playtime_val.setStyleSheet("color: #F4F4F5; font-size: 12px; font-weight: 600; background: transparent;")
        self.playtime_container.addWidget(self.playtime_val)
        layout.addLayout(self.playtime_container)

        layout.addWidget(self._create_divider(), 0, Qt.AlignmentFlag.AlignVCenter)

        # Column 4: Achievements Progress Summary (stacked vertically under title)
        self.ach_container = QVBoxLayout()
        self.ach_container.setSpacing(2)
        self.ach_container.setAlignment(Qt.AlignmentFlag.AlignVCenter)
        lbl_ach_title = QLabel("ACHIEVEMENTS")
        lbl_ach_title.setStyleSheet("color: #71717A; font-size: 10px; font-weight: 700; letter-spacing: 0.6px; background: transparent;")
        self.ach_container.addWidget(lbl_ach_title)

        self.ach_ratio_lbl = QLabel("0 / 0")
        self.ach_ratio_lbl.setStyleSheet("color: #F4F4F5; font-size: 12px; font-weight: 600; background: transparent;")
        self.ach_container.addWidget(self.ach_ratio_lbl)

        self.ach_mini_progress = QProgressBar()
        self.ach_mini_progress.setFixedSize(80, 4)
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
        self.ach_container.addWidget(self.ach_mini_progress)
        layout.addLayout(self.ach_container)

        layout.addStretch()

        # ── 3. Quick Action Tool Buttons ──
        # Kept as attributes for signal compatibility; only Favorite is added to layout to eliminate duplicate icons
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

        self.btn_edit = QPushButton()
        self.btn_edit.setIcon(get_icon("ph.pencil-simple-bold", color="#A1A1AA"))
        self.btn_edit.setIconSize(QSize(16, 16))
        self.btn_edit.setFixedSize(36, 36)
        self.btn_edit.setToolTip("Edit game")
        self.btn_edit.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_edit.setStyleSheet(quick_btn_style)
        self.btn_edit.clicked.connect(self.edit_clicked.emit)

        self.btn_settings = QPushButton()
        self.btn_settings.setIcon(get_icon("ph.gear-six-bold", color="#A1A1AA"))
        self.btn_settings.setIconSize(QSize(16, 16))
        self.btn_settings.setFixedSize(36, 36)
        self.btn_settings.setToolTip("Game properties")
        self.btn_settings.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_settings.setStyleSheet(quick_btn_style)
        self.btn_settings.clicked.connect(self.settings_clicked.emit)

        self.btn_folder = QPushButton()
        self.btn_folder.setIcon(get_icon("ph.folder-open-bold", color="#A1A1AA"))
        self.btn_folder.setIconSize(QSize(16, 16))
        self.btn_folder.setFixedSize(36, 36)
        self.btn_folder.setToolTip("Open game folder")
        self.btn_folder.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_folder.setStyleSheet(quick_btn_style)
        self.btn_folder.clicked.connect(self.folder_clicked.emit)

        self.btn_save = QPushButton()
        self.btn_save.setIcon(get_icon("ph.cloud-bold", color="#A1A1AA"))
        self.btn_save.setIconSize(QSize(16, 16))
        self.btn_save.setFixedSize(36, 36)
        self.btn_save.setToolTip("Save manager & backups")
        self.btn_save.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_save.setStyleSheet(quick_btn_style)
        self.btn_save.clicked.connect(self.save_manager_clicked.emit)

        self.btn_fav = QPushButton()
        self.btn_fav.setIcon(get_icon("ph.heart-bold", color="#A1A1AA"))
        self.btn_fav.setIconSize(QSize(16, 16))
        self.btn_fav.setFixedSize(36, 36)
        self.btn_fav.setToolTip("Add to favorites")
        self.btn_fav.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_fav.setStyleSheet(quick_btn_style)
        self.btn_fav.clicked.connect(self.favorite_clicked.emit)
        layout.addWidget(self.btn_fav, 0, Qt.AlignmentFlag.AlignVCenter)

        self.set_play_state("play")

    def set_play_state(self, state: str):
        """
        Update play button visuals according to state:
        - 'play' / 'idle': Green PLAY button.
        - 'running': Blue RUNNING button (clickable to stop).
        - 'stopping': Blue STOPPING button (disabled while stopping).
        """
        state_lower = (state or "").lower()
        if state_lower in ("running", "active"):
            self.btn_play.setText("  RUNNING")
            self.btn_play.setIcon(get_icon("fa5s.stop", color="#FFFFFF"))
            self.btn_play.setIconSize(QSize(16, 16))
            self.btn_play.setEnabled(True)
            self.btn_play.setToolTip("Click to stop game")
            self.btn_play.setStyleSheet("""
                QPushButton {
                    background: qlineargradient(x1:0, y1:0, x2:1, y2:0, stop:0 #2575FC, stop:1 #1A56DB);
                    color: #FFFFFF;
                    border: none;
                    border-radius: 6px;
                    padding: 0 16px;
                    font-weight: 800;
                    letter-spacing: 0.5px;
                }
                QPushButton:hover {
                    background: qlineargradient(x1:0, y1:0, x2:1, y2:0, stop:0 #3B82F6, stop:1 #2563EB);
                }
                QPushButton:pressed {
                    background: #1D4ED8;
                }
            """)
        elif state_lower in ("stopping", "terminating"):
            self.btn_play.setText("  STOPPING")
            self.btn_play.setIcon(get_icon("ph.arrows-clockwise-bold", color="#FFFFFF"))
            self.btn_play.setIconSize(QSize(16, 16))
            self.btn_play.setEnabled(False)
            self.btn_play.setToolTip("Stopping game container...")
            self.btn_play.setStyleSheet("""
                QPushButton {
                    background: qlineargradient(x1:0, y1:0, x2:1, y2:0, stop:0 #2575FC, stop:1 #1A56DB);
                    color: rgba(255, 255, 255, 0.85);
                    border: none;
                    border-radius: 6px;
                    padding: 0 16px;
                    font-weight: 800;
                    letter-spacing: 0.5px;
                }
                QPushButton:disabled {
                    background: qlineargradient(x1:0, y1:0, x2:1, y2:0, stop:0 #2575FC, stop:1 #1A56DB);
                    color: rgba(255, 255, 255, 0.85);
                }
            """)
        else:
            self.btn_play.setText("  PLAY")
            self.btn_play.setIcon(get_icon("fa5s.play", color="#FFFFFF"))
            self.btn_play.setIconSize(QSize(16, 16))
            self.btn_play.setEnabled(True)
            self.btn_play.setToolTip("Launch game")
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

    def _create_divider(self) -> QFrame:
        div = QFrame()
        div.setFrameShape(QFrame.Shape.VLine)
        div.setFrameShadow(QFrame.Shadow.Plain)
        div.setFixedWidth(1)
        div.setFixedHeight(32)
        div.setStyleSheet("background-color: rgba(255, 255, 255, 0.08); border: none;")
        return div

    def update_cloud_status(self, status: Any, has_update: bool = False):
        """Update the cloud icon and text based on SyncStatus or available update."""
        self.update_dot.setVisible(False)

        if status == SyncStatus.IN_SYNC:
            self.cloud_icon_lbl.setPixmap(get_icon("ph.cloud-check-fill", color="#3CD070").pixmap(14, 14))
            self.cloud_text_lbl.setText("Up to date")
            self.cloud_text_lbl.setStyleSheet("color: #3CD070; font-size: 12px; font-weight: 600; background: transparent;")
        elif status == SyncStatus.LOCAL_NEWER:
            self.cloud_icon_lbl.setPixmap(get_icon("ph.cloud-arrow-up-fill", color="#FF9F0A").pixmap(14, 14))
            self.cloud_text_lbl.setText("Ready to upload")
            self.cloud_text_lbl.setStyleSheet("color: #FF9F0A; font-size: 12px; font-weight: 600; background: transparent;")
        elif status in (SyncStatus.CLOUD_NEWER, SyncStatus.CLOUD_ONLY):
            self.cloud_icon_lbl.setPixmap(get_icon("ph.cloud-arrow-down-fill", color="#FF9F0A").pixmap(14, 14))
            self.cloud_text_lbl.setText("Newer in cloud")
            self.cloud_text_lbl.setStyleSheet("color: #FF9F0A; font-size: 12px; font-weight: 600; background: transparent;")
        elif status == SyncStatus.CONFLICT:
            self.cloud_icon_lbl.setPixmap(get_icon("ph.cloud-warning-fill", color="#FF453A").pixmap(14, 14))
            self.cloud_text_lbl.setText("Cloud conflict")
            self.cloud_text_lbl.setStyleSheet("color: #FF453A; font-size: 12px; font-weight: 600; background: transparent;")
        elif status == SyncStatus.CLOUD_OFFLINE:
            self.cloud_icon_lbl.setPixmap(get_icon("ph.cloud-slash-bold", color="#71717A").pixmap(14, 14))
            self.cloud_text_lbl.setText("Cloud offline")
            self.cloud_text_lbl.setStyleSheet("color: #71717A; font-size: 12px; font-weight: 600; background: transparent;")
        else:
            self.cloud_icon_lbl.setPixmap(get_icon("ph.cloud-slash-bold", color="#71717A").pixmap(14, 14))
            self.cloud_text_lbl.setText("Cloud missing")
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
    edit_clicked = pyqtSignal()
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
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        self.setStyleSheet("""
            QFrame#compactSubNavBar {
                background: qlineargradient(x1:0, y1:0, x2:0, y2:1,
                    stop:0 rgba(16, 16, 20, 0.80),
                    stop:1 rgba(12, 12, 16, 0.90)
                );
                border-bottom: 1px solid rgba(255, 255, 255, 0.08);
                border-left: none;
                border-right: none;
                border-top: none;
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

        self.btn_edit = QPushButton("Edit Game")
        self.btn_edit.setStyleSheet(nav_style)
        self.btn_edit.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_edit.clicked.connect(self.edit_clicked.emit)
        layout.addWidget(self.btn_edit)

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
        self.setMaximumHeight(260)
        self.setStyleSheet("""
            QFrame {
                background-color: #18181B;
                border: none;
                border-radius: 6px;
            }
        """)
        self.vbox = QVBoxLayout(self)
        self.vbox.setContentsMargins(16, 14, 16, 14)
        self.vbox.setSpacing(10)

        # Header
        hdr = QHBoxLayout()
        hdr.setSpacing(8)
        self.hdr_title = QLabel("ACTIVITY")
        self.hdr_title.setStyleSheet("color: #71717A; font-size: 11px; font-weight: 700; letter-spacing: 0.8px; background: transparent;")
        hdr.addWidget(self.hdr_title)
        hdr.addStretch()
        self.vbox.addLayout(hdr)

        # Scrollable container so activity list stays within max height
        self.scroll_area = QScrollArea(self)
        self.scroll_area.setWidgetResizable(True)
        self.scroll_area.setFrameShape(QFrame.Shape.NoFrame)
        self.scroll_area.setStyleSheet("""
            QScrollArea {
                background: transparent;
                border: none;
            }
            QScrollBar:vertical {
                background: transparent;
                width: 6px;
                margin: 0px;
            }
            QScrollBar::handle:vertical {
                background: rgba(255, 255, 255, 0.15);
                border-radius: 3px;
                min-height: 20px;
            }
            QScrollBar::handle:vertical:hover {
                background: rgba(255, 255, 255, 0.25);
            }
        """)
        if self.scroll_area.viewport():
            self.scroll_area.viewport().setAutoFillBackground(False)
            self.scroll_area.viewport().setStyleSheet("background: transparent;")

        self.items_container = QWidget()
        self.items_container.setStyleSheet("background: transparent;")
        self.items_layout = QVBoxLayout(self.items_container)
        self.items_layout.setContentsMargins(0, 0, 0, 0)
        self.items_layout.setSpacing(8)
        self.items_layout.setAlignment(Qt.AlignmentFlag.AlignTop)
        self.scroll_area.setWidget(self.items_container)
        self.vbox.addWidget(self.scroll_area)

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
                    background-color: #202024;
                    border: none;
                    border-radius: 6px;
                }
            """)
            r_layout = QHBoxLayout(row)
            r_layout.setContentsMargins(10, 8, 10, 8)
            r_layout.setSpacing(10)

            icon_lbl = QLabel()
            icon_lbl.setFixedSize(36, 36)
            icon_lbl.setStyleSheet("background-color: #27272A; border-radius: 4px; border: none;")
            icon_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)

            icon_path = ach.get("icon_path", "")
            if icon_path and os.path.isfile(icon_path):
                pix = QPixmap(icon_path)
                if not pix.isNull():
                    icon_lbl.setPixmap(_create_rounded_icon(pix, QSize(36, 36), radius=4))
                else:
                    icon_lbl.setPixmap(get_icon("ph.trophy-fill", color="#FFD60A").pixmap(20, 20))
            else:
                icon_lbl.setPixmap(get_icon("ph.trophy-fill", color="#FFD60A").pixmap(20, 20))

            r_layout.addWidget(icon_lbl)

            info_vbox = QVBoxLayout()
            info_vbox.setSpacing(2)
            title = QLabel(ach.get("display_name") or ach.get("api_name") or "Achievement")
            title.setStyleSheet("color: #FFFFFF; font-size: 12px; font-weight: 700; background: transparent;")
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
                border: none;
                border-radius: 6px;
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
                background-color: #202024;
                border: none;
                border-radius: 6px;
            }
        """)
        rc_layout = QHBoxLayout(self.recent_card)
        rc_layout.setContentsMargins(10, 8, 10, 8)
        rc_layout.setSpacing(10)

        self.recent_icon = QLabel()
        self.recent_icon.setFixedSize(42, 42)
        self.recent_icon.setStyleSheet("background-color: #27272A; border-radius: 4px; border: none;")
        self.recent_icon.setAlignment(Qt.AlignmentFlag.AlignCenter)
        rc_layout.addWidget(self.recent_icon)

        rc_text = QVBoxLayout()
        rc_text.setSpacing(2)
        self.recent_title = QLabel("Achievements are missing")
        self.recent_title.setStyleSheet("color: #FFFFFF; font-size: 12px; font-weight: 700; background: transparent;")
        rc_text.addWidget(self.recent_title)

        self.recent_desc = QLabel("No achievements configured for this game")
        self.recent_desc.setStyleSheet("color: #A1A1AA; font-size: 11px; background: transparent;")
        rc_text.addWidget(self.recent_desc)
        rc_layout.addLayout(rc_text, 1)

        self.vbox.addWidget(self.recent_card)

        # Locked Thumbnails Row
        self.thumbs_row = QHBoxLayout()
        self.thumbs_row.setSpacing(6)
        self.vbox.addLayout(self.thumbs_row)

        # View All Button
        self.btn_view_all = QPushButton("View All Achievements")
        self.btn_view_all.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_view_all.setFixedHeight(30)
        self.btn_view_all.setStyleSheet("""
            QPushButton {
                background-color: #202024;
                color: #D4D4D8;
                border: none;
                border-radius: 4px;
                font-size: 11px;
                font-weight: 600;
            }
            QPushButton:hover {
                background-color: #2A2A30;
                color: #FFFFFF;
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

        if total == 0:
            self.recent_title.setText("Achievements are missing")
            self.recent_desc.setText("No achievements configured for this game")
            self.recent_icon.setPixmap(get_icon("ph.trophy-bold", color="#71717A").pixmap(20, 20))
            self.btn_view_all.setEnabled(False)
        elif recent_unlocked:
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
            self.btn_view_all.setEnabled(True)
        else:
            self.recent_title.setText("No achievements unlocked yet")
            self.recent_desc.setText("Keep playing to unlock your first achievements!")
            self.recent_icon.setPixmap(get_icon("ph.trophy-bold", color="#71717A").pixmap(20, 20))
            self.btn_view_all.setEnabled(True)

        # Re-populate locked thumbnails
        while self.thumbs_row.count():
            item = self.thumbs_row.takeAt(0)
            w = item.widget()
            if w:
                w.deleteLater()

        for ach in locked_sample[:10]:
            thumb = QLabel()
            thumb.setFixedSize(32, 32)
            thumb.setStyleSheet("background-color: #202024; border-radius: 4px; border: none;")
            thumb.setAlignment(Qt.AlignmentFlag.AlignCenter)
            icongray = ach.get("icongray_path", "")
            if icongray and os.path.isfile(icongray):
                p = QPixmap(icongray)
                if not p.isNull():
                    thumb.setPixmap(_create_rounded_icon(p, QSize(32, 32), radius=4))
                else:
                    thumb.setPixmap(get_icon("ph.lock-simple-bold", color="#71717A").pixmap(14, 14))
            else:
                thumb.setPixmap(get_icon("ph.lock-simple-bold", color="#71717A").pixmap(14, 14))
            thumb.setToolTip(ach.get("display_name") or "Locked achievement")
            self.thumbs_row.addWidget(thumb)

        if total > 10:
            remaining = max(0, total - unlocked - min(len(locked_sample), 10))
            if remaining > 0:
                more_lbl = QLabel(f"+{remaining}")
                more_lbl.setStyleSheet("color: #71717A; font-size: 11px; font-weight: 700; background: transparent; padding-left: 4px;")
                self.thumbs_row.addWidget(more_lbl)

        self.thumbs_row.addStretch()


class CompactMediaShowcaseWidget(QFrame):
    """
    Dedicated Screenshot / Video Screen widget displayed under Achievements.
    If the recording/captures module is not active or installed, displays:
    'Module not active - turn on in settings' with an 'Open Settings' button.
    When active, displays recent screenshot and video captures with thumbnail previews
    and direct access buttons.
    """
    screenshots_clicked = pyqtSignal(int)
    videos_clicked = pyqtSignal(int)
    settings_clicked = pyqtSignal()
    open_screenshot_clicked = pyqtSignal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("compactMediaShowcase")
        self.current_game_id: Optional[int] = None
        self.current_game_name: str = ""
        self.setStyleSheet("""
            QFrame#compactMediaShowcase {
                background-color: #18181B;
                border: none;
                border-radius: 6px;
            }
        """)

        self.vbox = QVBoxLayout(self)
        self.vbox.setContentsMargins(16, 14, 16, 14)
        self.vbox.setSpacing(10)

        # Header Row
        header_row = QHBoxLayout()
        header_row.setSpacing(8)

        self.lbl_title = QLabel("SCREENSHOTS & RECORDINGS")
        self.lbl_title.setStyleSheet("color: #71717A; font-size: 11px; font-weight: 700; letter-spacing: 0.8px; background: transparent;")
        header_row.addWidget(self.lbl_title)
        header_row.addStretch()

        self.vbox.addLayout(header_row)

        # Content Area (dynamically refreshed)
        self.content_widget = QWidget(self)
        self.content_widget.setStyleSheet("background: transparent;")
        self.content_layout = QVBoxLayout(self.content_widget)
        self.content_layout.setContentsMargins(0, 0, 0, 0)
        self.content_layout.setSpacing(8)
        self.vbox.addWidget(self.content_widget)

    def set_media_data(self, game_id: Optional[int], game_name: str = "", force_inactive: Optional[bool] = None):
        """Populate widget with media for the game or show inactive module state."""
        self.current_game_id = game_id
        self.current_game_name = game_name

        # Clear existing items in content_layout
        while self.content_layout.count():
            item = self.content_layout.takeAt(0)
            w = item.widget()
            if w:
                w.deleteLater()
            l = item.layout()
            if l:
                while l.count():
                    sub = l.takeAt(0)
                    sw = sub.widget()
                    if sw:
                        sw.deleteLater()

        if not game_id:
            return

        # 1. Determine module active state
        is_active = True
        if force_inactive is True:
            is_active = False
        else:
            try:
                from core.plugins.gpu_screen_recorder import GpuRecorderService
                is_installed = GpuRecorderService.is_installed()
            except Exception:
                is_installed = False

            settings = QSettings("SafeLauncher", "SafeLauncher")
            is_enabled = settings.value("gpu_recorder_enabled", True, type=bool)
            is_active = is_installed and is_enabled

        if not is_active:
            # Inactive State: Cleanly integrated without nested card overlay
            top_row = QHBoxLayout()
            top_row.setSpacing(10)
            top_row.setAlignment(Qt.AlignmentFlag.AlignVCenter)

            icon_lbl = QLabel()
            icon_lbl.setPixmap(get_icon("ph.video-camera-slash-bold", color="#71717A").pixmap(20, 20))
            icon_lbl.setStyleSheet("background: transparent;")
            top_row.addWidget(icon_lbl)

            text_col = QVBoxLayout()
            text_col.setSpacing(2)
            lbl_inactive_title = QLabel("Module not active - turn on in settings")
            lbl_inactive_title.setStyleSheet("color: #E4E4E7; font-size: 12px; font-weight: 600; background: transparent;")
            text_col.addWidget(lbl_inactive_title)

            lbl_inactive_sub = QLabel("Enable GPU Screen Recorder / Captures in settings")
            lbl_inactive_sub.setStyleSheet("color: #71717A; font-size: 11px; background: transparent;")
            text_col.addWidget(lbl_inactive_sub)

            top_row.addLayout(text_col)
            top_row.addStretch()
            self.content_layout.addLayout(top_row)

            btn_settings = QPushButton("Open Settings")
            btn_settings.setIcon(get_icon("ph.gear-six-bold", color="#FFFFFF"))
            btn_settings.setIconSize(QSize(13, 13))
            btn_settings.setFixedHeight(28)
            btn_settings.setCursor(Qt.CursorShape.PointingHandCursor)
            btn_settings.setStyleSheet("""
                QPushButton {
                    background-color: #202024;
                    color: #FFFFFF;
                    border: 1px solid rgba(255, 255, 255, 0.08);
                    border-radius: 4px;
                    font-size: 11px;
                    font-weight: 500;
                    padding: 0 12px;
                }
                QPushButton:hover {
                    background-color: #28282E;
                    border-color: rgba(255, 255, 255, 0.16);
                }
            """)
            btn_settings.clicked.connect(self.settings_clicked.emit)
            self.content_layout.addWidget(btn_settings)
            return

        # Active State: Locate Screenshots and Recordings
        from database import _APP_DATA_DIR
        shots_dir = os.path.join(_APP_DATA_DIR, "screenshots", str(game_id))
        screenshots = []
        if os.path.isdir(shots_dir):
            for f in os.listdir(shots_dir):
                if f.lower().endswith((".png", ".jpg", ".jpeg")):
                    screenshots.append(os.path.join(shots_dir, f))
            screenshots.sort(key=lambda p: os.path.getmtime(p), reverse=True)

        settings = QSettings("SafeLauncher", "SafeLauncher")
        from core.plugins.gpu_screen_recorder import DEFAULT_RECORDINGS_DIR
        out_dir = settings.value("gpu_recorder_output_dir", DEFAULT_RECORDINGS_DIR, type=str)
        out_dir = os.path.abspath(os.path.expanduser(out_dir))
        game_prefix = re.sub(r"[^a-z0-9]+", "_", (game_name or "").strip().lower()).strip("_")
        videos = []
        if os.path.isdir(out_dir) and game_prefix:
            for f in os.listdir(out_dir):
                if f.lower().endswith((".mp4", ".mkv", ".webm", ".mov", ".avi")):
                    if game_prefix in f.lower():
                        videos.append(os.path.join(out_dir, f))
            videos.sort(key=lambda p: os.path.getmtime(p), reverse=True)

        if screenshots or videos:
            thumbs_row = QHBoxLayout()
            thumbs_row.setSpacing(8)

            for s_path in screenshots[:2]:
                thumb_btn = QPushButton()
                thumb_btn.setFixedSize(110, 64)
                thumb_btn.setCursor(Qt.CursorShape.PointingHandCursor)
                thumb_btn.setToolTip(f"View screenshot: {os.path.basename(s_path)}")
                pix = QPixmap(s_path)
                if not pix.isNull():
                    rounded = _create_rounded_icon(pix, QSize(110, 64), radius=4)
                    thumb_btn.setIcon(QIcon(rounded))
                    thumb_btn.setIconSize(QSize(110, 64))
                thumb_btn.setStyleSheet("""
                    QPushButton {
                        background-color: #202024;
                        border: 1px solid rgba(255, 255, 255, 0.08);
                        border-radius: 4px;
                    }
                    QPushButton:hover {
                        border-color: #3B9FE8;
                    }
                """)
                thumb_btn.clicked.connect(lambda _, p=s_path: self.open_screenshot_clicked.emit(p))
                thumbs_row.addWidget(thumb_btn)

            if videos:
                v_path = videos[0]
                v_btn = QPushButton()
                v_btn.setFixedSize(110, 64)
                v_btn.setCursor(Qt.CursorShape.PointingHandCursor)
                v_btn.setToolTip(f"Play recording: {os.path.basename(v_path)}")
                v_btn.setIcon(get_icon("ph.film-strip-bold", color="#3B9FE8"))
                v_btn.setIconSize(QSize(28, 28))
                v_btn.setStyleSheet("""
                    QPushButton {
                        background-color: #1A202C;
                        border: 1px solid rgba(59, 159, 232, 0.3);
                        border-radius: 4px;
                    }
                    QPushButton:hover {
                        border-color: #3B9FE8;
                        background-color: #232B3B;
                    }
                """)
                v_btn.clicked.connect(lambda _, gid=game_id: self.videos_clicked.emit(gid))
                thumbs_row.addWidget(v_btn)

            thumbs_row.addStretch()
            self.content_layout.addLayout(thumbs_row)

            actions_row = QHBoxLayout()
            actions_row.setSpacing(8)

            btn_shots = QPushButton(f"Screenshots ({len(screenshots)})")
            btn_shots.setIcon(get_icon("ph.image-bold", color="#A1A1AA"))
            btn_shots.setIconSize(QSize(13, 13))
            btn_shots.setFixedHeight(28)
            btn_shots.setCursor(Qt.CursorShape.PointingHandCursor)
            btn_shots.setStyleSheet("""
                QPushButton {
                    background-color: #202024;
                    color: #E4E4E7;
                    border: 1px solid rgba(255, 255, 255, 0.08);
                    border-radius: 4px;
                    font-size: 11px;
                    font-weight: 600;
                    padding: 0 10px;
                }
                QPushButton:hover {
                    background-color: #28282E;
                    border-color: rgba(255, 255, 255, 0.16);
                }
            """)
            btn_shots.clicked.connect(lambda _, gid=game_id: self.screenshots_clicked.emit(gid))
            actions_row.addWidget(btn_shots)

            btn_vids = QPushButton(f"Videos ({len(videos)})")
            btn_vids.setIcon(get_icon("ph.film-strip-bold", color="#A1A1AA"))
            btn_vids.setIconSize(QSize(13, 13))
            btn_vids.setFixedHeight(28)
            btn_vids.setCursor(Qt.CursorShape.PointingHandCursor)
            btn_vids.setStyleSheet("""
                QPushButton {
                    background-color: #202024;
                    color: #E4E4E7;
                    border: 1px solid rgba(255, 255, 255, 0.08);
                    border-radius: 4px;
                    font-size: 11px;
                    font-weight: 600;
                    padding: 0 10px;
                }
                QPushButton:hover {
                    background-color: #28282E;
                    border-color: rgba(255, 255, 255, 0.16);
                }
            """)
            btn_vids.clicked.connect(lambda _, gid=game_id: self.videos_clicked.emit(gid))
            actions_row.addWidget(btn_vids)
            actions_row.addStretch()

            self.content_layout.addLayout(actions_row)
        else:
            # Empty Captures State: Clean typography and controls without nested box overlay
            top_row = QHBoxLayout()
            top_row.setSpacing(8)
            top_row.setAlignment(Qt.AlignmentFlag.AlignVCenter)

            icon_lbl = QLabel()
            icon_lbl.setPixmap(get_icon("ph.camera-bold", color="#71717A").pixmap(18, 18))
            icon_lbl.setStyleSheet("background: transparent;")
            top_row.addWidget(icon_lbl)

            lbl_no_captures = QLabel("No captures yet")
            lbl_no_captures.setStyleSheet("color: #E4E4E7; font-size: 12px; font-weight: 600; background: transparent;")
            top_row.addWidget(lbl_no_captures)
            top_row.addStretch()
            self.content_layout.addLayout(top_row)

            lbl_hint = QLabel("Press F12 for screenshot, Ctrl+Shift+Y to record")
            lbl_hint.setStyleSheet("color: #71717A; font-size: 11px; background: transparent; padding-top: 1px;")
            self.content_layout.addWidget(lbl_hint)

            actions_row = QHBoxLayout()
            actions_row.setSpacing(8)

            btn_shots = QPushButton("Screenshots (0)")
            btn_shots.setIcon(get_icon("ph.image-bold", color="#A1A1AA"))
            btn_shots.setIconSize(QSize(13, 13))
            btn_shots.setFixedHeight(26)
            btn_shots.setCursor(Qt.CursorShape.PointingHandCursor)
            btn_shots.setStyleSheet("""
                QPushButton {
                    background-color: #202024;
                    color: #A1A1AA;
                    border: 1px solid rgba(255, 255, 255, 0.08);
                    border-radius: 4px;
                    font-size: 11px;
                    font-weight: 500;
                    padding: 0 10px;
                }
                QPushButton:hover {
                    color: #FFFFFF;
                    background-color: #28282E;
                    border-color: rgba(255, 255, 255, 0.16);
                }
            """)
            btn_shots.clicked.connect(lambda _, gid=game_id: self.screenshots_clicked.emit(gid))
            actions_row.addWidget(btn_shots)

            btn_vids = QPushButton("Videos (0)")
            btn_vids.setIcon(get_icon("ph.film-strip-bold", color="#A1A1AA"))
            btn_vids.setIconSize(QSize(13, 13))
            btn_vids.setFixedHeight(26)
            btn_vids.setCursor(Qt.CursorShape.PointingHandCursor)
            btn_vids.setStyleSheet("""
                QPushButton {
                    background-color: #202024;
                    color: #A1A1AA;
                    border: 1px solid rgba(255, 255, 255, 0.08);
                    border-radius: 4px;
                    font-size: 11px;
                    font-weight: 500;
                    padding: 0 10px;
                }
                QPushButton:hover {
                    color: #FFFFFF;
                    background-color: #28282E;
                    border-color: rgba(255, 255, 255, 0.16);
                }
            """)
            btn_vids.clicked.connect(lambda _, gid=game_id: self.videos_clicked.emit(gid))
            actions_row.addWidget(btn_vids)
            actions_row.addStretch()

            self.content_layout.addLayout(actions_row)


class CompactNotesWidget(QFrame):
    """Interactive Game Notes widget persisted in QSettings per game ID."""
    def __init__(self, parent=None):
        super().__init__(parent)
        self.current_game_id: Optional[int] = None
        self.settings = QSettings("SafeLauncher", "SafeLauncher")
        self.setStyleSheet("""
            QFrame {
                background-color: #18181B;
                border: none;
                border-radius: 6px;
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
                background-color: #202024;
                border: none;
                border-radius: 6px;
                color: #E4E4E7;
                font-size: 12px;
                padding: 8px;
            }
            QTextEdit:focus {
                background-color: #25252A;
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
    edit_requested = pyqtSignal(int)
    properties_requested = pyqtSignal(int)
    save_manager_requested = pyqtSignal(int)
    open_folder_requested = pyqtSignal(int)
    prefix_maintenance_requested = pyqtSignal(int)
    favorite_toggled = pyqtSignal(int)
    achievements_requested = pyqtSignal(int)
    steam_page_requested = pyqtSignal(str)
    screenshots_requested = pyqtSignal(int)
    videos_requested = pyqtSignal(int)
    settings_requested = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.current_game_id: Optional[int] = None
        self.current_game_record: Any = None
        self.current_steam_id: str = ""

        self.setObjectName("compactGamePageRoot")
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setStyleSheet("""
            QWidget#compactGamePageRoot {
                background: transparent;
            }
            QLabel {
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
        self.scroll_area.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.scroll_area.setStyleSheet("""
            QScrollArea {
                background: transparent;
                background-color: transparent;
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
            self.scroll_area.viewport().setStyleSheet("background: transparent; background-color: transparent;")

        content_widget = QWidget()
        content_widget.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        content_widget.setStyleSheet("background: transparent; background-color: transparent;")
        self.content_layout = QVBoxLayout(content_widget)
        self.content_layout.setContentsMargins(0, 0, 0, 32)
        self.content_layout.setSpacing(0)

        # 1. Cinematic Hero Banner (with embedded glassmorphic action bar)
        self.hero_banner = CompactHeroBanner(content_widget)
        self.content_layout.addWidget(self.hero_banner)

        # 2. Action & Stats Bar (embedded directly into hero banner for glassmorphic overlay)
        self.action_bar = CompactActionBar(self.hero_banner)
        self.action_bar.play_clicked.connect(self._on_play)
        self.action_bar.edit_clicked.connect(self._on_edit)
        self.action_bar.settings_clicked.connect(self._on_settings)
        self.action_bar.folder_clicked.connect(self._on_folder)
        self.action_bar.save_manager_clicked.connect(self._on_save_manager)
        self.action_bar.favorite_clicked.connect(self._on_favorite)
        self.hero_banner.set_action_bar(self.action_bar)

        # 3. Sub-Navigation Bar (embedded seamlessly into hero banner directly below action bar)
        self.sub_nav = CompactSubNavBar(self.hero_banner)
        self.sub_nav.edit_clicked.connect(self._on_edit)
        self.sub_nav.properties_clicked.connect(self._on_settings)
        self.sub_nav.save_manager_clicked.connect(self._on_save_manager)
        self.sub_nav.open_folder_clicked.connect(self._on_folder)
        self.sub_nav.prefix_clicked.connect(self._on_prefix)
        self.sub_nav.screenshots_clicked.connect(self._on_screenshots)
        self.sub_nav.steam_page_clicked.connect(self._on_steam_page)
        self.hero_banner.set_sub_nav(self.sub_nav)

        # 4. Two-Column Lower Dashboard
        dashboard_row = QHBoxLayout()
        dashboard_row.setContentsMargins(32, 20, 32, 0)
        dashboard_row.setSpacing(24)
        dashboard_row.setAlignment(Qt.AlignmentFlag.AlignTop)

        # Left Column (Activity & Details) - 62% width
        left_col = QVBoxLayout()
        left_col.setContentsMargins(0, 0, 0, 0)
        left_col.setSpacing(18)
        left_col.setAlignment(Qt.AlignmentFlag.AlignTop)

        self.activity_card = CompactActivityTimelineCard(content_widget)
        left_col.addWidget(self.activity_card, 0, Qt.AlignmentFlag.AlignTop)

        # System & Build Specs Card
        self.specs_card = QFrame(content_widget)
        self.specs_card.setMaximumHeight(160)
        self.specs_card.setStyleSheet("""
            QFrame {
                background-color: #18181B;
                border: none;
                border-radius: 6px;
            }
        """)
        specs_layout = QVBoxLayout(self.specs_card)
        specs_layout.setContentsMargins(16, 14, 16, 14)
        specs_layout.setSpacing(10)

        specs_title = QLabel("RUNNER & INSTALLATION DETAILS")
        specs_title.setStyleSheet("color: #71717A; font-size: 11px; font-weight: 700; letter-spacing: 0.8px; background: transparent;")
        specs_layout.addWidget(specs_title)

        grid = QGridLayout()
        grid.setContentsMargins(0, 2, 0, 0)
        grid.setHorizontalSpacing(16)
        grid.setVerticalSpacing(6)

        lbl_k_exe = QLabel("Executable")
        lbl_k_exe.setStyleSheet("color: #71717A; font-size: 11px; font-weight: 600; background: transparent;")
        lbl_k_exe.setFixedWidth(85)
        grid.addWidget(lbl_k_exe, 0, 0)

        self.lbl_path = QLabel("Executable: -")
        self.lbl_path.setStyleSheet("color: #E4E4E7; font-size: 11px; background: transparent;")
        self.lbl_path.setWordWrap(True)
        grid.addWidget(self.lbl_path, 0, 1)

        lbl_k_mode = QLabel("Runner")
        lbl_k_mode.setStyleSheet("color: #71717A; font-size: 11px; font-weight: 600; background: transparent;")
        lbl_k_mode.setFixedWidth(85)
        grid.addWidget(lbl_k_mode, 1, 0)

        self.lbl_mode = QLabel("Runner: UMU / Wine")
        self.lbl_mode.setStyleSheet("color: #E4E4E7; font-size: 11px; background: transparent;")
        grid.addWidget(self.lbl_mode, 1, 1)

        lbl_k_ver = QLabel("Version")
        lbl_k_ver.setStyleSheet("color: #71717A; font-size: 11px; font-weight: 600; background: transparent;")
        lbl_k_ver.setFixedWidth(85)
        grid.addWidget(lbl_k_ver, 2, 0)

        self.lbl_version = QLabel("Version: -")
        self.lbl_version.setStyleSheet("color: #E4E4E7; font-size: 11px; background: transparent;")
        grid.addWidget(self.lbl_version, 2, 1)

        grid.setColumnStretch(1, 1)
        specs_layout.addLayout(grid)

        left_col.addWidget(self.specs_card, 0, Qt.AlignmentFlag.AlignTop)
        left_col.addStretch()
        dashboard_row.addLayout(left_col, 62)

        # Right Column (Achievements & Notes) - 38% width
        right_col = QVBoxLayout()
        right_col.setContentsMargins(0, 0, 0, 0)
        right_col.setSpacing(18)
        right_col.setAlignment(Qt.AlignmentFlag.AlignTop)

        self.ach_widget = CompactAchievementsShowcaseWidget(content_widget)
        self.ach_widget.view_all_clicked.connect(self._on_view_achievements)
        right_col.addWidget(self.ach_widget, 0, Qt.AlignmentFlag.AlignTop)

        # Dedicated Screenshots & Recordings Showcase under Achievements
        self.media_widget = CompactMediaShowcaseWidget(content_widget)
        self.media_widget.screenshots_clicked.connect(self._on_screenshots)
        self.media_widget.videos_clicked.connect(self._on_videos)
        self.media_widget.settings_clicked.connect(self._on_settings_open)
        self.media_widget.open_screenshot_clicked.connect(self._on_open_screenshot)
        right_col.addWidget(self.media_widget, 0, Qt.AlignmentFlag.AlignTop)

        self.notes_widget = CompactNotesWidget(content_widget)
        right_col.addWidget(self.notes_widget, 0, Qt.AlignmentFlag.AlignTop)
        right_col.addStretch()

        dashboard_row.addLayout(right_col, 38)
        self.content_layout.addLayout(dashboard_row)
        self.content_layout.addStretch()

        self.scroll_area.setWidget(content_widget)
        outer_layout.addWidget(self.scroll_area)

        # Centered Empty Page State
        self.empty_page = QWidget(self)
        self.empty_page.setStyleSheet("background-color: #121214;")
        ep_layout = QVBoxLayout(self.empty_page)
        self.empty_page_lbl = QLabel("No games in this view")
        self.empty_page_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.empty_page_lbl.setStyleSheet("color: #71717A; font-size: 15px; font-weight: 500; background: transparent;")
        ep_layout.addStretch()
        ep_layout.addWidget(self.empty_page_lbl)
        ep_layout.addStretch()
        self.empty_page.setVisible(False)
        outer_layout.addWidget(self.empty_page)

    def set_empty_state(self, message: str = "No games in this view"):
        self.empty_page_lbl.setText(message)
        self.scroll_area.setVisible(False)
        self.empty_page.setVisible(True)

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
            self.set_empty_state("No games in this view")
            return

        self.empty_page.setVisible(False)
        self.scroll_area.setVisible(True)

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
            last_played = game_record[9] if len(game_record) > 9 else None
            tags = game_record[10] if len(game_record) > 10 else ""
            is_fav = bool(game_record[8]) if len(game_record) > 8 else False
            ver_override = game_record[15] if len(game_record) > 15 else ""
            p7 = game_record[7] if len(game_record) > 7 and isinstance(game_record[7], (int, float)) else 0
            p19 = game_record[19] if len(game_record) > 19 and isinstance(game_record[19], (int, float)) else 0
            playtime = p7 or p19 or 0

        self.current_game_id = g_id
        self.current_steam_id = s_id

        # 1. Hero Banner
        self.hero_banner.set_hero_data(hero_image_path, g_name, tags)

        # 2. Action Bar
        self.action_bar.set_play_state("running" if is_running else "play")

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
        full_exe = os.path.join(g_path, g_exe) if (g_path and g_exe) else (g_exe or "-")
        if full_exe and full_exe != "-":
            exe_name = os.path.basename(full_exe)
            parent_name = os.path.basename(os.path.dirname(full_exe))
            disp_path = f"{parent_name}/{exe_name}" if parent_name else exe_name
            self.lbl_path.setText(disp_path)
            self.lbl_path.setToolTip(full_exe)
        else:
            self.lbl_path.setText("-")
            self.lbl_path.setToolTip("")

        mode_name = g_mode.upper() if g_mode else "UMU"
        self.lbl_mode.setText(f"{mode_name} (Firejail sandbox active)")
        ver_str = ver_override if ver_override else (f"Steam AppID: {s_id}" if s_id else "Local game")
        self.lbl_version.setText(ver_str)

        # 5. Achievements Showcase
        self.ach_widget.set_achievements_data(unlocked, total, pct, recent_achievements, locked_achievements)

        # 6. Media Showcase
        self.media_widget.set_media_data(g_id, g_name)

        # 7. Notes
        self.notes_widget.load_notes_for_game(g_id)

    def set_play_state(self, state: str):
        """Update action bar play button visuals to idle, running, or stopping."""
        if hasattr(self, "action_bar") and self.action_bar:
            self.action_bar.set_play_state(state)

    def _on_play(self):
        if self.current_game_id is not None:
            self.play_requested.emit(self.current_game_id)

    def _on_edit(self):
        if self.current_game_id is not None:
            self.edit_requested.emit(self.current_game_id)

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

    def _on_screenshots(self, game_id: Optional[int] = None):
        gid = game_id or self.current_game_id
        if gid is not None:
            self.screenshots_requested.emit(gid)

    def _on_videos(self, game_id: Optional[int] = None):
        gid = game_id or self.current_game_id
        if gid is not None:
            self.videos_requested.emit(gid)

    def _on_settings_open(self):
        self.settings_requested.emit()

    def _on_open_screenshot(self, path: str):
        try:
            from ui.dialogs.settings_dialog import ScreenshotLightboxDialog
            dialog = ScreenshotLightboxDialog([path], 0, parent=self)
            dialog.exec()
        except Exception as e:
            logger.warning(f"Error opening screenshot lightbox: {e}")

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
        self.steam_id = game_tuple[6] if len(game_tuple) > 6 and game_tuple[6] else ""
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
        elif self.cloud_status in (SyncStatus.CLOUD_NEWER, SyncStatus.CLOUD_ONLY):
            self.cloud_lbl.setPixmap(get_icon("ph.cloud-arrow-down-fill", color="#FF9F0A").pixmap(12, 12))
            self.cloud_lbl.setToolTip("Cloud: Newer in cloud")
        elif self.cloud_status == SyncStatus.CONFLICT:
            self.cloud_lbl.setPixmap(get_icon("ph.cloud-warning-fill", color="#FF453A").pixmap(12, 12))
            self.cloud_lbl.setToolTip("Cloud: Conflict")
        elif self.cloud_status == SyncStatus.CLOUD_OFFLINE:
            self.cloud_lbl.setPixmap(get_icon("ph.cloud-slash-bold", color="#71717A").pixmap(12, 12))
            self.cloud_lbl.setToolTip("Cloud: Offline")
        else:
            self.cloud_lbl.setPixmap(get_icon("ph.cloud-slash-bold", color="#71717A").pixmap(12, 12))
            self.cloud_lbl.setToolTip("Cloud: Missing / no saves")

    def _load_icon(self):
        pix: Optional[QPixmap] = None
        if self.icon_url and os.path.exists(self.icon_url) and os.path.getsize(self.icon_url) > 0:
            loaded = QPixmap(self.icon_url)
            if not loaded.isNull():
                pix = loaded

        if pix is None and self.cache_dir:
            from core.steamgriddb_client import SteamGridDBClient
            full_exe = os.path.join(self.path, self.executable) if (self.path and self.executable) else ""
            art_key = SteamGridDBClient.get_artwork_key(
                steam_id=self.steam_id,
                game_name=self.name,
                exe_path=full_exe,
                game_id=self.game_id
            )
            icons_dir = os.path.join(os.path.dirname(self.cache_dir), "icons")
            # Try stable art_key first
            for ext in (".png", ".ico", ".jpg"):
                icon_path = os.path.join(icons_dir, f"icon_{art_key}{ext}")
                if os.path.exists(icon_path) and os.path.getsize(icon_path) > 0:
                    loaded = QPixmap(icon_path)
                    if not loaded.isNull():
                        pix = loaded
                        break

            # If no art_key icon and game is non-steam, try legacy game_id
            if pix is None and (not self.steam_id or str(self.steam_id) in ("0", "None", "")):
                for ext in (".png", ".ico", ".jpg"):
                    icon_path = os.path.join(icons_dir, f"icon_{self.game_id}{ext}")
                    if os.path.exists(icon_path) and os.path.getsize(icon_path) > 0:
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
    filter_changed = pyqtSignal(str)
    sort_changed = pyqtSignal(int)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("compactSidebarList")
        self.setMinimumWidth(220)
        self.setMaximumWidth(360)
        self.games_data: List[tuple] = []
        self.cache_dir: Optional[str] = None
        self.cloud_status_cache: Dict[int, Any] = {}
        self.active_filter = "all"

        self.setStyleSheet("""
            QFrame#compactSidebarList {
                background-color: #161618;
                border: none;
                border-right: 1px solid rgba(255, 255, 255, 0.06);
            }
        """)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(10, 12, 10, 12)
        layout.setSpacing(8)

        # ── Quick Filter Icons Bar (All, Installed, Favorites, Archived) ──
        filter_bar = QFrame()
        filter_bar.setStyleSheet("""
            QFrame {
                background-color: #1C1C20;
                border: none;
                border-radius: 6px;
            }
        """)
        fb_layout = QHBoxLayout(filter_bar)
        fb_layout.setContentsMargins(4, 3, 4, 3)
        fb_layout.setSpacing(4)

        btn_filter_style = """
            QPushButton {
                background: transparent;
                border: none;
                border-radius: 4px;
                padding: 4px;
                min-height: 20px;
            }
            QPushButton:hover {
                background: rgba(255, 255, 255, 0.08);
            }
            QPushButton:checked {
                background: rgba(255, 255, 255, 0.16);
            }
        """

        self.btn_f_all = QPushButton()
        self.btn_f_all.setIcon(get_icon("ph.squares-four-bold", color="#FFFFFF"))
        self.btn_f_all.setIconSize(QSize(15, 15))
        self.btn_f_all.setCheckable(True)
        self.btn_f_all.setChecked(True)
        self.btn_f_all.setToolTip("All Games")
        self.btn_f_all.setStyleSheet(btn_filter_style)
        self.btn_f_all.clicked.connect(lambda: self._on_filter_btn_clicked("all"))
        fb_layout.addWidget(self.btn_f_all)

        self.btn_f_inst = QPushButton()
        self.btn_f_inst.setIcon(get_icon("ph.check-circle-bold", color="#FFFFFF"))
        self.btn_f_inst.setIconSize(QSize(15, 15))
        self.btn_f_inst.setCheckable(True)
        self.btn_f_inst.setToolTip("Installed Games")
        self.btn_f_inst.setStyleSheet(btn_filter_style)
        self.btn_f_inst.clicked.connect(lambda: self._on_filter_btn_clicked("installed"))
        fb_layout.addWidget(self.btn_f_inst)

        self.btn_f_fav = QPushButton()
        self.btn_f_fav.setIcon(get_icon("ph.star-bold", color="#FFFFFF"))
        self.btn_f_fav.setIconSize(QSize(15, 15))
        self.btn_f_fav.setCheckable(True)
        self.btn_f_fav.setToolTip("Favorite Games")
        self.btn_f_fav.setStyleSheet(btn_filter_style)
        self.btn_f_fav.clicked.connect(lambda: self._on_filter_btn_clicked("favorites"))
        fb_layout.addWidget(self.btn_f_fav)

        self.btn_f_arch = QPushButton()
        self.btn_f_arch.setIcon(get_icon("ph.archive-bold", color="#FFFFFF"))
        self.btn_f_arch.setIconSize(QSize(15, 15))
        self.btn_f_arch.setCheckable(True)
        self.btn_f_arch.setToolTip("Archived Games")
        self.btn_f_arch.setStyleSheet(btn_filter_style)
        self.btn_f_arch.clicked.connect(lambda: self._on_filter_btn_clicked("archived"))
        fb_layout.addWidget(self.btn_f_arch)

        self._filter_buttons = [self.btn_f_all, self.btn_f_inst, self.btn_f_fav, self.btn_f_arch]
        layout.addWidget(filter_bar)

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

        # ── Sorting & Games Count Header Row ──
        sort_row = QHBoxLayout()
        sort_row.setContentsMargins(2, 2, 2, 2)
        sort_row.setSpacing(6)

        self.sort_combo = QComboBox()
        self.sort_combo.addItems(["Sort: A–Z Title", "Sort: Most Played", "Sort: Recently Added", "Sort: Disk Size", "Sort: Runner"])
        self.sort_combo.setFixedHeight(24)
        self.sort_combo.setCursor(Qt.CursorShape.PointingHandCursor)
        self.sort_combo.setStyleSheet("""
            QComboBox {
                background: transparent;
                color: #A1A1AA;
                border: none;
                border-radius: 4px;
                padding: 0 4px;
                font-size: 11px;
                font-weight: 500;
            }
            QComboBox:hover {
                color: #FFFFFF;
                background: rgba(255, 255, 255, 0.06);
            }
            QComboBox::drop-down { border: none; width: 14px; }
            QComboBox QAbstractItemView {
                background-color: #1C1C20;
                color: #FFFFFF;
                border: 1px solid rgba(255, 255, 255, 0.1);
                selection-background-color: rgba(255, 255, 255, 0.12);
                padding: 4px;
            }
        """)
        self.sort_combo.currentIndexChanged.connect(self._on_sort_combo_changed)
        sort_row.addWidget(self.sort_combo, stretch=1)

        # Clean vertical divider
        v_divider = QFrame()
        v_divider.setFixedSize(1, 14)
        v_divider.setStyleSheet("background-color: rgba(255, 255, 255, 0.1); border: none;")
        sort_row.addWidget(v_divider)

        # Games count label
        self.lbl_count = QLabel("0")
        self.lbl_count.setStyleSheet("color: #71717A; font-size: 10px; font-weight: 700; letter-spacing: 0.8px; background: transparent; padding-right: 4px;")
        sort_row.addWidget(self.lbl_count)

        layout.addLayout(sort_row)

        # Clean horizontal divider
        h_divider = QFrame()
        h_divider.setFixedHeight(1)
        h_divider.setStyleSheet("background-color: rgba(255, 255, 255, 0.06); border: none;")
        layout.addWidget(h_divider)

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

        self.empty_lbl = QLabel("No games found")
        self.empty_lbl.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.empty_lbl.setStyleSheet("color: #71717A; font-size: 12px; font-weight: 500; padding: 40px 12px; background: transparent;")
        self.empty_lbl.setWordWrap(True)
        self.empty_lbl.setVisible(False)
        layout.addWidget(self.empty_lbl)

    def _on_filter_btn_clicked(self, mode: str):
        self.active_filter = mode
        for btn in self._filter_buttons:
            btn.setChecked(False)
        if mode == "all":
            self.btn_f_all.setChecked(True)
        elif mode == "installed":
            self.btn_f_inst.setChecked(True)
        elif mode == "favorites":
            self.btn_f_fav.setChecked(True)
        elif mode == "archived":
            self.btn_f_arch.setChecked(True)
        self.filter_changed.emit(mode)

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
        self.lbl_count.setText(str(len(games)))
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

        if self.list_widget.count() == 0:
            if self.active_filter == "favorites":
                self.empty_lbl.setText("No favorite games added yet")
            elif self.active_filter == "archived":
                self.empty_lbl.setText("No archived games found")
            elif self.active_filter == "installed":
                self.empty_lbl.setText("No installed games found")
            elif query:
                self.empty_lbl.setText(f"No games matching '{query}'")
            else:
                self.empty_lbl.setText("No games added yet")
            self.empty_lbl.setVisible(True)
            self.list_widget.setVisible(False)
        else:
            self.empty_lbl.setVisible(False)
            self.list_widget.setVisible(True)

    def select_game(self, game_id: int):
        for i in range(self.list_widget.count()):
            item = self.list_widget.item(i)
            if item and item.data(Qt.ItemDataRole.UserRole) == game_id:
                self.list_widget.setCurrentItem(item)
                break

    def update_game_icon(self, game_id: int, icon_path: str):
        """Update the icon of a game in the sidebar list in real time."""
        for i in range(self.list_widget.count()):
            item = self.list_widget.item(i)
            if item and item.data(Qt.ItemDataRole.UserRole) == game_id:
                widget = self.list_widget.itemWidget(item)
                if isinstance(widget, CompactSidebarListItemWidget):
                    widget.icon_url = icon_path
                    widget._load_icon()
                break

    def _on_sort_combo_changed(self, idx: int):
        self.sort_changed.emit(idx)

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
    edit_requested = pyqtSignal(int)
    properties_requested = pyqtSignal(int)
    save_manager_requested = pyqtSignal(int)
    open_folder_requested = pyqtSignal(int)
    prefix_maintenance_requested = pyqtSignal(int)
    favorite_toggled = pyqtSignal(int)
    achievements_requested = pyqtSignal(int)
    steam_page_requested = pyqtSignal(str)
    filter_changed = pyqtSignal(str)
    sort_changed = pyqtSignal(int)
    screenshots_requested = pyqtSignal(int)
    videos_requested = pyqtSignal(int)
    settings_requested = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setStyleSheet("background: transparent;")

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
        self.sidebar_list.filter_changed.connect(self.filter_changed.emit)
        self.sidebar_list.sort_changed.connect(self.sort_changed.emit)
        self.splitter.addWidget(self.sidebar_list)

        # Right: Compact Game Detail Page
        self.game_page = CompactGamePageWidget(self.splitter)
        self.game_page.play_requested.connect(self.play_requested.emit)
        self.game_page.edit_requested.connect(self.edit_requested.emit)
        self.game_page.properties_requested.connect(self.properties_requested.emit)
        self.game_page.save_manager_requested.connect(self.save_manager_requested.emit)
        self.game_page.open_folder_requested.connect(self.open_folder_requested.emit)
        self.game_page.prefix_maintenance_requested.connect(self.prefix_maintenance_requested.emit)
        self.game_page.favorite_toggled.connect(self.favorite_toggled.emit)
        self.game_page.achievements_requested.connect(self.achievements_requested.emit)
        self.game_page.steam_page_requested.connect(self.steam_page_requested.emit)
        self.game_page.screenshots_requested.connect(self.screenshots_requested.emit)
        self.game_page.videos_requested.connect(self.videos_requested.emit)
        self.game_page.settings_requested.connect(self.settings_requested.emit)
        self.splitter.addWidget(self.game_page)

        self.splitter.setSizes([260, 920])
        main_layout.addWidget(self.splitter)
        self.sidebar = self.sidebar_list

    def set_play_state(self, state: str):
        """Update play button visual state in the compact game page."""
        if hasattr(self, "game_page") and self.game_page:
            self.game_page.set_play_state(state)

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

    def update_game_icon(self, game_id: int, icon_path: str):
        self.sidebar_list.update_game_icon(game_id, icon_path)


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
