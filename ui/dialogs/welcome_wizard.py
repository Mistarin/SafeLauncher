"""First-run introduction and setup wizard for SafeLauncher."""

import os
from PyQt6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QLineEdit, QPushButton,
    QFormLayout, QFileDialog, QCheckBox, QWidget, QSizeGrip
)
from PyQt6.QtCore import Qt
from PyQt6.QtGui import QFont, QIcon, QPixmap

from ui.icons import LOGO_PATH
from ui.components.sidebar import DialogTitleBar
from ui.dialogs.game_dialogs import ensure_sandbox_dir, DEFAULT_SANDBOX_DIR


class WelcomeWizardDialog(QDialog):
    """Clean, minimalist introduction setup wizard on first launch."""
    def __init__(self, user_name: str, proton_path: str = "", parent=None):
        super().__init__(parent)
        self.user_name = user_name
        self.proton_path = proton_path

        self.setWindowTitle("Welcome to SafeLauncher")
        if os.path.exists(LOGO_PATH):
            self.setWindowIcon(QIcon(LOGO_PATH))
        self.setMinimumSize(560, 440)
        self.resize(600, 480)
        self.setSizeGripEnabled(True)

        self.setStyleSheet("""
            QDialog {
                background-color: #121214;
                color: #ffffff;
            }
            QLabel {
                color: #e4e4e7;
            }
            QLineEdit {
                background: #1c1c20;
                color: #ffffff;
                border: 1px solid #333338;
                border-radius: 4px;
                padding: 8px 12px;
                font-size: 12px;
            }
            QLineEdit:focus {
                border: 1px solid #3b82f6;
                background: #222227;
            }
            QPushButton {
                background: #27272a;
                color: #ffffff;
                border: 1px solid #3f3f46;
                border-radius: 4px;
                padding: 8px 18px;
                font-size: 12px;
                font-weight: 600;
            }
            QPushButton:hover {
                background: #3f3f46;
            }
            QCheckBox {
                color: #a1a1aa;
                font-size: 12px;
            }
        """)

        root_layout = QVBoxLayout(self)
        root_layout.setContentsMargins(0, 0, 0, 0)
        root_layout.setSpacing(0)

        # Title bar
        self.title_bar = DialogTitleBar(self, "Welcome to SafeLauncher")
        root_layout.addWidget(self.title_bar)

        # Body container
        body = QWidget()
        body_layout = QVBoxLayout(body)
        body_layout.setContentsMargins(28, 20, 28, 20)
        body_layout.setSpacing(14)

        # Header
        header_layout = QHBoxLayout()
        header_layout.setSpacing(14)

        if os.path.exists(LOGO_PATH):
            logo_lbl = QLabel()
            pix = QPixmap(LOGO_PATH).scaled(48, 48, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation)
            logo_lbl.setPixmap(pix)
            header_layout.addWidget(logo_lbl)

        title_box = QVBoxLayout()
        title_box.setSpacing(3)
        title_lbl = QLabel("Welcome to SafeLauncher")
        title_lbl.setFont(QFont("Arial", 16, QFont.Weight.Bold))
        title_lbl.setStyleSheet("color: #ffffff;")
        title_box.addWidget(title_lbl)

        sub_lbl = QLabel("Isolated, container-hardened game sandbox manager for Linux.")
        sub_lbl.setStyleSheet("color: #a1a1aa; font-size: 12px;")
        title_box.addWidget(sub_lbl)

        header_layout.addLayout(title_box)
        header_layout.addStretch()
        body_layout.addLayout(header_layout)

        # Separator line
        sep = QLabel()
        sep.setFixedHeight(1)
        sep.setStyleSheet("background: #27272a;")
        body_layout.addWidget(sep)

        # Form fields
        form = QFormLayout()
        form.setSpacing(12)
        form.setLabelAlignment(Qt.AlignmentFlag.AlignLeft)

        self.name_input = QLineEdit(self.user_name)
        self.name_input.setPlaceholderText("Enter your display name")
        form.addRow("Player Name:", self.name_input)

        sandbox_dir = ensure_sandbox_dir()
        self.dir_input = QLineEdit(sandbox_dir)
        self.dir_input.setReadOnly(True)
        form.addRow("Sandbox Folder:", self.dir_input)

        proton_row = QHBoxLayout()
        self.proton_input = QLineEdit(self.proton_path)
        self.proton_input.setPlaceholderText("Leave blank for automatic UMU Proton runtime")
        proton_row.addWidget(self.proton_input)

        browse_btn = QPushButton("Browse")
        browse_btn.clicked.connect(self._browse_proton)
        proton_row.addWidget(browse_btn)

        form.addRow("Proton Path:", proton_row)
        body_layout.addLayout(form)

        body_layout.addStretch()

        # Checkbox: Don't show again
        self.chk_show_startup = QCheckBox("Show this welcome screen on startup")
        self.chk_show_startup.setChecked(False)
        body_layout.addWidget(self.chk_show_startup)

        # Action button
        btn_start = QPushButton("Get Started")
        btn_start.setMinimumHeight(38)
        btn_start.setStyleSheet("""
            QPushButton {
                background: #2563eb; color: #ffffff; border: none;
                border-radius: 4px; padding: 8px 24px; font-weight: bold; font-size: 13px;
            }
            QPushButton:hover { background: #1d4ed8; }
        """)
        btn_start.clicked.connect(self._finish)
        body_layout.addWidget(btn_start)

        root_layout.addWidget(body)

    def _browse_proton(self):
        path = QFileDialog.getExistingDirectory(self, "Select Proton directory", os.path.expanduser("~/.local/share"))
        if path:
            self.proton_input.setText(path)

    def _finish(self):
        if self.name_input.text().strip():
            self.user_name = self.name_input.text().strip()
        self.proton_path = self.proton_input.text().strip()
        self.accept()

    def get_user_name(self) -> str:
        return self.user_name

    def get_proton_path(self) -> str:
        return self.proton_path

    def should_show_on_startup(self) -> bool:
        return self.chk_show_startup.isChecked()
