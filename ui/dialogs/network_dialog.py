"""Focused-window dialog for an unexpected internet connection loss."""

from __future__ import annotations

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtWidgets import QHBoxLayout, QLabel, QPushButton, QVBoxLayout

from ui.components.popup_shell import PopupDialog


class NetworkUnavailableDialog(PopupDialog):
    """Ask how to proceed when online mode loses connectivity."""

    retry_requested = pyqtSignal()
    offline_requested = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__("Internet unavailable", parent)
        self.setModal(False)
        self.setMinimumWidth(430)
        self.setWindowModality(Qt.WindowModality.WindowModal)

        body = QVBoxLayout()
        body.setContentsMargins(24, 22, 24, 20)
        body.setSpacing(12)

        title = QLabel("Internet connection unavailable")
        title.setObjectName("popupTitle")
        body.addWidget(title)

        message = QLabel(
            "SafeLauncher is still in online mode, but the internet connection "
            "could not be reached. You can retry now or continue in Offline Mode."
        )
        message.setObjectName("popupSubtitle")
        message.setWordWrap(True)
        body.addWidget(message)

        hint = QLabel("Local games and cached data remain available either way.")
        hint.setObjectName("popupHint")
        hint.setWordWrap(True)
        body.addWidget(hint)

        buttons = QHBoxLayout()
        buttons.setSpacing(8)
        buttons.addStretch()
        self.btn_offline = QPushButton("Go to Offline Mode")
        self.btn_offline.setObjectName("popupSecondary")
        self.btn_offline.setToolTip("Stop automatic internet requests and use local or cached data.")
        self.btn_retry = QPushButton("Retry")
        self.btn_retry.setObjectName("popupPrimary")
        self.btn_retry.setDefault(True)
        self.btn_retry.setToolTip("Check the internet connection again.")
        buttons.addWidget(self.btn_offline)
        buttons.addWidget(self.btn_retry)
        body.addLayout(buttons)

        self.popup_layout().addLayout(body)
        self.btn_retry.clicked.connect(self.retry_requested)
        self.btn_offline.clicked.connect(self.offline_requested)

    def set_retrying(self, retrying: bool) -> None:
        self.btn_retry.setEnabled(not retrying)
        self.btn_offline.setEnabled(not retrying)
        self.btn_retry.setText("Checking…" if retrying else "Retry")


__all__ = ["NetworkUnavailableDialog"]
