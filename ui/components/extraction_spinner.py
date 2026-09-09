"""Small animated extraction indicator used by the main window."""

from PyQt6.QtCore import Qt, QTimer
from PyQt6.QtGui import QColor, QPainter, QPen
from PyQt6.QtWidgets import QFrame, QHBoxLayout, QLabel, QWidget


class _SpinnerGlyph(QWidget):
    """Paint a dependency-free twelve-segment animated wheel."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFixedSize(24, 24)
        self._step = 0
        self._timer = QTimer(self)
        self._timer.setInterval(75)
        self._timer.timeout.connect(self._advance)

    def start(self):
        self._timer.start()
        self.update()

    def stop(self):
        self._timer.stop()

    def _advance(self):
        self._step = (self._step + 1) % 12
        self.update()

    def paintEvent(self, event):
        del event
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.translate(self.width() / 2, self.height() / 2)
        for index in range(12):
            distance = (index - self._step) % 12
            alpha = 255 - distance * 18
            pen = QPen(QColor(59, 159, 232, max(45, alpha)))
            pen.setWidthF(2.4)
            pen.setCapStyle(Qt.PenCapStyle.RoundCap)
            painter.setPen(pen)
            painter.save()
            painter.rotate(index * 30)
            painter.drawLine(0, -7, 0, -11)
            painter.restore()


class ExtractionSpinner(QFrame):
    """Bottom-left, non-interactive indicator shown during archive extraction."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("extractionSpinner")
        self.setFixedSize(218, 46)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        self.setStyleSheet(
            "QFrame#extractionSpinner {"
            " background: #151A22; border: 1px solid #2A303B; border-radius: 9px;"
            "}"
            "QLabel { color: #F4F4F5; background: transparent;"
            " font-size: 11px; font-weight: 600; }"
        )

        layout = QHBoxLayout(self)
        layout.setContentsMargins(12, 0, 12, 0)
        layout.setSpacing(8)
        self.spinner = _SpinnerGlyph(self)
        layout.addWidget(self.spinner)
        label = QLabel("Extracting archive…", self)
        layout.addWidget(label)
        layout.addStretch()
        self.hide()

    def start(self):
        self.spinner.start()
        self.show()
        self.raise_()

    def stop(self):
        self.spinner.stop()
        self.hide()
