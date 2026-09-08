"""
Dedicated Game Achievements Viewer Dialog for SafeLauncher.
Apple macOS / iOS styled dark glass interface featuring:
- Hero game banner & metadata header
- 3 Glass metric counter cards (Unlocked, Completion %, Locked)
- Segmented pill filter controls (All, Unlocked, Locked, Secret)
- Search bar with instant filtering & sort controls
- Polished achievement cards with high-fidelity badges and rich tooltips
- Spoiler protection with interactive reveal animations
"""

from __future__ import annotations

import os
import html
import datetime
from pathlib import Path
from typing import Optional, List, Dict, Any

from PyQt6.QtCore import Qt, QSize, pyqtSignal
from PyQt6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QLabel, QLineEdit,
    QPushButton, QScrollArea, QFrame, QProgressBar, QButtonGroup,
    QComboBox, QGraphicsDropShadowEffect, QSizePolicy, QApplication
)
from PyQt6.QtGui import QPixmap, QColor, QPainter, QFont, QIcon, QPainterPath

from database import GameDatabase, GameRecord
from core.achievement_schema import SteamAchievementFetcherWorker
from core.achievement_providers import AchievementAvailability
from core.logger import get_logger
from ui.icons import get_icon
from ui.components.popup_shell import PopupDialog

logger = get_logger("AchievementsDialog")


def create_rounded_pixmap(src_pixmap: QPixmap, size: QSize, radius: int = 12) -> QPixmap:
    """Return a crisp antialiased rounded pixmap."""
    if not src_pixmap or src_pixmap.isNull() or size.width() <= 0 or size.height() <= 0:
        return src_pixmap if src_pixmap else QPixmap()
    
    try:
        scaled = src_pixmap.scaled(
            size,
            Qt.AspectRatioMode.KeepAspectRatioByExpanding,
            Qt.TransformationMode.SmoothTransformation
        )
        if scaled.isNull():
            return src_pixmap
        
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
        
        x_offset = max(0, (scaled.width() - size.width()) // 2)
        y_offset = max(0, (scaled.height() - size.height()) // 2)
        painter.drawPixmap(0, 0, scaled, x_offset, y_offset, size.width(), size.height())
        painter.end()
        return out
    except Exception as e:
        logger.debug(f"Error in create_rounded_pixmap: {e}")
        return src_pixmap


class MetricCard(QFrame):
    """Apple-styled frosted glass metric counter card."""
    def __init__(self, title: str, value: str, subtext: str, accent_color: str = "#30D158", parent=None):
        super().__init__(parent)
        self.setObjectName("metricCard")
        self.setStyleSheet(f"""
            QFrame#metricCard {{
                background-color: #161A22;
                border: 1px solid rgba(255, 255, 255, 0.08);
                border-radius: 12px;
                padding: 10px 14px;
            }}
            QFrame#metricCard:hover {{
                background-color: #1A1F28;
                border-color: rgba(255, 255, 255, 0.14);
            }}
        """)
        
        layout = QVBoxLayout(self)
        layout.setContentsMargins(10, 8, 10, 8)
        layout.setSpacing(2)

        header_layout = QHBoxLayout()
        header_layout.setContentsMargins(0, 0, 0, 0)
        header_layout.setSpacing(6)

        dot = QLabel("•")
        dot.setStyleSheet(f"color: {accent_color}; font-size: 16px; font-weight: bold; background: transparent;")
        header_layout.addWidget(dot)

        self.title_lbl = QLabel(title.upper())
        self.title_lbl.setStyleSheet("color: #8E8E93; font-size: 10px; font-weight: 700; letter-spacing: 0.8px; background: transparent;")
        header_layout.addWidget(self.title_lbl)
        header_layout.addStretch()

        layout.addLayout(header_layout)

        self.val_lbl = QLabel(value)
        self.val_lbl.setStyleSheet(f"color: #FFFFFF; font-size: 20px; font-weight: 800; background: transparent;")
        layout.addWidget(self.val_lbl)

        self.sub_lbl = QLabel(subtext)
        self.sub_lbl.setStyleSheet("color: #636366; font-size: 11px; background: transparent;")
        layout.addWidget(self.sub_lbl)

    def update_values(self, value: str, subtext: str):
        self.val_lbl.setText(value)
        self.sub_lbl.setText(subtext)


class AppleAchievementCard(QFrame):
    """Polished Apple-styled achievement card with rich tooltips and spoiler protection."""
    def __init__(self, ach: Dict[str, Any], parent: Optional[QWidget] = None):
        super().__init__(parent)
        self.ach = ach
        self.revealed = False
        self._build_ui()

    def _build_ui(self):
        unlocked = bool(self.ach.get("unlocked", False))
        hidden = bool(self.ach.get("hidden", False)) and not unlocked
        api_name = self.ach.get("api_name", "UNKNOWN")
        display_name = self.ach.get("display_name", api_name)
        description = self.ach.get("description", "") or "No description available."
        unlock_time = float(self.ach.get("unlock_time", 0.0) or 0.0)

        self.setObjectName("appleAchievementCard")
        
        border_css = "rgba(48, 209, 88, 0.25)" if unlocked else "rgba(255, 255, 255, 0.06)"
        bg_css = "rgba(48, 209, 88, 0.04)" if unlocked else "rgba(255, 255, 255, 0.025)"
        hover_border_css = "rgba(48, 209, 88, 0.45)" if unlocked else "rgba(255, 255, 255, 0.15)"

        self.setStyleSheet(f"""
            QFrame#appleAchievementCard {{
                background-color: {bg_css};
                border: 1px solid {border_css};
                border-radius: 12px;
            }}
            QFrame#appleAchievementCard:hover {{
                background-color: #171B23;
                border: 1px solid {hover_border_css};
            }}
        """)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(12, 12, 14, 12)
        layout.setSpacing(14)

        # 1. Badge Icon Container
        self.icon_lbl = QLabel()
        self.icon_lbl.setFixedSize(52, 52)
        self.icon_lbl.setStyleSheet("background: transparent; border: none;")

        icon_path = self.ach.get("icon_path") if unlocked else (self.ach.get("icongray_path") or self.ach.get("icon_path"))
        loaded_pix = False
        if icon_path and os.path.isfile(icon_path):
            raw_pix = QPixmap(icon_path)
            if not raw_pix.isNull():
                self.icon_lbl.setPixmap(create_rounded_pixmap(raw_pix, QSize(52, 52), radius=10))
                loaded_pix = True

        if not loaded_pix:
            fallback = QPixmap(52, 52)
            fallback.fill(QColor("#1C1C1E" if not unlocked else "#063D24"))
            painter = QPainter(fallback)
            painter.setRenderHint(QPainter.RenderHint.Antialiasing)
            painter.setPen(QColor("#30D158" if unlocked else "#636366"))
            font = QFont("Arial", 10, QFont.Weight.Bold)
            painter.setFont(font)
            painter.drawText(fallback.rect(), Qt.AlignmentFlag.AlignCenter, "EARNED" if unlocked else "LOCKED")
            painter.end()
            self.icon_lbl.setPixmap(create_rounded_pixmap(fallback, QSize(52, 52), radius=10))

        layout.addWidget(self.icon_lbl)

        # 2. Text Details
        text_layout = QVBoxLayout()
        text_layout.setContentsMargins(0, 0, 0, 0)
        text_layout.setSpacing(3)

        # Header Row: Title + Status Pill Badge
        header_row = QHBoxLayout()
        header_row.setContentsMargins(0, 0, 0, 0)
        header_row.setSpacing(8)

        title_text = "Secret Achievement" if hidden and not self.revealed else display_name
        self.title_lbl = QLabel(title_text)
        title_color = "#FFFFFF" if unlocked else "#C7C7CC"
        self.title_lbl.setStyleSheet(f"color: {title_color}; font-size: 13px; font-weight: 700; background: transparent;")
        header_row.addWidget(self.title_lbl)
        header_row.addStretch()

        # Status Pill
        if unlocked and unlock_time > 1000:
            dt_str = datetime.datetime.fromtimestamp(unlock_time).strftime("%b %d, %Y · %H:%M")
            status_text = f"UNLOCKED {dt_str}"
            pill_style = "background-color: rgba(48, 209, 88, 0.15); color: #30D158; border: 1px solid rgba(48, 209, 88, 0.3);"
        elif unlocked:
            status_text = "UNLOCKED"
            pill_style = "background-color: rgba(48, 209, 88, 0.15); color: #30D158; border: 1px solid rgba(48, 209, 88, 0.3);"
        elif hidden and not self.revealed:
            status_text = "SECRET"
            pill_style = "background-color: rgba(10, 132, 255, 0.12); color: #0A84FF; border: 1px solid rgba(10, 132, 255, 0.25);"
        else:
            status_text = "LOCKED"
            pill_style = "background-color: #171B23; color: #8E8E93; border: 1px solid rgba(255, 255, 255, 0.08);"

        self.pill_lbl = QLabel(f" {status_text} ")
        self.pill_lbl.setStyleSheet(f"{pill_style} font-size: 9px; font-weight: 700; border-radius: 4px; padding: 2px 6px; letter-spacing: 0.5px;")
        header_row.addWidget(self.pill_lbl)

        text_layout.addLayout(header_row)

        # Description
        if hidden and not self.revealed:
            desc_text = "Details for this achievement are hidden until unlocked in gameplay."
        else:
            desc_text = description

        self.desc_lbl = QLabel(desc_text)
        self.desc_lbl.setStyleSheet("color: #8E8E93; font-size: 11px; line-height: 1.3; background: transparent;")
        self.desc_lbl.setWordWrap(True)
        text_layout.addWidget(self.desc_lbl)

        layout.addLayout(text_layout, 1)

        # 3. Reveal Button for Hidden Achievements
        if hidden:
            self.btn_reveal = QPushButton("Reveal")
            self.btn_reveal.setFixedHeight(26)
            self.btn_reveal.setStyleSheet("""
                QPushButton {
                    background-color: #202633;
                    color: #0A84FF;
                    border: 1px solid rgba(10, 132, 255, 0.3);
                    border-radius: 13px;
                    padding: 0 12px;
                    font-size: 11px;
                    font-weight: 600;
                }
                QPushButton:hover {
                    background-color: rgba(10, 132, 255, 0.2);
                    color: #FFFFFF;
                }
            """)
            self.btn_reveal.clicked.connect(self._toggle_reveal)
            layout.addWidget(self.btn_reveal)

        # 4. Rich Formatted Tooltip
        self._update_rich_tooltip()

    def _update_rich_tooltip(self):
        unlocked = bool(self.ach.get("unlocked", False))
        api_name = self.ach.get("api_name", "UNKNOWN")
        display_name = self.ach.get("display_name", api_name)
        description = self.ach.get("description", "") or "No description available."
        unlock_time = float(self.ach.get("unlock_time", 0.0) or 0.0)
        hidden = bool(self.ach.get("hidden", False))

        if unlocked and unlock_time > 1000:
            dt_str = datetime.datetime.fromtimestamp(unlock_time).strftime("%A, %B %d, %Y at %H:%M:%S")
            time_line = f"<p style='color: #30D158; font-weight: bold; margin-top: 6px;'>Unlocked on {dt_str}</p>"
        elif unlocked:
            time_line = "<p style='color: #30D158; font-weight: bold; margin-top: 6px;'>Unlocked</p>"
        elif hidden:
            time_line = "<p style='color: #0A84FF; font-weight: bold; margin-top: 6px;'>Secret Achievement</p>"
        else:
            time_line = "<p style='color: #8E8E93; font-weight: bold; margin-top: 6px;'>Locked</p>"

        tip_html = f"""
        <div style='background-color: #1C1C1E; color: #FFFFFF; font-family: sans-serif; padding: 4px;'>
            <b style='font-size: 13px; color: #F5F5F7;'>{html.escape(display_name)}</b>
            <p style='color: #A1A1A6; font-size: 11px; margin: 4px 0 0 0;'>{html.escape(description)}</p>
            <p style='color: #636366; font-size: 9px; margin: 4px 0 0 0;'>API Name: <code>{html.escape(api_name)}</code></p>
            {time_line}
        </div>
        """
        self.setToolTip(tip_html)

    def _toggle_reveal(self):
        self.revealed = not self.revealed
        display_name = self.ach.get("display_name", self.ach.get("api_name", ""))
        desc_text = self.ach.get("description", "") or "No description available."
        
        if self.revealed:
            self.title_lbl.setText(display_name)
            self.desc_lbl.setText(desc_text)
            if hasattr(self, "btn_reveal"):
                self.btn_reveal.setText("Hide")
                self.btn_reveal.setStyleSheet("""
                    QPushButton {
                        background-color: #171B23;
                        color: #8E8E93;
                        border: 1px solid rgba(255, 255, 255, 0.1);
                        border-radius: 13px;
                        padding: 0 12px;
                        font-size: 11px;
                        font-weight: 600;
                    }
                    QPushButton:hover {
                        background-color: #252B35;
                        color: #FFFFFF;
                    }
                """)
        else:
            self.title_lbl.setText("Secret Achievement")
            self.desc_lbl.setText("Details for this achievement are hidden until unlocked in gameplay.")
            if hasattr(self, "btn_reveal"):
                self.btn_reveal.setText("Reveal")
                self.btn_reveal.setStyleSheet("""
                    QPushButton {
                        background-color: #202633;
                        color: #0A84FF;
                        border: 1px solid rgba(10, 132, 255, 0.3);
                        border-radius: 13px;
                        padding: 0 12px;
                        font-size: 11px;
                        font-weight: 600;
                    }
                    QPushButton:hover {
                        background-color: rgba(10, 132, 255, 0.2);
                        color: #FFFFFF;
                    }
                """)


# Backward compatibility alias
AchievementCard = AppleAchievementCard


class AchievementsDialog(PopupDialog):
    """
    Apple macOS styled Game Achievements Viewer.
    Features frosted glass metric cards, segmented filter pills, search, and rich tooltips.
    """
    def __init__(self, game: Any, db: GameDatabase, parent: Optional[QWidget] = None):
        initial_name = (
            game[1] if isinstance(game, (tuple, list)) else
            game.get("name", "Achievements") if isinstance(game, dict) else
            getattr(game, "name", "Achievements")
        )
        super().__init__(f"Achievements - {initial_name}", parent)
        self.game = game
        self.db = db

        if isinstance(game, (list, tuple)):
            self.game_id = game[0]
            self.game_name = game[1]
            self.game_path = game[2] if len(game) > 2 else ""
            self.game_steam_id = str(game[6]) if len(game) > 6 and game[6] else ""
            self.game_banner = game[5] if len(game) > 5 and game[5] else ""
            self.game_proton_path = game[12] if len(game) > 12 and game[12] else ""
        elif isinstance(game, dict):
            self.game_id = game.get("id", 0)
            self.game_name = game.get("name", "Unknown Game")
            self.game_path = game.get("path", "")
            self.game_steam_id = str(game.get("steam_id", "") or "")
            self.game_banner = game.get("banner_url", "")
            self.game_proton_path = game.get("proton_path", "")
        else:
            self.game_id = getattr(game, "id", 0)
            self.game_name = getattr(game, "name", "Unknown Game")
            self.game_path = getattr(game, "path", "")
            self.game_steam_id = str(getattr(game, "steam_id", "") or "")
            self.game_banner = getattr(game, "banner_url", "")
            self.game_proton_path = getattr(game, "proton_path", "")

        self.achievements: List[Dict[str, Any]] = []
        self.current_filter = "all"
        self.search_query = ""
        self.sort_mode = "unlocked_first"

        self.resize(780, 680)
        self.setMinimumSize(680, 540)
        
        self.setStyleSheet("""
            QDialog {
                background-color: #0B0B0E;
                color: #FFFFFF;
            }
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
                min-height: 20px;
            }
            QScrollBar::handle:vertical:hover {
                background: rgba(255, 255, 255, 0.25);
            }
            QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {
                height: 0px;
            }
            QLineEdit {
                background-color: #171B23;
                border: 1px solid rgba(255, 255, 255, 0.09);
                border-radius: 10px;
                padding: 7px 14px;
                color: #FFFFFF;
                font-size: 13px;
            }
            QLineEdit:focus {
                background-color: #202633;
                border: 1px solid #0A84FF;
            }
            QComboBox {
                background-color: #171B23;
                border: 1px solid rgba(255, 255, 255, 0.09);
                border-radius: 10px;
                padding: 6px 12px;
                color: #E5E5EA;
                font-size: 12px;
                font-weight: 500;
            }
            QComboBox::drop-down {
                border: none;
                width: 20px;
            }
            QComboBox QAbstractItemView {
                background-color: #1C1C1E;
                border: 1px solid rgba(255, 255, 255, 0.1);
                selection-background-color: #0A84FF;
                color: #FFFFFF;
                border-radius: 8px;
                padding: 4px;
            }
        """)

        self._build_ui()
        self._load_and_sync_achievements()

    def _build_ui(self):
        main_layout = self.popup_layout(margins=(24, 20, 24, 20), spacing=16)

        # -------------------------------------------------------------
        # 1. Header Hero Card with Game Title & Sync Action
        # -------------------------------------------------------------
        hero_frame = QFrame()
        hero_frame.setStyleSheet("""
            QFrame {
                background: qlineargradient(x1:0, y1:0, x2:1, y2:0, stop:0 rgba(255, 255, 255, 0.05), stop:1 rgba(255, 255, 255, 0.02));
                border: 1px solid rgba(255, 255, 255, 0.08);
                border-radius: 14px;
                padding: 12px 16px;
            }
        """)
        hero_layout = QHBoxLayout(hero_frame)
        hero_layout.setContentsMargins(12, 8, 12, 8)
        hero_layout.setSpacing(16)

        # Game Cover thumbnail
        self.cover_lbl = QLabel()
        self.cover_lbl.setFixedSize(48, 64)
        self.cover_lbl.setStyleSheet("background-color: #1C1C1E; border-radius: 6px;")
        if self.game_banner and os.path.isfile(self.game_banner):
            pix = QPixmap(self.game_banner)
            if not pix.isNull():
                self.cover_lbl.setPixmap(create_rounded_pixmap(pix, QSize(48, 64), radius=6))
        hero_layout.addWidget(self.cover_lbl)

        # Title & Sub-pills
        title_vbox = QVBoxLayout()
        title_vbox.setContentsMargins(0, 0, 0, 0)
        title_vbox.setSpacing(4)

        self.title_lbl = QLabel(self.game_name)
        self.title_lbl.setStyleSheet("font-size: 19px; font-weight: 800; color: #FFFFFF; background: transparent;")
        title_vbox.addWidget(self.title_lbl)

        sub_row = QHBoxLayout()
        sub_row.setContentsMargins(0, 0, 0, 0)
        sub_row.setSpacing(8)

        if self.game_steam_id:
            appid_pill = QLabel(f" Steam AppID: {self.game_steam_id} ")
            appid_pill.setStyleSheet("background: #1A1F28; color: #8E8E93; font-size: 10px; font-weight: 700; border-radius: 4px; padding: 2px 6px;")
            sub_row.addWidget(appid_pill)

        self.status_tag = QLabel(" Synchronized ")
        self.status_tag.setStyleSheet("background: rgba(48, 209, 88, 0.12); color: #30D158; font-size: 10px; font-weight: 700; border-radius: 4px; padding: 2px 6px;")
        sub_row.addWidget(self.status_tag)
        sub_row.addStretch()

        title_vbox.addLayout(sub_row)
        hero_layout.addLayout(title_vbox, 1)

        # Sync / Refresh Button
        self.btn_refresh = QPushButton("Sync Achievements")
        self.btn_refresh.setIcon(get_icon("ph.arrows-clockwise-bold", color="#FFFFFF"))
        self.btn_refresh.setIconSize(QSize(14, 14))
        self.btn_refresh.setFixedHeight(34)
        self.btn_refresh.setStyleSheet("""
            QPushButton {
                background-color: #202633;
                border: 1px solid rgba(255, 255, 255, 0.12);
                border-radius: 8px;
                padding: 0 16px;
                color: #FFFFFF;
                font-size: 12px;
                font-weight: 600;
            }
            QPushButton:hover {
                background-color: rgba(255, 255, 255, 0.14);
                border-color: rgba(255, 255, 255, 0.22);
            }
            QPushButton:pressed {
                background-color: #171B23;
            }
        """)
        self.btn_refresh.clicked.connect(self._load_and_sync_achievements)
        hero_layout.addWidget(self.btn_refresh)

        main_layout.addWidget(hero_frame)

        # -------------------------------------------------------------
        # 2. Apple Glass Metric Counters Row
        # -------------------------------------------------------------
        metrics_layout = QHBoxLayout()
        metrics_layout.setContentsMargins(0, 0, 0, 0)
        metrics_layout.setSpacing(12)

        self.card_unlocked = MetricCard("Unlocked", "0", "0 of 0 Badges", accent_color="#30D158")
        self.card_progress = MetricCard("Progress", "0%", "Completion Rate", accent_color="#0A84FF")
        self.card_locked = MetricCard("Locked", "0", "Remaining Badges", accent_color="#8E8E93")

        metrics_layout.addWidget(self.card_unlocked)
        metrics_layout.addWidget(self.card_progress)
        metrics_layout.addWidget(self.card_locked)

        main_layout.addLayout(metrics_layout)

        # Sleek Apple-style Progress Bar
        self.progress_bar = QProgressBar()
        self.progress_bar.setFixedHeight(6)
        self.progress_bar.setTextVisible(False)
        self.progress_bar.setRange(0, 100)
        self.progress_bar.setValue(0)
        self.progress_bar.setStyleSheet("""
            QProgressBar {
                background-color: #1A1F28;
                border: none;
                border-radius: 3px;
            }
            QProgressBar::chunk {
                background: qlineargradient(x1:0, y1:0, x2:1, y2:0, stop:0 #30D158, stop:1 #34C759);
                border-radius: 3px;
            }
        """)
        main_layout.addWidget(self.progress_bar)

        # -------------------------------------------------------------
        # 3. Apple Segmented Control Filter Tabs & Search
        # -------------------------------------------------------------
        controls_layout = QHBoxLayout()
        controls_layout.setContentsMargins(0, 4, 0, 0)
        controls_layout.setSpacing(10)

        # Segmented Control Container
        seg_container = QFrame()
        seg_container.setStyleSheet("""
            QFrame {
                background-color: #171B23;
                border: 1px solid rgba(255, 255, 255, 0.08);
                border-radius: 9px;
                padding: 2px;
            }
        """)
        seg_layout = QHBoxLayout(seg_container)
        seg_layout.setContentsMargins(2, 2, 2, 2)
        seg_layout.setSpacing(2)

        self.btn_tab_all = self._create_segment_button("All", is_checked=True)
        self.btn_tab_unlocked = self._create_segment_button("Unlocked")
        self.btn_tab_locked = self._create_segment_button("Locked")
        self.btn_tab_secret = self._create_segment_button("Secret")

        self.segment_group = QButtonGroup(self)
        self.segment_group.addButton(self.btn_tab_all, 0)
        self.segment_group.addButton(self.btn_tab_unlocked, 1)
        self.segment_group.addButton(self.btn_tab_locked, 2)
        self.segment_group.addButton(self.btn_tab_secret, 3)

        seg_layout.addWidget(self.btn_tab_all)
        seg_layout.addWidget(self.btn_tab_unlocked)
        seg_layout.addWidget(self.btn_tab_locked)
        seg_layout.addWidget(self.btn_tab_secret)

        self.segment_group.idClicked.connect(self._on_filter_changed)
        controls_layout.addWidget(seg_container)

        controls_layout.addStretch()

        # Sort combo
        self.combo_sort = QComboBox()
        self.combo_sort.addItem("Default Order", "default")
        self.combo_sort.addItem("Unlocked First", "unlocked_first")
        self.combo_sort.addItem("Name (A-Z)", "name_asc")
        self.combo_sort.currentIndexChanged.connect(self._on_sort_changed)
        controls_layout.addWidget(self.combo_sort)

        # Search Bar
        self.search_edit = QLineEdit()
        self.search_edit.setPlaceholderText("Filter achievements...")
        self.search_edit.setFixedWidth(210)
        self.search_edit.textChanged.connect(self._on_search_changed)
        controls_layout.addWidget(self.search_edit)

        main_layout.addLayout(controls_layout)

        # -------------------------------------------------------------
        # 4. Scrollable Container for Achievement Cards
        # -------------------------------------------------------------
        self.scroll_area = QScrollArea()
        self.scroll_area.setWidgetResizable(True)
        
        self.cards_container = QWidget()
        self.cards_layout = QVBoxLayout(self.cards_container)
        self.cards_layout.setContentsMargins(0, 0, 0, 0)
        self.cards_layout.setSpacing(8)
        self.cards_layout.addStretch()

        self.scroll_area.setWidget(self.cards_container)
        main_layout.addWidget(self.scroll_area, 1)

        # -------------------------------------------------------------
        # 5. Footer Dialog Buttons
        # -------------------------------------------------------------
        footer_layout = QHBoxLayout()
        footer_layout.setContentsMargins(0, 0, 0, 0)

        self.stats_footer_lbl = QLabel("")
        self.stats_footer_lbl.setStyleSheet("color: #636366; font-size: 11px;")
        footer_layout.addWidget(self.stats_footer_lbl)
        footer_layout.addStretch()

        btn_close = QPushButton("Done")
        btn_close.setFixedHeight(34)
        btn_close.setStyleSheet("""
            QPushButton {
                background-color: #0A84FF;
                border: none;
                border-radius: 8px;
                padding: 0 24px;
                color: #FFFFFF;
                font-size: 12px;
                font-weight: 700;
            }
            QPushButton:hover {
                background-color: #0071E3;
            }
            QPushButton:pressed {
                background-color: #005BB5;
            }
        """)
        btn_close.clicked.connect(self.accept)
        footer_layout.addWidget(btn_close)

        main_layout.addLayout(footer_layout)

    def _create_segment_button(self, text: str, is_checked: bool = False) -> QPushButton:
        btn = QPushButton(text)
        btn.setCheckable(True)
        btn.setChecked(is_checked)
        btn.setFixedHeight(28)
        btn.setStyleSheet("""
            QPushButton {
                background-color: transparent;
                color: #8E8E93;
                border: none;
                border-radius: 7px;
                padding: 0 12px;
                font-size: 11px;
                font-weight: 600;
            }
            QPushButton:hover {
                color: #FFFFFF;
            }
            QPushButton:checked {
                background-color: rgba(255, 255, 255, 0.12);
                color: #FFFFFF;
                font-weight: 700;
            }
        """)
        return btn

    def _load_and_sync_achievements(self):
        """Render cached achievements, then resolve local/Steam state once."""
        app_id = self.game_steam_id.strip() if self.game_steam_id else ""
        if not app_id:
            self.status_tag.setText(" No Steam AppID ")
            self.status_tag.setStyleSheet("background: rgba(255, 69, 58, 0.15); color: #FF453A; font-size: 10px; font-weight: 700; border-radius: 4px; padding: 2px 6px;")
            self._render_cards()
            return

        # Show the last known schema immediately, but always let the shared
        # resolver reconcile it with local state and authenticated Steam.
        db_achs = self.db.get_game_achievements(self.game_id)
        if db_achs:
            self.achievements = db_achs
            self.status_tag.setText(" Cached · checking… ")
            self.status_tag.setStyleSheet("background: rgba(10, 132, 255, 0.15); color: #0A84FF; font-size: 10px; font-weight: 700; border-radius: 4px; padding: 2px 6px;")
            self._render_cards()

        self.status_tag.setText(" Resolving achievement data… ")
        self.status_tag.setStyleSheet("background: rgba(10, 132, 255, 0.15); color: #0A84FF; font-size: 10px; font-weight: 700; border-radius: 4px; padding: 2px 6px;")
        self.fetch_worker = SteamAchievementFetcherWorker(
            self.game_id,
            app_id,
            game_path=self.game_path,
            proton_path=self.game_proton_path,
            download_icons=True,
            parent=self
        )
        self.fetch_worker.resolution_ready.connect(self._on_resolution_ready)
        self.fetch_worker.failed.connect(self._on_schema_failed)
        self.fetch_worker.start()

    def _on_resolution_ready(self, game_id: int, app_id: str, resolution):
        if game_id != self.game_id:
            return
        if resolution.schema:
            self.db.save_achievement_schema(game_id, app_id, resolution.schema)
        if resolution.state:
            self.db.unlock_achievements_batch(game_id, resolution.state)
        self.achievements = self.db.get_game_achievements(self.game_id)

        if resolution.availability == AchievementAvailability.AVAILABLE:
            self.status_tag.setText(" Synchronized ")
            self.status_tag.setStyleSheet("background: rgba(48, 209, 88, 0.12); color: #30D158; font-size: 10px; font-weight: 700; border-radius: 4px; padding: 2px 6px;")
        elif resolution.state:
            self.status_tag.setText(" Local state · schema unavailable ")
            self.status_tag.setStyleSheet("background: rgba(255, 159, 10, 0.15); color: #FF9F0A; font-size: 10px; font-weight: 700; border-radius: 4px; padding: 2px 6px;")
        else:
            self.status_tag.setText(" Achievement data unavailable ")
            self.status_tag.setStyleSheet("background: rgba(255, 159, 10, 0.15); color: #FF9F0A; font-size: 10px; font-weight: 700; border-radius: 4px; padding: 2px 6px;")
        self._render_cards()

    def _on_schema_fetched(self, game_id: int, app_id: str, achs_list: list):
        if game_id == self.game_id:
            self.db.save_achievement_schema(game_id, app_id, achs_list)
            self.achievements = self.db.get_game_achievements(self.game_id)
            self.status_tag.setText(" Synchronized ")
            self.status_tag.setStyleSheet("background: rgba(48, 209, 88, 0.12); color: #30D158; font-size: 10px; font-weight: 700; border-radius: 4px; padding: 2px 6px;")
            self._render_cards()

    def _on_schema_failed(self, game_id: int, app_id: str, error: str):
        if game_id == self.game_id:
            # Missing schema is not an empty achievement list.  Keep any
            # already cached cards visible and explain the unavailable source.
            self.status_tag.setText(" Achievement data unavailable ")
            self.status_tag.setStyleSheet("background: rgba(255, 159, 10, 0.15); color: #FF9F0A; font-size: 10px; font-weight: 700; border-radius: 4px; padding: 2px 6px;")
            self._render_cards()

    def _on_search_changed(self, text: str):
        self.search_query = text.strip().lower()
        self._render_cards()

    def _on_filter_changed(self, button_id: int):
        if button_id == 1:
            self.current_filter = "unlocked"
        elif button_id == 2:
            self.current_filter = "locked"
        elif button_id == 3:
            self.current_filter = "secret"
        else:
            self.current_filter = "all"
        self._render_cards()

    def _on_sort_changed(self, index: int):
        self.sort_mode = self.combo_sort.currentData() or "default"
        self._render_cards()

    def _render_cards(self):
        # Clear existing cards
        while self.cards_layout.count() > 1:
            item = self.cards_layout.takeAt(0)
            w = item.widget()
            if w:
                w.deleteLater()

        unlocked_count = sum(1 for a in self.achievements if a.get("unlocked"))
        total_count = len(self.achievements)
        locked_count = total_count - unlocked_count
        secret_count = sum(1 for a in self.achievements if a.get("hidden"))
        pct = round((unlocked_count / total_count) * 100.0, 1) if total_count > 0 else 0.0

        # Update Metric Counter Cards
        self.card_unlocked.update_values(str(unlocked_count), f"{unlocked_count} of {total_count} Badges")
        self.card_progress.update_values(f"{int(pct)}%", f"{pct}% Completed")
        self.card_locked.update_values(str(locked_count), f"{locked_count} Remaining")
        self.progress_bar.setValue(int(pct))

        # Update Segment Tabs Labels with Counts
        self.btn_tab_all.setText(f"All ({total_count})")
        self.btn_tab_unlocked.setText(f"Unlocked ({unlocked_count})")
        self.btn_tab_locked.setText(f"Locked ({locked_count})")
        self.btn_tab_secret.setText(f"Secret ({secret_count})")

        filtered = []
        for a in self.achievements:
            unlocked = bool(a.get("unlocked", False))
            hidden = bool(a.get("hidden", False))

            if self.current_filter == "unlocked" and not unlocked:
                continue
            if self.current_filter == "locked" and unlocked:
                continue
            if self.current_filter == "secret" and not hidden:
                continue

            if self.search_query:
                name_match = self.search_query in a.get("display_name", "").lower()
                desc_match = self.search_query in a.get("description", "").lower()
                api_match = self.search_query in a.get("api_name", "").lower()
                if not (name_match or desc_match or api_match):
                    continue

            filtered.append(a)

        # Sort
        if self.sort_mode == "unlocked_first":
            filtered.sort(key=lambda x: (not x.get("unlocked", False), -float(x.get("unlock_time", 0.0) or 0.0), x.get("display_name", "")))
        elif self.sort_mode == "name_asc":
            filtered.sort(key=lambda x: x.get("display_name", "").lower())

        if not filtered:
            no_match = QFrame()
            no_match.setStyleSheet("""
                QFrame {
                    background-color: #10141B;
                    border: 1px dashed rgba(255, 255, 255, 0.08);
                    border-radius: 12px;
                    padding: 30px;
                }
            """)
            no_layout = QVBoxLayout(no_match)
            no_layout.setSpacing(6)
            
            lbl_msg = QLabel("No Achievements Found")
            lbl_msg.setStyleSheet("color: #FFFFFF; font-size: 14px; font-weight: 700;")
            lbl_msg.setAlignment(Qt.AlignmentFlag.AlignCenter)
            no_layout.addWidget(lbl_msg)

            lbl_sub = QLabel("Try adjusting your filter or search query.")
            lbl_sub.setStyleSheet("color: #8E8E93; font-size: 12px;")
            lbl_sub.setAlignment(Qt.AlignmentFlag.AlignCenter)
            no_layout.addWidget(lbl_sub)

            self.cards_layout.insertWidget(0, no_match)
        else:
            for ach in filtered:
                card = AppleAchievementCard(ach, self.cards_container)
                self.cards_layout.insertWidget(self.cards_layout.count() - 1, card)

        self.stats_footer_lbl.setText(f"Showing {len(filtered)} of {total_count} achievements")
