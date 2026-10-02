"""Small animated extraction indicator used by the main window."""

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import QFrame, QHBoxLayout, QLabel

from ui.components.loading_spinner import LoadingSpinner


class ExtractionSpinner(QFrame):
    """Bottom-left, non-interactive indicator shown during archive extraction."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setObjectName("extractionSpinner")
        self.setFixedSize(218, 46)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)
        self.setStyleSheet(
            "QFrame#extractionSpinner {"
            " background: #151A22; border: 1px solid #2A2A2E; border-radius: 9px;"
            "}"
            "QLabel { color: #F4F4F5; background: transparent;"
            " font-size: 11px; font-weight: 600; }"
        )

        layout = QHBoxLayout(self)
        layout.setContentsMargins(12, 0, 12, 0)
        layout.setSpacing(8)
        self.spinner = LoadingSpinner(self, size=24)
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
