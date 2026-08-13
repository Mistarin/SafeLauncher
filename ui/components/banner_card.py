import os
from PyQt6.QtWidgets import QFrame, QVBoxLayout, QLabel, QPushButton
from PyQt6.QtCore import Qt, QSize, QPoint, pyqtSignal, QVariantAnimation, QEasingCurve
from PyQt6.QtGui import QFont, QPixmap, QColor, QPainter

from ui.icons import get_app_icon, get_icon


class GameBannerWidget(QFrame):
    """Individual borderless game banner card with pixel font title and LERP zoom"""
    clicked = pyqtSignal(int)
    doubleClicked = pyqtSignal(int)
    rightClicked = pyqtSignal(int, QPoint)
    favoriteClicked = pyqtSignal(int)

    def __init__(self, game_id: int, name: str, banner_path: str = None, playtime_seconds: int = 0, parent=None):
        super().__init__(parent)
        self.game_id = game_id
        self.name = name
        self.banner_path = banner_path
        self.playtime_seconds = playtime_seconds
        self.selected = False
        self.is_missing = False
        self.is_favorite = False
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
            return "⏱ Never played" if not seconds else f"⏱ {seconds}s"
        minutes = seconds // 60
        hours = minutes // 60
        remaining_mins = minutes % 60
        if hours > 0:
            return f"⏱ {hours}h {remaining_mins}m" if remaining_mins else f"⏱ {hours}h"
        return f"⏱ {minutes}m"

    def set_playtime(self, seconds: int):
        """Update displayed playtime without rebuilding the whole widget."""
        self.playtime_seconds = seconds
        self.playtime_label.setText(self._format_playtime(seconds))

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
            self.setToolTip("⚠️ Missing on Drive: Game directory does not exist")
        else:
            self.setToolTip("")
        self.update_appearance()

    def update_appearance(self):
        """Update container styling (borderless) and trigger frame render"""
        self.setStyleSheet("border: none; background: transparent;")
        
        if self.is_missing:
            self.name_label.setText(f"⚠️ {self.name} (Missing)")
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
        self.favorite_button.setText("" if not icon.isNull() else "★")
        self.favorite_button.setToolTip("Remove from Favorites" if is_favorite else "Add to Favorites")
        self.render_frame(self._hover_progress)

    def _position_favorite_button(self):
        self.favorite_button.move(self.card_width - self.favorite_button.width() - 8, 8)
        self.favorite_button.raise_()

    def set_update_available(self, is_available: bool):
        self.is_update_available = is_available
        self.render_frame(self._hover_progress)

    def _overlay_update_badge(self, pixmap: QPixmap) -> QPixmap:
        """Overlay green '🟢 Update' badge in top-left corner of card if update is available."""
        if not getattr(self, 'is_update_available', False):
            return pixmap
        res = QPixmap(pixmap)
        painter = QPainter(res)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        
        bx, by, bw, bh = 8, 8, 70, 22
        painter.setBrush(QColor(6, 95, 70, 230))
        painter.setPen(QColor(52, 211, 153, 220))
        painter.drawRoundedRect(bx, by, bw, bh, 6, 6)

        painter.setPen(QColor(52, 211, 153))
        painter.setFont(QFont("Arial", 8, QFont.Weight.Bold))
        painter.drawText(bx, by, bw, bh, Qt.AlignmentFlag.AlignCenter, "🟢 Update")
        painter.end()
        return res

    def set_card_size(self, width: int):
        """Dynamically resize card banner width and height (2:3 ratio)."""
        self.card_width = width
        self.card_height = int(width * 1.5)
        self.setFixedSize(QSize(self.card_width, self.card_height + 55))
        self.image_label.setFixedSize(QSize(self.card_width, self.card_height))
        self._position_favorite_button()
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
                    return
            
            self.image_label.setPixmap(QPixmap())
            self.image_label.setText(f"⚠️\n\n{self.name}\n(Missing)")
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

                self.image_label.setPixmap(self._overlay_update_badge(cropped))
                self.image_label.setText("")
                return

        # 3. Placeholder card when cover art is cleared ('none') or missing
        placeholder = QPixmap(target_w, target_h)
        placeholder.fill(QColor("#181818"))
        painter = QPainter(placeholder)
        painter.setPen(QColor("#777777"))
        painter.setFont(QFont("Monospace", 12, QFont.Weight.Bold))
        painter.drawText(placeholder.rect(), Qt.AlignmentFlag.AlignCenter, self.name)
        painter.end()
        self.image_label.setPixmap(self._overlay_update_badge(placeholder))
