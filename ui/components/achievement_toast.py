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

from PyQt6.QtCore import Qt, QTimer, QPropertyAnimation, QEasingCurve, QPoint, QEvent
from PyQt6.QtWidgets import (
    QWidget, QLabel, QHBoxLayout, QVBoxLayout, QFrame, QGraphicsOpacityEffect,
    QApplication, QSizePolicy
)
from PyQt6.QtGui import QPixmap, QIcon, QPainter, QColor, QFont

from core.logger import get_logger
from ui.icons import get_icon, get_icon_pixmap

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
        self._anchor_parent = None
        self._watched_screen = None
        self._screen_connections = []
        self._window_screen_signal = None
        self._detached = False

        self._build_ui()

    def _build_ui(self):
        root_layout = QVBoxLayout(self)
        root_layout.setContentsMargins(10, 10, 10, 10)

        # Card container
        self.card = QFrame(self)
        self.card.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Minimum)
        self.card.setStyleSheet("""
            QFrame {
                background-color: #121214;
                border: 1px solid #202024;
                border-left: 4px solid #35C98A;
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
            # Fallback styled badge with trophy vector icon
            fallback = QPixmap(48, 48)
            fallback.fill(Qt.GlobalColor.transparent)
            painter = QPainter(fallback)
            painter.setRenderHint(QPainter.RenderHint.Antialiasing)
            painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
            painter.setBrush(QColor("#202024"))
            painter.setPen(QColor("#35C98A"))
            painter.drawRoundedRect(1, 1, 46, 46, 8, 8)
            ico_pix = get_icon_pixmap("ph.trophy-fill", 24, color="#35C98A")
            painter.drawPixmap(12, 12, ico_pix)
            painter.end()
            self.icon_lbl.setPixmap(fallback)

        card_layout.addWidget(self.icon_lbl)

        # Text Details
        text_layout = QVBoxLayout()
        text_layout.setSpacing(2)

        header_lbl = QLabel("ACHIEVEMENT UNLOCKED")
        header_lbl.setStyleSheet("color: #35C98A; font-size: 10px; font-weight: bold; letter-spacing: 1px; background: transparent; border: none;")
        text_layout.addWidget(header_lbl)

        title_lbl = QLabel(self.display_name)
        title_lbl.setStyleSheet("color: #F4F4F5; font-size: 13px; font-weight: bold; background: transparent; border: none;")
        title_lbl.setWordWrap(True)
        title_lbl.setMinimumWidth(0)
        title_lbl.setMaximumWidth(300)
        title_lbl.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Minimum)
        title_lbl.setToolTip(self.display_name)
        text_layout.addWidget(title_lbl)
        self.title_label = title_lbl

        if self.description:
            desc_lbl = QLabel(self.description)
            desc_lbl.setStyleSheet("color: #A1A1AA; font-size: 11px; background: transparent; border: none;")
            desc_lbl.setWordWrap(True)
            desc_lbl.setMinimumWidth(0)
            desc_lbl.setMaximumWidth(300)
            desc_lbl.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Minimum)
            desc_lbl.setToolTip(self.description)
            text_layout.addWidget(desc_lbl)
            self.description_label = desc_lbl
        else:
            self.description_label = None

        card_layout.addLayout(text_layout)
        root_layout.addWidget(self.card)

        self.adjustSize()

    def show_animated(self, parent_widget: Optional[QWidget] = None):
        """Position toast in top-right or screen corner and show with fade-in."""
        self._anchor_parent = parent_widget or self.parentWidget()
        if self._anchor_parent is not None:
            self._anchor_parent.installEventFilter(self)
            toasts = [
                toast for toast in getattr(self._anchor_parent, "_safelauncher_achievement_toasts", [])
                if toast is not self and not toast._detached
            ]
            while len(toasts) >= 3:
                toasts.pop(0).close()
            toasts.append(self)
            self._anchor_parent._safelauncher_achievement_toasts = toasts

        # Fade in opacity animation
        self.opacity_effect = QGraphicsOpacityEffect(self)
        self.setGraphicsEffect(self.opacity_effect)

        self.fade_anim = QPropertyAnimation(self.opacity_effect, b"opacity")
        self.fade_anim.setDuration(300)
        self.fade_anim.setStartValue(0.0)
        self.fade_anim.setEndValue(1.0)
        self.fade_anim.setEasingCurve(QEasingCurve.Type.OutCubic)

        self.show()
        self._watch_anchor_screen()
        self._relayout_toasts()
        self.fade_anim.start()

        # Auto-dismiss timer
        QTimer.singleShot(self.duration_ms, self._fade_out)

    def _watch_anchor_screen(self):
        parent = self._anchor_parent
        window = parent.windowHandle() if parent is not None else None
        if window is not None:
            try:
                window.screenChanged.connect(self._on_parent_screen_changed)
                self._window_screen_signal = window.screenChanged
            except (TypeError, RuntimeError):
                pass
        self._on_parent_screen_changed(self._screen_for_anchor())

    def _screen_for_anchor(self):
        parent = self._anchor_parent
        if parent is not None:
            try:
                window = parent.windowHandle()
                if window is not None and window.screen() is not None:
                    return window.screen()
                if parent.screen() is not None:
                    return parent.screen()
            except RuntimeError:
                pass
        return QApplication.primaryScreen()

    def _on_parent_screen_changed(self, screen=None):
        for signal, slot in self._screen_connections:
            try:
                signal.disconnect(slot)
            except (TypeError, RuntimeError):
                pass
        self._screen_connections.clear()
        if self._window_screen_signal is not None:
            try:
                self._window_screen_signal.disconnect(self._on_parent_screen_changed)
            except (TypeError, RuntimeError):
                pass
            self._window_screen_signal = None
        self._watched_screen = screen or self._screen_for_anchor()
        if self._watched_screen is not None:
            for signal in (
                self._watched_screen.geometryChanged,
                self._watched_screen.availableGeometryChanged,
                self._watched_screen.logicalDotsPerInchChanged,
            ):
                try:
                    signal.connect(self._on_screen_geometry_changed)
                    self._screen_connections.append((signal, self._on_screen_geometry_changed))
                except (TypeError, RuntimeError):
                    pass
        self._relayout_toasts()

    def _on_screen_geometry_changed(self, *_args):
        self._relayout_toasts()

    def _relayout_toasts(self):
        if self._detached:
            return
        parent = self._anchor_parent
        screen = self._watched_screen or self._screen_for_anchor()
        if screen is None:
            return
        available = screen.availableGeometry()
        if parent is not None:
            try:
                anchor = parent.frameGeometry().intersected(available)
                if anchor.isEmpty():
                    anchor = available
            except RuntimeError:
                anchor = available
        else:
            anchor = available
        margin, gap = 18, 10
        max_width = max(180, min(440, anchor.width() - 2 * margin))
        toasts = [
            toast for toast in getattr(parent, "_safelauncher_achievement_toasts", [self])
            if not toast._detached
        ] if parent is not None else [self]
        bottom = anchor.y() + 48
        for toast in toasts:
            toast._set_layout_width(max_width)
            x = anchor.x() + anchor.width() - toast.width() - margin
            y = bottom
            toast.move(
                max(available.left() + margin, min(x, available.right() - toast.width() - margin + 1)),
                max(available.top() + margin, min(y, available.bottom() - toast.height() - margin + 1)),
            )
            bottom = toast.y() + toast.height() + gap

    def _set_layout_width(self, max_width: int):
        self.setMaximumWidth(max_width)
        text_width = max(80, max_width - 10 - 10 - 28 - 48 - 12 - 12)
        self.title_label.setMaximumWidth(text_width)
        if self.description_label is not None:
            self.description_label.setMaximumWidth(text_width)
        self.adjustSize()

    def eventFilter(self, watched, event):
        if watched is self._anchor_parent and event.type() in {
            QEvent.Type.Resize,
            QEvent.Type.Move,
        }:
            self._relayout_toasts()
        return super().eventFilter(watched, event)

    def _detach(self):
        if self._detached:
            return
        self._detached = True
        parent = self._anchor_parent
        if parent is not None:
            try:
                parent.removeEventFilter(self)
                parent._safelauncher_achievement_toasts = [
                    toast for toast in getattr(parent, "_safelauncher_achievement_toasts", [])
                    if toast is not self and not toast._detached
                ]
            except RuntimeError:
                pass
        for signal, slot in self._screen_connections:
            try:
                signal.disconnect(slot)
            except (TypeError, RuntimeError):
                pass
        self._screen_connections.clear()
        if parent is not None:
            QTimer.singleShot(0, lambda parent=parent: self._relayout_parent(parent))

    @staticmethod
    def _relayout_parent(parent):
        try:
            toasts = getattr(parent, "_safelauncher_achievement_toasts", [])
            if toasts:
                toasts[0]._relayout_toasts()
        except (RuntimeError, AttributeError):
            pass

    def closeEvent(self, event):
        self._detach()
        super().closeEvent(event)

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
