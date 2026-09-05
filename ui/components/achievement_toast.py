"""
In-App and Desktop Achievement Unlock Notification Banner.
Renders an animated floating overlay notification for unlocked achievements
and triggers Freedesktop system notifications via notify-send.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path
from typing import Optional

from PyQt6.QtCore import Qt, QTimer, QPropertyAnimation, QEasingCurve, QPoint
from PyQt6.QtWidgets import (
    QWidget, QLabel, QHBoxLayout, QVBoxLayout, QFrame, QGraphicsOpacityEffect, QApplication
)
from PyQt6.QtGui import QPixmap, QIcon, QPainter, QColor, QFont

from core.logger import get_logger

logger = get_logger("AchievementToast")


def send_desktop_notification(title: str, message: str, icon_path: Optional[str] = None):
    """Trigger native desktop notification via notify-send or Freedesktop DBus."""
    if shutil.which("notify-send"):
        try:
            cmd = ["notify-send", "-a", "SafeLauncher", "-u", "normal", title, message]
            if icon_path and os.path.isfile(icon_path):
                cmd.extend(["-i", icon_path])
            subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        except Exception as e:
            logger.debug(f"Desktop notification failed: {e}")


class AchievementToast(QWidget):
    """
    Floating overlay banner that animates onto the screen when an achievement unlocks.
    """
    def __init__(
        self,
        display_name: str,
        description: str,
        icon_path: Optional[str] = None,
        duration_ms: int = 5000,
        parent: Optional[QWidget] = None
    ):
        super().__init__(parent, Qt.WindowType.FramelessWindowHint | Qt.WindowType.WindowStaysOnTopHint | Qt.WindowType.Tool)
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose, True)

        self.display_name = display_name
        self.description = description
        self.icon_path = icon_path
        self.duration_ms = duration_ms

        self._build_ui()

    def _build_ui(self):
        root_layout = QVBoxLayout(self)
        root_layout.setContentsMargins(10, 10, 10, 10)

        # Card container
        self.card = QFrame(self)
        self.card.setStyleSheet("""
            QFrame {
                background-color: #121214;
                border: 1px solid #27272A;
                border-left: 4px solid #10B981;
                border-radius: 8px;
            }
        """)
        card_layout = QHBoxLayout(self.card)
        card_layout.setContentsMargins(12, 10, 16, 10)
        card_layout.setSpacing(12)

        # Badge Icon
        self.icon_lbl = QLabel()
        self.icon_lbl.setFixedSize(48, 48)
        self.icon_lbl.setStyleSheet("background: transparent; border: none;")

        loaded_pix = False
        if self.icon_path and os.path.isfile(self.icon_path):
            pix = QPixmap(self.icon_path)
            if not pix.isNull():
                self.icon_lbl.setPixmap(pix.scaled(48, 48, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation))
                loaded_pix = True

        if not loaded_pix:
            # Fallback styled badge
            fallback = QPixmap(48, 48)
            fallback.fill(QColor("#1E293B"))
            painter = QPainter(fallback)
            painter.setPen(QColor("#10B981"))
            font = QFont("Arial", 11, QFont.Weight.Bold)
            painter.setFont(font)
            painter.drawText(fallback.rect(), Qt.AlignmentFlag.AlignCenter, "ACH")
            painter.end()
            self.icon_lbl.setPixmap(fallback)

        card_layout.addWidget(self.icon_lbl)

        # Text Details
        text_layout = QVBoxLayout()
        text_layout.setSpacing(2)

        header_lbl = QLabel("ACHIEVEMENT UNLOCKED")
        header_lbl.setStyleSheet("color: #10B981; font-size: 10px; font-weight: bold; letter-spacing: 1px; background: transparent; border: none;")
        text_layout.addWidget(header_lbl)

        title_lbl = QLabel(self.display_name)
        title_lbl.setStyleSheet("color: #F4F4F5; font-size: 13px; font-weight: bold; background: transparent; border: none;")
        text_layout.addWidget(title_lbl)

        if self.description:
            desc_lbl = QLabel(self.description)
            desc_lbl.setStyleSheet("color: #A1A1AA; font-size: 11px; background: transparent; border: none;")
            desc_lbl.setWordWrap(True)
            desc_lbl.setMaximumWidth(260)
            text_layout.addWidget(desc_lbl)

        card_layout.addLayout(text_layout)
        root_layout.addWidget(self.card)

        self.adjustSize()

    def show_animated(self, parent_widget: Optional[QWidget] = None):
        """Position toast in top-right or screen corner and show with fade-in."""
        if parent_widget:
            p_geo = parent_widget.geometry()
            target_x = p_geo.x() + p_geo.width() - self.width() - 20
            target_y = p_geo.y() + 40
        else:
            screen = QApplication.primaryScreen()
            if screen:
                s_geo = screen.availableGeometry()
                target_x = s_geo.x() + s_geo.width() - self.width() - 24
                target_y = s_geo.y() + 48
            else:
                target_x, target_y = 100, 100

        self.move(target_x, target_y)

        # Fade in opacity animation
        self.opacity_effect = QGraphicsOpacityEffect(self)
        self.setGraphicsEffect(self.opacity_effect)

        self.fade_anim = QPropertyAnimation(self.opacity_effect, b"opacity")
        self.fade_anim.setDuration(300)
        self.fade_anim.setStartValue(0.0)
        self.fade_anim.setEndValue(1.0)
        self.fade_anim.setEasingCurve(QEasingCurve.Type.OutCubic)

        self.show()
        self.fade_anim.start()

        # Auto-dismiss timer
        QTimer.singleShot(self.duration_ms, self._fade_out)

    def _fade_out(self):
        if not self.isVisible():
            return
        self.fade_out_anim = QPropertyAnimation(self.opacity_effect, b"opacity")
        self.fade_out_anim.setDuration(400)
        self.fade_out_anim.setStartValue(1.0)
        self.fade_out_anim.setEndValue(0.0)
        self.fade_out_anim.finished.connect(self.close)
        self.fade_out_anim.start()

    def mousePressEvent(self, event):
        self.close()
