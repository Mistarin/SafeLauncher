"""
Icon helper module for SafeLauncher providing crisp Phosphor & FontAwesome vector icons
via QtAwesome with automatic fallback handling.
"""

from typing import Optional
from PyQt6.QtGui import QIcon

try:
    import qtawesome as qta
    _QTA_AVAILABLE = True
except ImportError:
    _QTA_AVAILABLE = False


def get_icon(name: str, color: str = "#ffffff", active_color: Optional[str] = None) -> QIcon:
    """Get high-resolution vector QIcon.
    
    Examples:
        get_icon('ph.game-controller-bold', color='#ffffff')
        get_icon('ph.arrows-clockwise-bold', color='#64b5f6')
    """
    if not _QTA_AVAILABLE:
        return QIcon()
    
    try:
        kwargs = {"color": color}
        if active_color:
            kwargs["color_active"] = active_color
            kwargs["color_selected"] = active_color
        return qta.icon(name, **kwargs)
    except Exception as e:
        print(f"Icon load warning for '{name}': {e}")
        return QIcon()


# Pre-defined Icon Key Mappings for SafeLauncher
ICONS = {
    "library": ("ph.game-controller-bold", "#ffffff"),
    "sandbox": ("ph.folder-open-bold", "#ffffff"),
    "sync": ("ph.arrows-clockwise-bold", "#64b5f6"),
    "launch": ("fa5s.play", "#ffffff"),
    "add": ("ph.plus-bold", "#ffffff"),
    "edit": ("ph.pencil-bold", "#ffffff"),
    "remove": ("ph.trash-bold", "#ffffff"),
    "export": ("ph.floppy-disk-bold", "#ffffff"),
    "import": ("ph.download-bold", "#ffffff"),
    "minimize": ("fa5s.minus", "#aaaaaa"),
    "maximize": ("fa5s.square", "#aaaaaa"),
    "restore": ("fa5s.clone", "#aaaaaa"),
    "close": ("fa5s.times", "#aaaaaa"),
    "search": ("ph.magnifying-glass-bold", "#ffffff"),
    "shield": ("fa5s.shield-alt", "#ffffff"),
    "globe": ("fa5s.globe", "#ffffff"),
    "wine": ("fa5s.wine-glass", "#ffffff"),
    "terminal": ("fa5s.terminal", "#ffffff"),
}


def get_app_icon(key: str, color: Optional[str] = None) -> QIcon:
    """Retrieve pre-configured icon by key name."""
    if key in ICONS:
        name, default_color = ICONS[key]
        return get_icon(name, color=color or default_color)
    return QIcon()


import os
from PyQt6.QtCore import Qt
from PyQt6.QtGui import QPixmap, QPainter, QRadialGradient, QLinearGradient, QColor, QPen

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LOGO_PATH = os.path.join(BASE_DIR, "assets", "logo.png")


def asset_path(filename: str) -> str:
    """Return an asset path that works from source and PyInstaller builds."""
    candidates = [
        os.path.join(BASE_DIR, "assets", filename),
        os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "assets", filename),
    ]
    for candidate in candidates:
        if os.path.exists(candidate):
            return os.path.abspath(candidate)
    return os.path.abspath(candidates[0])


GIF_PATH = asset_path("penguin-pudgy.gif")
CONFIRM_GIF_PATH = asset_path("smict.gif")


def draw_custom_lock_pixmap(size: int = 80, is_ready: bool = False) -> QPixmap:
    """Draw a high-resolution, multi-layered vector lock or checkmark badge icon with radial glow effects."""
    pix = QPixmap(size, size)
    pix.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pix)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)

    cx, cy = size // 2, size // 2

    if not is_ready:
        # Radial Glow Ring
        glow_grad = QRadialGradient(cx, cy, size // 2)
        glow_grad.setColorAt(0.0, QColor(34, 197, 94, 60))
        glow_grad.setColorAt(0.7, QColor(34, 197, 94, 15))
        glow_grad.setColorAt(1.0, QColor(0, 0, 0, 0))
        painter.setBrush(glow_grad)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.drawEllipse(0, 0, size, size)

        # Glass Badge Base
        circle_grad = QLinearGradient(0, 0, size, size)
        circle_grad.setColorAt(0.0, QColor(6, 78, 59))
        circle_grad.setColorAt(1.0, QColor(2, 44, 34))
        painter.setBrush(circle_grad)
        painter.setPen(QPen(QColor(34, 197, 94), 2))
        painter.drawEllipse(8, 8, size - 16, size - 16)

        # White Lock Shackle
        painter.setPen(QPen(QColor(255, 255, 255), 4, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawArc(cx - 12, cy - 18, 24, 24, 0, 180 * 16)

        # White Lock Body
        painter.setBrush(QColor(255, 255, 255))
        painter.setPen(Qt.PenStyle.NoPen)
        painter.drawRoundedRect(cx - 15, cy - 3, 30, 22, 5, 5)

        # Dark Keyhole
        painter.setBrush(QColor(2, 44, 34))
        painter.drawEllipse(cx - 4, cy + 3, 8, 8)
        painter.drawRect(cx - 2, cy + 7, 4, 6)
    else:
        # Radial Glow Ring for Ready
        glow_grad = QRadialGradient(cx, cy, size // 2)
        glow_grad.setColorAt(0.0, QColor(34, 197, 94, 90))
        glow_grad.setColorAt(0.7, QColor(34, 197, 94, 25))
        glow_grad.setColorAt(1.0, QColor(0, 0, 0, 0))
        painter.setBrush(glow_grad)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.drawEllipse(0, 0, size, size)

        # Emerald Badge Base
        circle_grad = QLinearGradient(0, 0, size, size)
        circle_grad.setColorAt(0.0, QColor(22, 163, 74))
        circle_grad.setColorAt(1.0, QColor(21, 128, 61))
        painter.setBrush(circle_grad)
        painter.setPen(QPen(QColor(74, 222, 128), 2))
        painter.drawEllipse(8, 8, size - 16, size - 16)

        # Pure White Bold Checkmark
        painter.setPen(QPen(QColor(255, 255, 255), 5, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin))
        painter.drawLine(cx - 12, cy, cx - 4, cy + 8)
        painter.drawLine(cx - 4, cy + 8, cx + 13, cy - 9)

    painter.end()
    return pix
