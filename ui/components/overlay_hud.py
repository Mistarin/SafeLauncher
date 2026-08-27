"""Ultra lightweight, non-intrusive in-game notification HUD overlay for SafeLauncher."""

import os
import shutil
import subprocess
from typing import Optional
from PyQt6.QtWidgets import QWidget, QHBoxLayout, QVBoxLayout, QLabel, QApplication, QGraphicsOpacityEffect
from PyQt6.QtCore import Qt, QTimer, QPropertyAnimation, QEasingCurve, QRectF, QPoint
from PyQt6.QtGui import QFont, QColor, QPainter, QPen, QPainterPath, QCursor

from core.logger import get_logger
from core.host_process import host_process_env

logger = get_logger("OverlayHUD")

_ACTIVE_OVERLAY = None

# Optional path to notification sound file
_BUNDLED_SOUND = os.path.join(os.path.dirname(os.path.dirname(os.path.dirname(__file__))), "assets", "notification.mp3")
_SOUND_FILE = _BUNDLED_SOUND if os.path.exists(_BUNDLED_SOUND) else ""


def play_notification_sound():
    """Play the notification sound if available (non-blocking, silent)."""
    if not _SOUND_FILE or not os.path.exists(_SOUND_FILE):
        return
    try:
        mpv = shutil.which("mpv")
        if mpv:
            subprocess.Popen(
                [mpv, "--no-video", "--no-terminal", "--really-quiet", _SOUND_FILE],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
    except Exception:
        pass


def _resolve_target_screen(target_screen_name: str = "current"):
    """Resolve screen object based on configuration: monitor name, 'current', or 'primary'."""
    app = QApplication.instance()
    if not app:
        return None

    screens = app.screens()
    if not screens:
        return None

    target_clean = (target_screen_name or "current").strip()

    # 1. Match specific named monitor (e.g. 'HDMI-A-1', 'DP-1')
    for s in screens:
        if s.name().lower() == target_clean.lower():
            return s

    # 2. 'current' / 'active' / 'focused': screen where cursor currently resides
    if target_clean in ("current", "active", "focused", "screen"):
        cursor_pos = QCursor.pos()
        screen = app.screenAt(cursor_pos)
        if screen:
            return screen

    # 3. Default to primary screen
    return app.primaryScreen() or screens[0]


def _get_overlay_position(widget_w: int, widget_h: int, target_screen_name: str = "current", margin: int = 14):
    """Calculate absolute top-right position on the designated monitor."""
    screen = _resolve_target_screen(target_screen_name)
    if screen:
        g = screen.geometry()
        x = g.x() + g.width() - widget_w - margin
        y = g.y() + margin
        return x, y
    return 100, margin


_ANCHOR_WINDOW = None

def _get_transient_parent():
    """Find or create a valid top-level window to act as transient parent so Wayland/X11 popup doesn't center or lose parent."""
    global _ANCHOR_WINDOW
    app = QApplication.instance()
    if not app:
        return None
    for widget in app.topLevelWidgets():
        if widget.isWindow() and widget.isVisible() and widget != _ANCHOR_WINDOW:
            return widget

    if _ANCHOR_WINDOW is None:
        _ANCHOR_WINDOW = QWidget()
        _ANCHOR_WINDOW.setWindowFlags(Qt.WindowType.FramelessWindowHint | Qt.WindowType.WindowDoesNotAcceptFocus)
        _ANCHOR_WINDOW.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        _ANCHOR_WINDOW.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating, True)
        _ANCHOR_WINDOW.setGeometry(0, 0, 1, 1)
        _ANCHOR_WINDOW.show()
    return _ANCHOR_WINDOW


class OverlayIconWidget(QWidget):
    """Custom vector painted icon for camera, recording, and replay indicators."""

    def __init__(self, icon_type: str = "screenshot", parent=None):
        super().__init__(parent)
        self.icon_type = icon_type
        self.setFixedSize(28, 28)

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)

        if self.icon_type in ("screenshot", "camera"):
            pen = QPen(QColor(228, 228, 231), 1.8)
            pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
            pen.setCapStyle(Qt.PenCapStyle.RoundCap)
            painter.setPen(pen)
            painter.setBrush(Qt.BrushStyle.NoBrush)
            path = QPainterPath()
            path.addRoundedRect(QRectF(3.0, 7.5, 22.0, 15.5), 3.0, 3.0)
            painter.drawPath(path)
            painter.drawRoundedRect(QRectF(7.5, 4.5, 6.0, 3.5), 1.5, 1.5)
            painter.setBrush(QColor(228, 228, 231, 60))
            painter.drawEllipse(QRectF(10.0, 11.5, 8.0, 8.0))
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QColor(56, 189, 248))
            painter.drawEllipse(QRectF(20.0, 9.5, 2.5, 2.5))

        elif self.icon_type == "recording":
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QColor(239, 68, 68, 55))
            painter.drawEllipse(QRectF(3.0, 3.0, 22.0, 22.0))
            painter.setBrush(QColor(239, 68, 68))
            painter.drawEllipse(QRectF(7.0, 7.0, 14.0, 14.0))

        elif self.icon_type == "replay":
            pen = QPen(QColor(56, 189, 248), 1.8)
            pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
            painter.setPen(pen)
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawRoundedRect(QRectF(4.0, 5.0, 20.0, 18.0), 3.0, 3.0)
            play = QPainterPath()
            play.moveTo(11.0, 10.0)
            play.lineTo(18.0, 14.0)
            play.lineTo(11.0, 18.0)
            play.closeSubpath()
            painter.setBrush(QColor(56, 189, 248))
            painter.drawPath(play)

        elif self.icon_type == "warning":
            pen = QPen(QColor(245, 158, 11), 1.8)
            painter.setPen(pen)
            painter.setBrush(Qt.BrushStyle.NoBrush)
            t = QPainterPath()
            t.moveTo(14.0, 4.0)
            t.lineTo(25.0, 23.0)
            t.lineTo(3.0, 23.0)
            t.closeSubpath()
            painter.drawPath(t)
            painter.setPen(QPen(QColor(245, 158, 11), 2.0))
            painter.drawLine(14, 11, 14, 16)
            painter.drawPoint(14, 19)

        else:  # info / saved
            painter.setPen(QPen(QColor(52, 211, 153), 2.0))
            painter.setBrush(Qt.BrushStyle.NoBrush)
            painter.drawRoundedRect(QRectF(3.0, 3.0, 22.0, 22.0), 4.0, 4.0)
            check = QPainterPath()
            check.moveTo(8.0, 14.0)
            check.lineTo(12.0, 18.0)
            check.lineTo(20.0, 10.0)
            painter.drawPath(check)

        painter.end()


class GameOverlayNotificationWidget(QWidget):
    """Clean dark rectangle pinned to the top-right of the chosen monitor. Never steals focus."""

    W = 320
    H = 56
    MARGIN = 14

    def __init__(self, title: str, subtitle: str = "", icon_type: str = "screenshot",
                 duration_ms: int = 2800, target_screen: str = "current", parent=None):
        parent_widget = parent or _get_transient_parent()
        super().__init__(parent_widget)
        self.target_screen_name = target_screen

        # ToolTip flags with transient parent: no focus stealing, no WM centering, always on top
        self.setWindowFlags(
            Qt.WindowType.ToolTip |
            Qt.WindowType.FramelessWindowHint |
            Qt.WindowType.WindowStaysOnTopHint |
            Qt.WindowType.WindowDoesNotAcceptFocus |
            Qt.WindowType.WindowTransparentForInput
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating, True)
        self.setAttribute(Qt.WidgetAttribute.WA_X11DoNotAcceptFocus, True)
        self.setFixedSize(self.W, self.H)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(12, 8, 14, 8)
        layout.setSpacing(10)

        self.icon_widget = OverlayIconWidget(icon_type, self)
        layout.addWidget(self.icon_widget, 0, Qt.AlignmentFlag.AlignVCenter)

        text_layout = QVBoxLayout()
        text_layout.setContentsMargins(0, 0, 0, 0)
        text_layout.setSpacing(1)

        lbl_title = QLabel(title)
        lbl_title.setFont(QFont("Inter, Segoe UI, Roboto, sans-serif", 10, QFont.Weight.DemiBold))
        lbl_title.setStyleSheet("color: #ffffff; background: transparent; letter-spacing: 0.2px;")
        text_layout.addWidget(lbl_title)

        if subtitle:
            lbl_sub = QLabel(subtitle)
            lbl_sub.setFont(QFont("Inter, Segoe UI, Roboto, sans-serif", 9))
            lbl_sub.setStyleSheet("color: #a1a1aa; background: transparent;")
            text_layout.addWidget(lbl_sub)

        layout.addLayout(text_layout)
        layout.addStretch()

        # Smooth Opacity Fade-in
        self.opacity_effect = QGraphicsOpacityEffect(self)
        self.setGraphicsEffect(self.opacity_effect)
        self.opacity_effect.setOpacity(0.0)

        self.anim_in = QPropertyAnimation(self.opacity_effect, b"opacity")
        self.anim_in.setDuration(180)
        self.anim_in.setStartValue(0.0)
        self.anim_in.setEndValue(1.0)
        self.anim_in.setEasingCurve(QEasingCurve.Type.OutCubic)

        self.dismiss_timer = QTimer(self)
        self.dismiss_timer.setSingleShot(True)
        self.dismiss_timer.timeout.connect(self._fade_out)
        self.duration_ms = duration_ms

    def show_animated(self):
        x, y = _get_overlay_position(self.W, self.H, self.target_screen_name, self.MARGIN)
        self.move(x, y)
        self.show()

        # Ensure correct placement across multi-screen setups
        QTimer.singleShot(0, lambda: self.move(x, y))
        QTimer.singleShot(25, lambda: self.move(x, y))

        self.anim_in.start()
        self.dismiss_timer.start(self.duration_ms)

    def _fade_out(self):
        self.anim_out = QPropertyAnimation(self.opacity_effect, b"opacity")
        self.anim_out.setDuration(220)
        self.anim_out.setStartValue(1.0)
        self.anim_out.setEndValue(0.0)
        self.anim_out.setEasingCurve(QEasingCurve.Type.InCubic)
        self.anim_out.finished.connect(self.close)
        self.anim_out.start()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        card = QRectF(0.5, 0.5, self.width() - 1.0, self.height() - 1.0)
        painter.setBrush(QColor(18, 18, 22, 245))
        painter.setPen(QColor(50, 50, 58, 220))
        painter.drawRoundedRect(card, 6.0, 6.0)
        painter.end()


def show_ingame_notification(title: str, subtitle: str = "", icon_type: str = "screenshot",
                             enabled: bool = True, play_sound: bool = False, target_screen: str = "current"):
    """Display the in-game floating HUD overlay on top-right of designated screen."""
    global _ACTIVE_OVERLAY

    if play_sound:
        play_notification_sound()

    if not enabled:
        return

    try:
        app = QApplication.instance()
        if not app:
            return

        # Dismiss existing overlay
        if _ACTIVE_OVERLAY is not None:
            try:
                _ACTIVE_OVERLAY.dismiss_timer.stop()
                _ACTIVE_OVERLAY.close()
            except Exception:
                pass
            _ACTIVE_OVERLAY = None

        overlay = GameOverlayNotificationWidget(title, subtitle, icon_type, target_screen=target_screen)
        _ACTIVE_OVERLAY = overlay
        overlay.show_animated()
        logger.debug(f"HUD overlay: '{title}' / '{subtitle}' on screen '{target_screen}'")

    except Exception as e:
        logger.debug(f"Failed to display overlay HUD: {e}")
