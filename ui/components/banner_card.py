import os
from PyQt6.QtWidgets import QFrame, QVBoxLayout, QLabel, QPushButton, QWidget
from PyQt6.QtCore import Qt, QSize, QPoint, QPointF, pyqtSignal, QVariantAnimation, QEasingCurve
from PyQt6.QtGui import QFont, QPixmap, QColor, QPainter

from ui.icons import get_app_icon, get_icon


class UpdatePulsingDotWidget(QWidget):
    """Pulsating emerald-green indicator circle for available updates."""
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedSize(28, 28)
        self.setToolTip("Steam update available")
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        self._pulse_phase = 0.0

        self._anim = QVariantAnimation(self)
        self._anim.setStartValue(0.0)
        self._anim.setEndValue(1.0)
        self._anim.setDuration(1600)
        self._anim.setLoopCount(-1)
        self._anim.valueChanged.connect(self._on_pulse)

    def _on_pulse(self, val: float):
        self._pulse_phase = val
        self.update()

    def showEvent(self, event):
        super().showEvent(event)
        self._anim.start()

    def hideEvent(self, event):
        super().hideEvent(event)
        self._anim.stop()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)

        center = QPointF(self.width() / 2.0, self.height() / 2.0)

        # Outer pulsating halo (grows and fades out symmetrically around center)
        halo_radius = 4.5 + (7.0 * self._pulse_phase)
        halo_alpha = int(180 * (1.0 - self._pulse_phase))
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor(16, 185, 129, halo_alpha))
        painter.drawEllipse(center, halo_radius, halo_radius)

        # Inner solid emerald green core exactly centered
        core_radius = 4.0
        painter.setBrush(QColor(52, 211, 153))
        painter.setPen(QColor(16, 185, 129))
        painter.drawEllipse(center, core_radius, core_radius)
        painter.end()


class GameBannerWidget(QFrame):
    """Individual borderless game banner card with pixel font title and LERP zoom"""
    clicked = pyqtSignal(int)
    doubleClicked = pyqtSignal(int)
    rightClicked = pyqtSignal(int, QPoint)
    favoriteClicked = pyqtSignal(int)

    def __init__(self, game_id: int, name: str, banner_path: str = None, playtime_seconds: int = 0, version: str = "", parent=None):
        super().__init__(parent)
        self.game_id = game_id
        self.name = name
        self.banner_path = banner_path
        self.playtime_seconds = playtime_seconds
        self.version = str(version).strip() if version else ""
        self.selected = False
        self.is_missing = False
        self.is_favorite = False
        self.is_update_available = False
        self._hover_progress = 0.0  # LERP progress: 0.0 (normal) -> 1.0 (hovered)
        
        # Smooth 180ms LERP animation setup with OutCubic easing curve
        self.anim = QVariantAnimation(self)
        self.anim.setDuration(180)
        self.anim.setEasingCurve(QEasingCurve.Type.OutCubic)
        self.anim.valueChanged.connect(self._on_anim_frame)

        self.card_width = 200
        self.card_height = 300

        self.setFrameStyle(QFrame.Shape.NoFrame)
        self.setLineWidth(0)
        self.setFixedSize(QSize(self.card_width, self.card_height + 55))
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(3)
        
        # Banner image (2:3 portrait aspect ratio matching Steam 600x900 library covers)
        self.image_label = QLabel()
        self.image_label.setFixedSize(QSize(self.card_width, self.card_height))
        self.image_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(self.image_label)

        # Green animated pulsing circle indicator for updates
        self.update_indicator = UpdatePulsingDotWidget(self)
        self.update_indicator.move(8, 8)
        self.update_indicator.hide()

        # Favorite belongs to the library card itself. Keep it as a real
        # button instead of painting a non-interactive star into the artwork.
        self.favorite_button = QPushButton(self)
        self.favorite_button.setFixedSize(30, 30)
        self.favorite_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self.favorite_button.setCheckable(True)
        self.favorite_button.setIconSize(QSize(17, 17))
        self.favorite_button.setStyleSheet("""
            QPushButton {
                background: transparent;
                color: #d4d4d8;
                border: none;
                border-radius: 8px;
                padding: 0;
            }
            QPushButton:hover { background: transparent; color: #ffffff; }
            QPushButton:checked {
                background: transparent;
                color: #facc15;
                border: none;
            }
        """)
        self.favorite_button.clicked.connect(lambda: self.favoriteClicked.emit(self.game_id))
        self.favorite_button.hide()
        self._position_favorite_button()

        # Pure version badge positioned at bottom-left of the banner cover
        self.version_badge = QLabel(self)
        self.version_badge.setFont(QFont("Arial", 9, QFont.Weight.Bold))
        self.version_badge.setStyleSheet("""
            QLabel {
                background: rgba(15, 15, 18, 0.82);
                color: #ffffff;
                font-weight: bold;
                font-size: 10px;
                border-radius: 4px;
                padding: 2px 6px;
                border: 1px solid rgba(255, 255, 255, 0.12);
            }
        """)
        self.version_badge.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        self.set_version(self.version)
        
        # Game name label using clean sans-serif typography
        self.name_label = QLabel(name)
        self.name_label.setWordWrap(True)
        self.name_label.setFont(QFont("Arial", 11, QFont.Weight.Bold))
        self.name_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(self.name_label)

        # Playtime label — small, dimmed, below the title
        self.playtime_label = QLabel(self._format_playtime(playtime_seconds))
        self.playtime_label.setFont(QFont("Arial", 9))
        self.playtime_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.playtime_label.setStyleSheet("color: #a1a1aa; background: transparent;")
        layout.addWidget(self.playtime_label)
        
        self.update_appearance()

    @staticmethod
    def _format_playtime(seconds: int) -> str:
        """Convert raw seconds to a human-readable playtime string."""
        if not seconds or seconds < 60:
            return "Never played" if not seconds else f"{seconds}s"
        minutes = seconds // 60
        hours = minutes // 60
        remaining_mins = minutes % 60
        if hours > 0:
            return f"{hours}h {remaining_mins}m" if remaining_mins else f"{hours}h"
        return f"{minutes}m"

    def set_playtime(self, seconds: int):
        """Update displayed playtime without rebuilding the whole widget."""
        self.playtime_seconds = seconds
        self.playtime_label.setText(self._format_playtime(seconds))

    def set_version(self, version: str):
        """Set pure game version and position badge at bottom-left of banner."""
        self.version = str(version).strip() if version else ""
        if self.version:
            self.version_badge.setText(self.version)
            self.version_badge.adjustSize()
            self.version_badge.show()
            self._position_version_badge()
        else:
            self.version_badge.setText("")
            self.version_badge.hide()

    def _position_version_badge(self):
        if hasattr(self, 'version_badge') and self.version:
            self.version_badge.move(8, self.card_height - self.version_badge.height() - 8)
            self.version_badge.raise_()

    def mousePressEvent(self, event):
        super().mousePressEvent(event)
        if event.button() == Qt.MouseButton.LeftButton:
            self.clicked.emit(self.game_id)

    def showEvent(self, event):
        super().showEvent(event)
        # A child can be re-shown when its parent card is shown. Re-apply the
        # hover-only state after the card enters the widget hierarchy.
        if not self.underMouse():
            self.favorite_button.hide()

    def mouseDoubleClickEvent(self, event):
        super().mouseDoubleClickEvent(event)
        if event.button() == Qt.MouseButton.LeftButton:
            self.doubleClicked.emit(self.game_id)
            
    def enterEvent(self, event):
        super().enterEvent(event)
        self.favorite_button.show()
        self.favorite_button.raise_()
        if not self.is_missing:
            self.anim.stop()
            self.anim.setStartValue(self._hover_progress)
            self.anim.setEndValue(1.0)
            self.anim.start()

    def leaveEvent(self, event):
        super().leaveEvent(event)
        self.favorite_button.hide()
        if not self.is_missing:
            self.anim.stop()
            self.anim.setStartValue(self._hover_progress)
            self.anim.setEndValue(0.0)
            self.anim.start()

    def _on_anim_frame(self, value: float):
        self._hover_progress = value
        self.render_frame(value)

    def set_banner(self, banner_path: str):
        """Set the banner path and update render"""
        self.banner_path = banner_path
        self.update_appearance()
    
    def set_selected(self, selected: bool):
        """Toggle selected state"""
        self.selected = selected
        self.update_appearance()

    def set_missing(self, is_missing: bool):
        """Grey out card if game files are missing on drive"""
        self.is_missing = is_missing
        if is_missing:
            self.setToolTip("Missing on Drive: Game directory does not exist")
        else:
            self.setToolTip("")
        self.update_appearance()

    def update_appearance(self):
        """Update container styling (borderless) and trigger frame render"""
        self.setStyleSheet("border: none; background: transparent;")
        
        if self.is_missing:
            self.name_label.setText(f"{self.name} (Missing)")
            self.name_label.setStyleSheet("padding: 4px; color: #71717a; font-weight: bold;")
            self.image_label.setStyleSheet("background: #18181f; border: 1px solid #272730; border-radius: 8px;")
        elif self.selected:
            self.name_label.setText(self.name)
            self.name_label.setStyleSheet("padding: 4px; background: #3b3f46; color: #ffffff; font-weight: bold; border-radius: 4px;")
            self.image_label.setStyleSheet("background: #18181f; border: 2px solid #6b7280; border-radius: 8px;")
        else:
            self.name_label.setText(self.name)
            self.name_label.setStyleSheet("padding: 4px; color: #f4f4f5; font-weight: bold;")
            self.image_label.setStyleSheet("background: #18181f; border: 1px solid #272730; border-radius: 8px;")

        self.render_frame(self._hover_progress)

    def set_favorite(self, is_favorite: bool):
        self.is_favorite = is_favorite
        self.favorite_button.setChecked(is_favorite)
        icon = get_icon("ph.star-fill" if is_favorite else "ph.star", color="#facc15" if is_favorite else "#d4d4d8")
        self.favorite_button.setIcon(icon)
        self.favorite_button.setText("" if not icon.isNull() else "*")
        self.favorite_button.setToolTip("Remove from Favorites" if is_favorite else "Add to Favorites")
        self.render_frame(self._hover_progress)

    def _position_favorite_button(self):
        self.favorite_button.move(self.card_width - self.favorite_button.width() - 8, 8)
        self.favorite_button.raise_()

    def set_update_available(self, is_available: bool):
        self.is_update_available = is_available
        if hasattr(self, 'update_indicator'):
            self.update_indicator.setVisible(is_available)
            if is_available:
                self.update_indicator.raise_()
        self.render_frame(self._hover_progress)

    def set_card_size(self, width: int):
        """Dynamically resize card banner width and height (2:3 ratio)."""
        self.card_width = width
        self.card_height = int(width * 1.5)
        self.setFixedSize(QSize(self.card_width, self.card_height + 55))
        self.image_label.setFixedSize(QSize(self.card_width, self.card_height))
        self._position_favorite_button()
        self._position_version_badge()
        self.render_frame(self._hover_progress)

    def render_frame(self, progress: float):
        """Render cover art with LERP zoom & dark overlay on hover"""
        target_w, target_h = self.card_width, self.card_height
        
        # 1. Missing game state (greyed out)
        if self.is_missing:
            if self.banner_path and self.banner_path != "none" and os.path.exists(self.banner_path):
                pixmap = QPixmap(self.banner_path)
                if not pixmap.isNull():
                    scaled = pixmap.scaled(
                        QSize(target_w, target_h),
                        Qt.AspectRatioMode.KeepAspectRatioByExpanding,
                        Qt.TransformationMode.SmoothTransformation
                    )
                    crop_x = max(0, (scaled.width() - target_w) // 2)
                    crop_y = max(0, (scaled.height() - target_h) // 2)
                    cropped = scaled.copy(crop_x, crop_y, target_w, target_h)
                    
                    greyed = QPixmap(cropped.size())
                    greyed.fill(Qt.GlobalColor.transparent)
                    painter = QPainter(greyed)
                    painter.drawPixmap(0, 0, cropped)
                    painter.fillRect(greyed.rect(), QColor(20, 20, 20, 175))
                    painter.end()
                    
                    self.image_label.setPixmap(greyed)
                    self.image_label.setText("")
                    if hasattr(self, 'update_indicator') and self.is_update_available:
                        self.update_indicator.raise_()
                    self._position_version_badge()
                    return
            
            self.image_label.setPixmap(QPixmap())
            self.image_label.setText(f"{self.name}\n(Missing)")
            self.image_label.setStyleSheet(
                "background: #181818; color: #777777; font-weight: bold; font-size: 12px; padding: 10px; border-radius: 6px;"
            )
            return

        # 2. Normal game state with LERP hover zoom + smooth hover darkening!
        if self.banner_path and self.banner_path != "none" and os.path.exists(self.banner_path):
            pixmap = QPixmap(self.banner_path)
            if not pixmap.isNull():
                scale_factor = 1.0 + (0.04 * progress)
                zoom_w = int(target_w * scale_factor)
                zoom_h = int(target_h * scale_factor)
                
                scaled = pixmap.scaled(
                    QSize(zoom_w, zoom_h),
                    Qt.AspectRatioMode.KeepAspectRatioByExpanding,
                    Qt.TransformationMode.SmoothTransformation
                )
                crop_x = max(0, (scaled.width() - target_w) // 2)
                crop_y = max(0, (scaled.height() - target_h) // 2)
                cropped = scaled.copy(crop_x, crop_y, target_w, target_h)

                # Apply smooth dark tint overlay on hover
                if progress > 0.0:
                    darkened = QPixmap(cropped.size())
                    darkened.fill(Qt.GlobalColor.transparent)
                    p = QPainter(darkened)
                    p.drawPixmap(0, 0, cropped)
                    p.fillRect(darkened.rect(), QColor(0, 0, 0, int(70 * progress)))
                    p.end()
                    cropped = darkened

                self.image_label.setPixmap(cropped)
                self.image_label.setText("")
                if hasattr(self, 'update_indicator') and self.is_update_available:
                    self.update_indicator.raise_()
                self._position_version_badge()
                return

        # 3. Placeholder card when cover art is cleared ('none') or missing
        placeholder = QPixmap(target_w, target_h)
        placeholder.fill(QColor("#181818"))
        painter = QPainter(placeholder)
        painter.setPen(QColor("#777777"))
        painter.setFont(QFont("Monospace", 12, QFont.Weight.Bold))
        painter.drawText(placeholder.rect(), Qt.AlignmentFlag.AlignCenter, self.name)
        painter.end()
        self.image_label.setPixmap(placeholder)
        if hasattr(self, 'update_indicator') and self.is_update_available:
            self.update_indicator.raise_()
