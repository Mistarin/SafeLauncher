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
    launchClicked = pyqtSignal(int)

    def __init__(self, game_id: int, name: str, banner_path: str = None, playtime_seconds: int = 0, version: str = "", icon_path: str = "", parent=None):
        super().__init__(parent)
        self.game_id = game_id
        self.name = name
        self.banner_path = banner_path
        self.icon_path = str(icon_path).strip() if icon_path else ""
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
        layout.setSpacing(0)
        
        # Banner image (2:3 portrait aspect ratio matching Steam 600x900 library covers)
        self.image_label = QLabel(self)
        self.image_label.setFixedSize(QSize(self.card_width, self.card_height))
        self.image_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(self.image_label)

        # Green animated pulsing circle indicator for updates
        self.update_indicator = UpdatePulsingDotWidget(self)
        self.update_indicator.move(8, 8)
        self.update_indicator.hide()

        # Favorite belongs to the library card itself
        # Favorite button
        self.favorite_button = QPushButton(self)
        self.favorite_button.setFixedSize(28, 28)
        self.favorite_button.setCursor(Qt.CursorShape.PointingHandCursor)
        self.favorite_button.setCheckable(True)
        self.favorite_button.setIconSize(QSize(16, 16))
        self.favorite_button.setStyleSheet("""
            QPushButton {
                background: rgba(20, 23, 29, 0.75);
                border: 1px solid #252A33;
                border-radius: 6px;
                padding: 0;
            }
            QPushButton:hover {
                background: #1A1E26;
                border-color: #6F7682;
            }
        """)
        self.favorite_button.clicked.connect(lambda: self.favoriteClicked.emit(self.game_id))
        self.favorite_button.hide()
        self._position_favorite_button()

        # Quick-Launch Play button overlay (prominently centered on hover)
        self.btn_card_play = QPushButton(self)
        self.btn_card_play.setFixedSize(44, 44)
        self.btn_card_play.setCursor(Qt.CursorShape.PointingHandCursor)
        self.btn_card_play.setIcon(get_icon("ph.play-fill", color="#FFFFFF"))
        self.btn_card_play.setIconSize(QSize(18, 18))
        self.btn_card_play.setToolTip(f"Launch {self.name}")
        self.btn_card_play.setStyleSheet("""
            QPushButton {
                background: #3B9FE8;
                color: #FFFFFF;
                border: 1px solid #3B9FE8;
                border-radius: 22px;
                padding: 0;
            }
            QPushButton:hover {
                background: #55ACED;
                border-color: #55ACED;
            }
            QPushButton:pressed {
                background: #2789D0;
            }
        """)
        self.btn_card_play.clicked.connect(lambda: self.launchClicked.emit(self.game_id))
        self.btn_card_play.hide()
        self._position_play_button()

        # Pure version badge positioned at bottom-left of the banner cover
        self.version_badge = QLabel(self)
        self.version_badge.setFont(QFont("Arial", 9, QFont.Weight.Bold))
        self.version_badge.setStyleSheet("""
            QLabel {
                background: rgba(20, 23, 29, 0.88);
                color: #A7ADB8;
                font-weight: 600;
                font-size: 10px;
                border-radius: 4px;
                padding: 2px 6px;
                border: 1px solid #252A33;
            }
        """)
        self.version_badge.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        self.set_version(self.version)
        
        # Footer container for Title & Playtime (vertically & horizontally centered)
        self.footer_widget = QWidget(self)
        self.footer_widget.setFixedSize(QSize(self.card_width, 55))
        self.footer_widget.setStyleSheet("background: transparent;")
        footer_layout = QVBoxLayout(self.footer_widget)
        footer_layout.setContentsMargins(4, 0, 4, 0)
        footer_layout.setSpacing(2)
        footer_layout.setAlignment(Qt.AlignmentFlag.AlignCenter)

        # Game name label using clean sans-serif typography
        self.name_label = QLabel(name)
        self.name_label.setWordWrap(True)
        self.name_label.setFont(QFont("Arial", 11, QFont.Weight.Bold))
        self.name_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.name_label.setStyleSheet("color: #F5F7FA; background: transparent;")
        footer_layout.addWidget(self.name_label)

        # Playtime label — small, muted, centered below the title
        self.playtime_label = QLabel(self._format_playtime(playtime_seconds))
        self.playtime_label.setFont(QFont("Arial", 9))
        self.playtime_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.playtime_label.setStyleSheet("color: #A7ADB8; background: transparent;")
        footer_layout.addWidget(self.playtime_label)

        layout.addWidget(self.footer_widget)
        
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
            if hasattr(self, 'btn_card_play'):
                self.btn_card_play.hide()

    def mouseDoubleClickEvent(self, event):
        super().mouseDoubleClickEvent(event)
        if event.button() == Qt.MouseButton.LeftButton:
            self.doubleClicked.emit(self.game_id)
            
    def enterEvent(self, event):
        super().enterEvent(event)
        self.favorite_button.show()
        self.favorite_button.raise_()
        if not self.is_missing:
            if hasattr(self, 'btn_card_play'):
                self.btn_card_play.show()
                self.btn_card_play.raise_()
            if not self.selected:
                self.image_label.setStyleSheet("background: #18181f; border: 1px solid rgba(255, 255, 255, 0.25); border-radius: 8px;")
            self.anim.stop()
            self.anim.setStartValue(self._hover_progress)
            self.anim.setEndValue(1.0)
            self.anim.start()

    def leaveEvent(self, event):
        super().leaveEvent(event)
        self.favorite_button.hide()
        if hasattr(self, 'btn_card_play'):
            self.btn_card_play.hide()
        if not self.selected and not self.is_missing:
            self.image_label.setStyleSheet("background: #18181f; border: none; border-radius: 8px;")
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
        """Update container styling (borderless when not hovering) and trigger frame render"""
        self.setStyleSheet("border: none; background: transparent;")
        
        if self.is_missing:
            self.name_label.setText(self.name)
            self.name_label.setStyleSheet("padding: 4px; color: #6F7682; font-weight: 600;")
            self.image_label.setStyleSheet("background: #14171D; border: 1px solid #252A33; border-radius: 8px;")
        elif self.selected:
            self.name_label.setText(self.name)
            self.name_label.setStyleSheet("padding: 4px; background: #0D2A40; color: #3B9FE8; font-weight: 600; border-radius: 4px;")
            self.image_label.setStyleSheet("background: #14171D; border: 2px solid #3B9FE8; border-radius: 8px;")
        else:
            self.name_label.setText(self.name)
            self.name_label.setStyleSheet("padding: 4px; color: #F5F7FA; font-weight: 600;")
            self.image_label.setStyleSheet("background: #14171D; border: 1px solid #252A33; border-radius: 8px;")

        self.render_frame(self._hover_progress)

    def set_favorite(self, is_favorite: bool):
        self.is_favorite = is_favorite
        try:
            self.favorite_button.setChecked(is_favorite)
            icon = get_icon("ph.star-fill" if is_favorite else "ph.star-bold", color="#F5C451" if is_favorite else "#6F7682")
            self.favorite_button.setIcon(icon)
            self.favorite_button.setText("" if not icon.isNull() else "*")
            self.favorite_button.setToolTip("Remove from Favorites" if is_favorite else "Add to Favorites")
            self.render_frame(self._hover_progress)
        except (RuntimeError, AttributeError):
            pass

    def _position_favorite_button(self):
        try:
            self.favorite_button.move(self.card_width - self.favorite_button.width() - 8, 8)
            self.favorite_button.raise_()
        except (RuntimeError, AttributeError):
            pass

    def _position_play_button(self):
        try:
            if hasattr(self, 'btn_card_play') and self.btn_card_play:
                px = (self.card_width - 48) // 2
                py = (self.card_height - 48) // 2
                self.btn_card_play.move(px, py)
                self.btn_card_play.raise_()
        except (RuntimeError, AttributeError):
            pass

    def _position_version_badge(self):
        try:
            if hasattr(self, 'version_badge') and self.version_badge and self.version_badge.isVisible():
                self.version_badge.move(8, self.card_height - self.version_badge.height() - 8)
                self.version_badge.raise_()
        except (RuntimeError, AttributeError):
            pass

    def set_update_available(self, is_available: bool):
        self.is_update_available = is_available
        try:
            if hasattr(self, 'update_indicator') and self.update_indicator:
                self.update_indicator.setVisible(is_available)
                if is_available:
                    self.update_indicator.raise_()
        except (RuntimeError, AttributeError):
            pass
        try:
            self.render_frame(self._hover_progress)
        except (RuntimeError, AttributeError):
            pass

    def set_card_size(self, width: int):
        """Dynamically resize card banner width and height (2:3 ratio)."""
        try:
            self.card_width = width
            self.card_height = int(width * 1.5)
            self.setFixedSize(QSize(self.card_width, self.card_height + 55))
            self.image_label.setFixedSize(QSize(self.card_width, self.card_height))
            if hasattr(self, 'footer_widget'):
                self.footer_widget.setFixedSize(QSize(self.card_width, 55))
            self._position_favorite_button()
            self._position_play_button()
            self._position_version_badge()
            self.render_frame(self._hover_progress)
        except (RuntimeError, AttributeError):
            pass

    def set_icon(self, icon_path: str):
        """Set game icon path and re-render."""
        self.icon_path = str(icon_path).strip() if icon_path else ""
        self.render_frame(self._hover_progress)

    def render_frame(self, progress: float):
        """Render cover art with LERP zoom & hover overlay"""
        target_w, target_h = self.card_width, self.card_height
        
        # 2. Missing game state (greyed out fallback)
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
            
            placeholder = QPixmap(target_w, target_h)
            placeholder.fill(QColor("#111318"))
            painter = QPainter(placeholder)
            painter.setPen(QColor("#475569"))
            painter.setFont(QFont("Arial", 10, QFont.Weight.Bold))
            painter.drawText(placeholder.rect(), Qt.AlignmentFlag.AlignCenter, self.name)
            painter.end()
            self.image_label.setPixmap(placeholder)
            self.image_label.setText("")
            return

        # 3. Normal game state with LERP hover zoom + smooth hover darkening!
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

        # 4. Placeholder card when cover art is cleared ('none') or missing
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
