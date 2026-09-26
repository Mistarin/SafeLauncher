"""
Icon helper module for SafeLauncher providing crisp Phosphor & FontAwesome vector icons
via QtAwesome with automatic fallback handling.
"""

from typing import Optional
from PyQt6.QtGui import QIcon, QPixmap, QGuiApplication, QPainter, QPainterPath, QPen, QColor
from PyQt6.QtCore import QSize, Qt

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


def icon_pixmap(icon: QIcon, size: int | QSize) -> QPixmap:
    """Render an icon pixmap at the correct physical size for the display.

    QIcon#setIconSize handles device-pixel-ratio scaling automatically. Direct
    QIcon.pixmap() calls used for QLabel icons do not always do so when given
    logical dimensions, which can turn icons into tiny blocks on scaled
    displays.
    """
    requested = QSize(size, size) if isinstance(size, int) else QSize(size)
    screen = QGuiApplication.primaryScreen()
    dpr = max(1.0, float(screen.devicePixelRatio())) if screen else 1.0
    # Ask QIcon for the logical size first. Qt may already return a DPR-aware
    # pixmap; only upscale when it returned a legacy DPR=1 pixmap. Requesting
    # physical dimensions unconditionally double-scales icons on Qt builds
    # that already apply the screen DPR internally.
    pixmap = icon.pixmap(requested)
    if dpr > 1.0 and pixmap.devicePixelRatio() <= 1.0:
        physical = QSize(round(requested.width() * dpr), round(requested.height() * dpr))
        pixmap = pixmap.scaled(physical.width(), physical.height())
        pixmap.setDevicePixelRatio(dpr)
    return pixmap


def get_icon_pixmap(name: str, size: int | QSize, color: str = "#ffffff") -> QPixmap:
    """Return a DPI-aware pixmap for an icon-font name."""
    return icon_pixmap(get_icon(name, color=color), size)


def get_app_icon_pixmap(key: str, size: int | QSize, color: Optional[str] = None) -> QPixmap:
    """Return a DPI-aware pixmap for a configured application icon."""
    return icon_pixmap(get_app_icon(key, color=color), size)


def draw_folder_pixmap(size: int = 15, color: str = "#FFFFFF") -> QPixmap:
    """Draw a compact folder icon without relying on a tiny font glyph."""
    screen = QGuiApplication.primaryScreen()
    dpr = max(1.0, float(screen.devicePixelRatio())) if screen else 1.0
    physical_size = round(size * dpr)
    pixmap = QPixmap(physical_size, physical_size)
    pixmap.fill(QColor(0, 0, 0, 0))
    pixmap.setDevicePixelRatio(dpr)

    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing, True)
    painter.scale(dpr, dpr)
    painter.setPen(QPen(
        QColor(color), 1.25, Qt.PenStyle.SolidLine,
        Qt.PenCapStyle.RoundCap, Qt.PenJoinStyle.RoundJoin,
    ))
    painter.setBrush(QColor(0, 0, 0, 0))
    path = QPainterPath()
    path.moveTo(2.0, 4.5)
    path.lineTo(6.0, 4.5)
    path.lineTo(7.5, 6.0)
    path.lineTo(13.0, 6.0)
    path.quadTo(13.5, 6.0, 13.5, 6.5)
    path.lineTo(13.5, 12.0)
    path.quadTo(13.5, 13.0, 12.5, 13.0)
    path.lineTo(2.5, 13.0)
    path.quadTo(1.5, 13.0, 1.5, 12.0)
    path.lineTo(1.5, 5.5)
    path.quadTo(1.5, 4.5, 2.0, 4.5)
    painter.drawPath(path)
    painter.end()
    return pixmap


# Pre-defined Icon Key Mappings for SafeLauncher matching Modern Dark SaaS palette
ICONS = {
    "library": ("ph.game-controller-bold", "#F4F4F5"),
    "sandbox": ("ph.folder-open-bold", "#A1A1AA"),
    "sync": ("ph.arrows-clockwise-bold", "#3B9FE8"),
    "launch": ("ph.play-fill", "#F4F4F5"),
    "add": ("ph.plus-bold", "#F4F4F5"),
    "edit": ("ph.pencil-bold", "#A1A1AA"),
    "remove": ("ph.trash-bold", "#F05D6C"),
    "export": ("ph.floppy-disk-bold", "#A1A1AA"),
    "import": ("ph.download-bold", "#A1A1AA"),
    "minimize": ("ph.minus-bold", "#71717A"),
    "maximize": ("ph.square-bold", "#71717A"),
    "restore": ("ph.copy-simple-bold", "#71717A"),
    "close": ("ph.x-bold", "#71717A"),
    "search": ("ph.magnifying-glass-bold", "#71717A"),
    "shield": ("ph.shield-check-bold", "#35C98A"),
    "globe": ("ph.globe-bold", "#3B9FE8"),
    "wine": ("ph.wine-bold", "#A1A1AA"),
    "terminal": ("ph.terminal-window-bold", "#A1A1AA"),
    "favorite": ("ph.heart-fill", "#FF453A"),
    "favorite_outline": ("ph.heart-bold", "#71717A"),
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
