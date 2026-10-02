"""Small, theme-aware loading indicators shared by SafeLauncher surfaces."""

from __future__ import annotations

from PyQt6.QtCore import QPointF, Qt, QTimer
from PyQt6.QtGui import QColor, QPainter, QPen
from PyQt6.QtWidgets import QWidget


class LoadingSpinner(QWidget):
    """Dependency-free animated spinner for indeterminate work."""

    def __init__(self, parent=None, *, size: int = 24, color: str = "#3B9FE8"):
        super().__init__(parent)
        self.setFixedSize(int(size), int(size))
        self._step = 0
        self._color = QColor(color)
        self._timer = QTimer(self)
        self._timer.setInterval(75)
        self._timer.timeout.connect(self._advance)
        self.setAccessibleName("Loading")
        self.setAccessibleDescription("The requested operation is in progress.")
        self.hide()

    @property
    def running(self) -> bool:
        return self._timer.isActive()

    def set_color(self, color: str) -> None:
        self._color = QColor(color)
        self.update()

    def start(self) -> None:
        if not self._timer.isActive():
            self._timer.start()
        self.show()
        self.update()

    def stop(self) -> None:
        self._timer.stop()
        self.hide()

    def _advance(self) -> None:
        self._step = (self._step + 1) % 12
        self.update()

    def paintEvent(self, event) -> None:
        del event
        painter = QPainter(self)
        if not painter.isActive():
            return
        try:
            painter.setRenderHint(QPainter.RenderHint.Antialiasing)
            painter.translate(self.width() / 2, self.height() / 2)
            radius = max(3.0, min(self.width(), self.height()) / 2 - 3.0)
            inner = max(1.0, radius - 4.0)
            for index in range(12):
                distance = (index - self._step) % 12
                alpha = max(45, 255 - distance * 18)
                color = QColor(self._color)
                color.setAlpha(alpha)
                pen = QPen(color)
                pen.setWidthF(max(1.6, min(self.width(), self.height()) / 10.0))
                pen.setCapStyle(Qt.PenCapStyle.RoundCap)
                painter.setPen(pen)
                painter.save()
                painter.rotate(index * 30)
                painter.drawLine(QPointF(0.0, -inner), QPointF(0.0, -radius))
                painter.restore()
        finally:
            painter.end()


__all__ = ["LoadingSpinner"]
