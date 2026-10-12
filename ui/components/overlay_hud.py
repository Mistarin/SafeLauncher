"""Ultra lightweight, non-intrusive in-game notification HUD overlay for SafeLauncher."""

import os
import shutil
import subprocess
from typing import Optional
from PyQt6.QtWidgets import QWidget, QHBoxLayout, QVBoxLayout, QLabel, QApplication
from PyQt6.QtCore import Qt, QTimer, QRectF, QEvent
from PyQt6.QtGui import QFont, QColor, QPainter, QPen, QPainterPath, QCursor

from core.logger import get_logger
from core.host_process import host_process_env

logger = get_logger("OverlayHUD")

_ACTIVE_OVERLAYS = []
_SCREEN_CHANGE_EVENT = getattr(QEvent.Type, "ScreenChangeInternal", None)

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
                [mpv, "--no-config", "--no-video", "--no-terminal", "--really-quiet", _SOUND_FILE],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                close_fds=True,
                start_new_session=True,
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

    if target_clean.startswith("screen:"):
        try:
            index = int(target_clean.split(":", 1)[1])
            if 0 <= index < len(screens):
                return screens[index]
        except ValueError:
            pass

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


def _screen_target_value(screen) -> str:
    """Return a target key that stays unambiguous for duplicate monitor names."""
    app = QApplication.instance()
    if app is None:
        return screen.name() or "current"
    screens = app.screens()
    matches = [item for item in screens if item.name() == screen.name()]
    if not screen.name() or len(matches) > 1:
        try:
            return f"screen:{screens.index(screen)}"
        except ValueError:
            return "primary"
    return screen.name()


def _get_overlay_position(widget_w: int, widget_h: int, target_screen_name: str = "current", margin: int = 14,
                          screen=None):
    """Return a clamped top-right position in Qt's logical screen coordinates."""
    screen = screen or _resolve_target_screen(target_screen_name)
    if screen:
        g = screen.availableGeometry()
        widget_w = min(max(1, widget_w), max(1, g.width() - 2 * margin))
        widget_h = min(max(1, widget_h), max(1, g.height() - 2 * margin))
        x = max(g.left(), min(g.right() - widget_w + 1, g.right() - widget_w - margin + 1))
        y = max(g.top(), min(g.bottom() - widget_h + 1, g.top() + margin))
        return x, y
    return 100, margin


def _relayout_active_overlays():
    """Stack visible HUD cards by monitor and keep them inside usable bounds."""
    global _ACTIVE_OVERLAYS
    _ACTIVE_OVERLAYS = [overlay for overlay in _ACTIVE_OVERLAYS if overlay.isVisible()]
    by_screen = {}
    for overlay in _ACTIVE_OVERLAYS:
        screen = overlay._target_screen or _resolve_target_screen(overlay.target_screen_name)
        if screen is None:
            continue
        overlay._fit_to_screen(screen)
        by_screen.setdefault(id(screen), (screen, []))[1].append(overlay)
    for screen, overlays in by_screen.values():
        area = screen.availableGeometry()
        y = area.top() + GameOverlayNotificationWidget.MARGIN
        for overlay in overlays:
            x, _ = _get_overlay_position(overlay.width(), overlay.height(), overlay.target_screen_name,
                                         GameOverlayNotificationWidget.MARGIN, screen=screen)
            y = min(y, max(area.top(), area.bottom() - overlay.height() + 1))
            overlay.move(x, y)
            y += overlay.height() + 10


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
        # Keep this as an independent, non-activating top-level surface. A
        # transient relationship to the launcher can make some window
        # managers activate the launcher when a fullscreen game is focused.
        super().__init__(None)
        self.target_screen_name = str(target_screen or "current")
        self.duration_ms = max(250, int(duration_ms))
        self._target_screen = None

        # A regular tool window avoids the platform's cursor-anchored tooltip
        # placement, which otherwise overrides our screen-corner geometry.
        self.setWindowFlags(
            Qt.WindowType.Tool |
            Qt.WindowType.FramelessWindowHint |
            Qt.WindowType.WindowStaysOnTopHint |
            Qt.WindowType.WindowDoesNotAcceptFocus |
            Qt.WindowType.WindowTransparentForInput
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)
        self.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating, True)
        self.setAttribute(Qt.WidgetAttribute.WA_X11DoNotAcceptFocus, True)
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose, True)
        self.setMaximumWidth(self.W)
        self._screen_connections = []
        self._app_screen_connections = []

        layout = QHBoxLayout(self)
        self._layout = layout
        layout.setContentsMargins(12, 8, 14, 8)
        layout.setSpacing(10)

        self.icon_widget = OverlayIconWidget(icon_type, self)
        layout.addWidget(self.icon_widget, 0, Qt.AlignmentFlag.AlignVCenter)

        text_layout = QVBoxLayout()
        text_layout.setContentsMargins(0, 0, 0, 0)
        text_layout.setSpacing(1)

        lbl_title = QLabel(title)
        lbl_title.setWordWrap(True)
        lbl_title.setMinimumWidth(0)
        lbl_title.setToolTip(title)
        lbl_title.setFont(QFont("Inter, Segoe UI, Roboto, sans-serif", 10, QFont.Weight.DemiBold))
        lbl_title.setStyleSheet("color: #ffffff; background: transparent; letter-spacing: 0.2px;")
        text_layout.addWidget(lbl_title)

        if subtitle:
            lbl_sub = QLabel(subtitle)
            lbl_sub.setWordWrap(True)
            lbl_sub.setMinimumWidth(0)
            lbl_sub.setToolTip(subtitle)
            lbl_sub.setFont(QFont("Inter, Segoe UI, Roboto, sans-serif", 9))
            lbl_sub.setStyleSheet("color: #a1a1aa; background: transparent;")
            text_layout.addWidget(lbl_sub)

        layout.addLayout(text_layout)
        layout.addStretch()
        self.title_label = lbl_title
        self.subtitle_label = lbl_sub if subtitle else None

        self.dismiss_timer = QTimer(self)
        self.dismiss_timer.setSingleShot(True)
        self.dismiss_timer.timeout.connect(self.close)

    def show_animated(self):
        screen = _resolve_target_screen(self.target_screen_name)
        if screen is not None:
            # Resolve aliases such as "current" once, so cursor movement does
            # not make a notification jump between monitors while it is shown.
            self.target_screen_name = _screen_target_value(screen)
            self._target_screen = screen
            self._watch_screen(screen)
        app = QApplication.instance()
        if app is not None:
            for signal in (app.screenAdded, app.screenRemoved):
                try:
                    signal.connect(self._on_screen_topology_changed)
                    self._app_screen_connections.append((signal, self._on_screen_topology_changed))
                except (TypeError, RuntimeError):
                    pass
        self._fit_to_screen(screen)
        x, y = _get_overlay_position(self.width(), self.height(), self.target_screen_name, self.MARGIN,
                                     screen=screen)
        self.move(x, y)
        self.show()
        # One post-map correction handles window managers that adjust tool geometry.
        QTimer.singleShot(0, _relayout_active_overlays)
        self.dismiss_timer.start(self.duration_ms)

    def _watch_screen(self, screen):
        for signal, slot in self._screen_connections:
            try:
                signal.disconnect(slot)
            except (TypeError, RuntimeError):
                pass
        self._screen_connections.clear()
        if screen is None:
            return
        for signal in (
            screen.geometryChanged,
            screen.availableGeometryChanged,
            screen.logicalDotsPerInchChanged,
        ):
            try:
                signal.connect(self._on_screen_geometry_changed)
                self._screen_connections.append((signal, self._on_screen_geometry_changed))
            except (TypeError, RuntimeError):
                pass

    def _fit_to_screen(self, screen):
        if screen is None:
            self.adjustSize()
            return
        area = screen.availableGeometry()
        max_width = max(1, min(self.W, area.width() - 2 * self.MARGIN))
        self.setMaximumWidth(max_width)
        compact = max_width < 116 or area.height() < 64
        self.icon_widget.setVisible(not compact)
        if self.subtitle_label is not None:
            self.subtitle_label.setVisible(not compact)
        self._layout.setContentsMargins(4 if compact else 12, 2 if compact else 8,
                                       4 if compact else 14, 2 if compact else 8)
        self._layout.setSpacing(4 if compact else 10)
        text_width = max(1, max_width - 12 - 14 - 28 - 10)
        self.title_label.setMaximumWidth(text_width)
        if self.subtitle_label is not None:
            self.subtitle_label.setMaximumWidth(text_width)
        self.adjustSize()
        self.setMaximumHeight(max(1, area.height() - 2 * self.MARGIN))
        self.adjustSize()

    def _on_screen_geometry_changed(self, *_args):
        screen = _resolve_target_screen(self.target_screen_name)
        self._fit_to_screen(screen)
        _relayout_active_overlays()

    def _on_screen_topology_changed(self, *_args):
        screen = _resolve_target_screen(self.target_screen_name)
        if screen is not None:
            self.target_screen_name = _screen_target_value(screen)
            self._target_screen = screen
            self._watch_screen(screen)
        self._fit_to_screen(screen)
        _relayout_active_overlays()

    def event(self, event):
        result = super().event(event)
        if _SCREEN_CHANGE_EVENT is not None and event.type() == _SCREEN_CHANGE_EVENT:
            QTimer.singleShot(0, self._on_screen_geometry_changed)
        return result

    def closeEvent(self, event):
        self.dismiss_timer.stop()
        for signal, slot in self._screen_connections + self._app_screen_connections:
            try:
                signal.disconnect(slot)
            except (TypeError, RuntimeError):
                pass
        self._screen_connections.clear()
        self._app_screen_connections.clear()
        try:
            _ACTIVE_OVERLAYS.remove(self)
        except ValueError:
            pass
        super().closeEvent(event)
        QTimer.singleShot(0, _relayout_active_overlays)

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
    global _ACTIVE_OVERLAYS

    if not enabled:
        return

    try:
        app = QApplication.instance()
        if not app:
            return
        if play_sound:
            play_notification_sound()

        overlay = GameOverlayNotificationWidget(title, subtitle, icon_type, target_screen=target_screen)
        _ACTIVE_OVERLAYS = [item for item in _ACTIVE_OVERLAYS if item.isVisible()]
        screen = _resolve_target_screen(target_screen)
        same_screen = [
            item for item in _ACTIVE_OVERLAYS
            if screen is not None and item._target_screen is screen
        ]
        while len(same_screen) >= 3:
            same_screen.pop(0).close()
        _ACTIVE_OVERLAYS.append(overlay)
        overlay.show_animated()
        logger.debug(f"HUD overlay: '{title}' / '{subtitle}' on screen '{target_screen}'")

    except Exception as e:
        logger.debug(f"Failed to display overlay HUD: {e}")
