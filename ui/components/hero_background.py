import os
from typing import Optional
from PyQt6.QtWidgets import QWidget
from PyQt6.QtCore import Qt, QVariantAnimation, QEasingCurve
from PyQt6.QtGui import QPixmap, QColor, QPainter, QLinearGradient


class HeroBackgroundWidget(QWidget):
    """Custom background container widget that renders a blurred 16:9 hero image with smooth cross-fade animation."""
    def __init__(self, parent=None):
        super().__init__(parent)
        self.current_pixmap = None
        self.target_pixmap = None
        self.opacity = 1.0
        self.setAttribute(Qt.WidgetAttribute.WA_StyledBackground, True)
        
        self.fade_anim = QVariantAnimation(self)
        self.fade_anim.setDuration(350)
        self.fade_anim.setStartValue(0.0)
        self.fade_anim.setEndValue(1.0)
        self.fade_anim.setEasingCurve(QEasingCurve.Type.InOutCubic)
        self.fade_anim.valueChanged.connect(self._on_fade_value_changed)
        self.fade_anim.finished.connect(self._on_fade_finished)

    def _on_fade_value_changed(self, value):
        self.opacity = value
        self.update()

    def _on_fade_finished(self):
        if self.target_pixmap:
            self.current_pixmap = self.target_pixmap
            self.target_pixmap = None
        self.opacity = 1.0
        self.update()

    def set_hero_image(self, image_path: Optional[str]):
        if image_path and os.path.exists(image_path):
            original = QPixmap(image_path)
            if not original.isNull():
                new_pix = self._process_blurred_pixmap(original)
                if self.current_pixmap is None:
                    self.current_pixmap = new_pix
                    self.opacity = 1.0
                    self.update()
                else:
                    self.target_pixmap = new_pix
                    self.fade_anim.stop()
                    self.fade_anim.start()
                return

        # Smooth fade out if clearing background
        if self.current_pixmap is not None:
            self.target_pixmap = None
            self.fade_anim.stop()
            self.fade_anim.start()
        else:
            self.current_pixmap = None
            self.target_pixmap = None
            self.update()

    def _process_blurred_pixmap(self, original: QPixmap) -> QPixmap:
        """Create a heavily blurred and darkened 1920x1080 background pixmap while preserving aspect ratio."""
        w, h = 1920, 1080
        scaled_down = original.scaled(w // 8, h // 8, Qt.AspectRatioMode.KeepAspectRatioByExpanding, Qt.TransformationMode.SmoothTransformation)
        blurred = scaled_down.scaled(w, h, Qt.AspectRatioMode.KeepAspectRatioByExpanding, Qt.TransformationMode.SmoothTransformation)
        
        res = QPixmap(w, h)
        res.fill(QColor(13, 13, 16))
        
        painter = QPainter(res)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        
        crop_x = max(0, (blurred.width() - w) // 2)
        crop_y = max(0, (blurred.height() - h) // 2)
        painter.drawPixmap(0, 0, blurred, crop_x, crop_y, w, h)
        
        gradient = QLinearGradient(0, 0, 0, h)
        gradient.setColorAt(0.0, QColor(11, 11, 14, 180))
        gradient.setColorAt(0.4, QColor(11, 11, 14, 210))
        gradient.setColorAt(1.0, QColor(11, 11, 14, 240))
        painter.fillRect(0, 0, w, h, gradient)
        
        painter.end()
        return res

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        w, h = self.width(), self.height()
        
        # Base background fill
        painter.fillRect(self.rect(), QColor(13, 13, 16))

        # Paint current background pixmap
        if self.current_pixmap and not self.current_pixmap.isNull():
            scaled_curr = self.current_pixmap.scaled(self.size(), Qt.AspectRatioMode.KeepAspectRatioByExpanding, Qt.TransformationMode.SmoothTransformation)
            crop_x = max(0, (scaled_curr.width() - w) // 2)
            crop_y = max(0, (scaled_curr.height() - h) // 2)
            if self.target_pixmap:
                painter.setOpacity(max(0.0, 1.0 - self.opacity))
            else:
                painter.setOpacity(self.opacity)
            painter.drawPixmap(0, 0, scaled_curr, crop_x, crop_y, w, h)

        # Paint target background pixmap fading in over current
        if self.target_pixmap and not self.target_pixmap.isNull():
            scaled_target = self.target_pixmap.scaled(self.size(), Qt.AspectRatioMode.KeepAspectRatioByExpanding, Qt.TransformationMode.SmoothTransformation)
            crop_x = max(0, (scaled_target.width() - w) // 2)
            crop_y = max(0, (scaled_target.height() - h) // 2)
            painter.setOpacity(self.opacity)
            painter.drawPixmap(0, 0, scaled_target, crop_x, crop_y, w, h)

        painter.end()
        super().paintEvent(event)
