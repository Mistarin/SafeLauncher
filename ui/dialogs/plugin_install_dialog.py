from PyQt6.QtWidgets import QApplication, QHBoxLayout, QLabel, QLineEdit, QPushButton
from PyQt6.QtGui import QFont
from core.plugins.gpu_screen_recorder import WlScreenrecService
from ui.components.popup_shell import PopupDialog
from ui.components.sort_combo import SortComboBox
from core.logger import get_logger
logger = get_logger("plugin_install_dialog")


class PluginInstallNoticeDialog(PopupDialog):
    """Notice dialog explaining why root/sudo privileges are required for AUR package installation."""
    def __init__(self, parent=None):
        super().__init__("Install GPU Screen Recorder", parent)
        self.setMinimumSize(540, 320)
        self.setSizeGripEnabled(True)
        self.setStyleSheet("""
            QDialog { background: #121214; color: #ffffff; }
            QLabel { color: #d4d4d8; font-size: 12px; }
            QLineEdit { background: #1c1c20; color: #3B9FE8; border: 1px solid #333338; border-radius: 4px; padding: 6px; }
            QPushButton {
                background: #27272a; color: #ffffff; border: 1px solid #2A2A2E;
                border-radius: 4px; padding: 8px 16px; font-weight: bold; font-size: 12px;
            }
            QPushButton:hover { background: #2A2A2E; }
        """)

        layout = self.popup_layout(margins=(20, 18, 20, 18), spacing=14)

        title = QLabel("Install Hardware Recorder (gpu-screen-recorder)")
        title.setFont(QFont("Arial", 14, QFont.Weight.Bold))
        title.setStyleSheet("color: #ffffff;")
        layout.addWidget(title)

        notice_text = (
            "To enable zero-overhead hardware NVENC / VAAPI recording and shadowplay instant replay "
            "on KDE Plasma Wayland, SafeLauncher uses the open-source 'gpu-screen-recorder' package.\n\n"
            "Why privileges are required:\n"
            "Building and installing places the compiled binary and capture capabilities into /usr/bin/gpu-screen-recorder, "
            "which requires administrator (sudo) privileges on your system.\n\n"
            "You can choose to install automatically via your terminal AUR helper (paru/yay), "
            "via flatpak, or copy the command and install it manually."
        )
        msg = QLabel(notice_text)
        msg.setWordWrap(True)
        msg.setStyleSheet("color: #a1a1aa;")
        layout.addWidget(msg)

        self.notice_option_combo = SortComboBox()
        for label, cmd in WlScreenrecService.get_install_options():
            self.notice_option_combo.addItem(label, cmd)
        layout.addWidget(self.notice_option_combo)

        cmd_box = QLineEdit(WlScreenrecService.get_install_options()[0][1])
        cmd_box.setReadOnly(True)
        self.notice_cmd_box = cmd_box
        self.notice_option_combo.currentIndexChanged.connect(
            lambda idx: cmd_box.setText(self.notice_option_combo.itemData(idx) or ""))
        layout.addWidget(cmd_box)

        layout.addStretch()

        btn_box = QHBoxLayout()
        btn_box.setSpacing(8)

        btn_cancel = QPushButton("Cancel")
        btn_cancel.clicked.connect(self.reject)
        btn_box.addWidget(btn_cancel)

        btn_copy = QPushButton("Copy Command & Close")
        btn_copy.clicked.connect(self._copy_and_close)
        btn_box.addWidget(btn_copy)

        btn_install = QPushButton("Install via Terminal")
        btn_install.setStyleSheet("QPushButton { background: #3B9FE8; color: #ffffff; border: none; } QPushButton:hover { background: #2789D0; }")
        btn_install.clicked.connect(self._launch_install)
        btn_box.addWidget(btn_install)

        layout.addLayout(btn_box)

    def _copy_and_close(self):
        from PyQt6.QtWidgets import QApplication
        QApplication.clipboard().setText(self.notice_cmd_box.text())
        self.accept()

    def _launch_install(self):
        WlScreenrecService.launch_terminal_installer(self)
        self.accept()
