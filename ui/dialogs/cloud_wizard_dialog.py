"""Interactive setup wizard dialog for SafeLauncher private cloud saves."""

import threading
import requests
from PyQt6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QLineEdit,
    QPushButton, QStackedWidget, QWidget, QMessageBox, QApplication
)
from PyQt6.QtCore import Qt, pyqtSignal, QSettings

from core.cloud_detector import discover_local_cloud_backend
from core.cloud_backend import get_site_url


class CloudWizardDialog(QDialog):
    """3-step wizard to guide users through deploying and connecting their Convex cloud backend."""

    test_completed = pyqtSignal(bool, str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Cloud Save Setup Wizard")
        self.resize(560, 420)
        self.setStyleSheet("""
            QDialog {
                background-color: #121214;
                color: #EDEDED;
                font-family: 'Segoe UI', system-ui, sans-serif;
            }
            QLabel {
                color: #D1D5DB;
                font-size: 13px;
            }
            QLineEdit {
                background-color: #1E1E22;
                border: 1px solid #2E2E34;
                border-radius: 6px;
                padding: 8px 12px;
                color: #FFFFFF;
                font-size: 13px;
            }
            QLineEdit:focus {
                border: 1px solid #3B82F6;
            }
            QPushButton {
                background-color: #27272A;
                border: 1px solid #3F3F46;
                border-radius: 6px;
                padding: 8px 16px;
                color: #FFFFFF;
                font-size: 13px;
                font-weight: 500;
            }
            QPushButton:hover {
                background-color: #323238;
            }
            QPushButton#primaryBtn {
                background-color: #2563EB;
                border: 1px solid #3B82F6;
            }
            QPushButton#primaryBtn:hover {
                background-color: #1D4ED8;
            }
        """)

        self.layout = QVBoxLayout(self)
        self.layout.setContentsMargins(24, 24, 24, 24)
        self.layout.setSpacing(16)

        # Header
        self.title_lbl = QLabel("Private Cloud Save Setup")
        self.title_lbl.setStyleSheet("font-size: 18px; font-weight: bold; color: #FFFFFF;")
        self.layout.addWidget(self.title_lbl)

        self.subtitle_lbl = QLabel("Step 1 of 2: Deploy your free Convex backend")
        self.subtitle_lbl.setStyleSheet("color: #9CA3AF; font-size: 13px;")
        self.layout.addWidget(self.subtitle_lbl)

        # Stacked Pages
        self.pages = QStackedWidget()
        self.pages.addWidget(self._create_step1_widget())
        self.pages.addWidget(self._create_step2_widget())
        self.layout.addWidget(self.pages, 1)

        # Bottom Buttons
        btn_layout = QHBoxLayout()
        self.btn_cancel = QPushButton("Cancel")
        self.btn_cancel.clicked.connect(self.reject)
        btn_layout.addWidget(self.btn_cancel)

        btn_layout.addStretch()

        self.btn_back = QPushButton("Back")
        self.btn_back.clicked.connect(self._go_back)
        self.btn_back.setEnabled(False)
        btn_layout.addWidget(self.btn_back)

        self.btn_next = QPushButton("Next")
        self.btn_next.setObjectName("primaryBtn")
        self.btn_next.clicked.connect(self._go_next)
        btn_layout.addWidget(self.btn_next)

        self.layout.addLayout(btn_layout)

        self.test_completed.connect(self._on_test_completed)

        # Auto-detect local backend if available
        discovered = discover_local_cloud_backend()
        if discovered and not self.edit_url.text().strip():
            self.edit_url.setText(discovered)

    def _create_step1_widget(self) -> QWidget:
        widget = QWidget()
        layout = QVBoxLayout(widget)
        layout.setContentsMargins(0, 8, 0, 0)
        layout.setSpacing(12)

        desc = QLabel(
            "SafeLauncher stores game saves encrypted on your personal Convex instance.\n"
            "Convex provides 1 GB free storage without monthly costs or credit cards."
        )
        desc.setWordWrap(True)
        layout.addWidget(desc)

        cmd_box = QLabel(
            "git clone https://github.com/Mistarin/SafeLauncherCloud.git\n"
            "cd SafeLauncherCloud\n"
            "npm install\n"
            "npx convex deploy"
        )
        cmd_box.setStyleSheet("""
            background-color: #18181B;
            border: 1px solid #27272A;
            border-radius: 6px;
            padding: 12px;
            font-family: 'JetBrains Mono', 'Fira Code', monospace;
            font-size: 12px;
            color: #10B981;
        """)
        layout.addWidget(cmd_box)

        btn_copy = QPushButton("Copy Deploy Commands to Clipboard")
        btn_copy.clicked.connect(lambda: self._copy_commands(cmd_box.text()))
        layout.addWidget(btn_copy)

        hint = QLabel("Once 'npx convex deploy' finishes, it will print your project's .convex.site URL. Click Next to connect.")
        hint.setStyleSheet("color: #9CA3AF; font-size: 12px;")
        hint.setWordWrap(True)
        layout.addWidget(hint)
        layout.addStretch()

        return widget

    def _create_step2_widget(self) -> QWidget:
        widget = QWidget()
        layout = QVBoxLayout(widget)
        layout.setContentsMargins(0, 8, 0, 0)
        layout.setSpacing(14)

        layout.addWidget(QLabel("Paste your Convex Site URL below:"))

        settings = QSettings("SafeLauncher", "SafeLauncher")
        existing_url = settings.value("convex_site_url", "", type=str) or get_site_url()

        self.edit_url = QLineEdit(existing_url)
        self.edit_url.setPlaceholderText("https://your-project.convex.site")
        layout.addWidget(self.edit_url)

        layout.addWidget(QLabel("Secret Access Key (optional, if configured via SAFELAUNCHER_SECRET_KEY):"))
        existing_key = settings.value("cloud_secret_key", "", type=str)
        self.edit_key = QLineEdit(existing_key)
        self.edit_key.setEchoMode(QLineEdit.EchoMode.Password)
        self.edit_key.setPlaceholderText("Leave blank if none")
        layout.addWidget(self.edit_key)

        self.status_lbl = QLabel("")
        self.status_lbl.setWordWrap(True)
        layout.addWidget(self.status_lbl)

        layout.addStretch()
        return widget

    def _copy_commands(self, text: str):
        clipboard = QApplication.clipboard()
        if clipboard:
            clipboard.setText(text)
            QMessageBox.information(self, "Copied", "Commands copied to clipboard!")

    def _go_back(self):
        self.pages.setCurrentIndex(0)
        self.subtitle_lbl.setText("Step 1 of 2: Deploy your free Convex backend")
        self.btn_back.setEnabled(False)
        self.btn_next.setText("Next")

    def _go_next(self):
        if self.pages.currentIndex() == 0:
            self.pages.setCurrentIndex(1)
            self.subtitle_lbl.setText("Step 2 of 2: Connect SafeLauncher")
            self.btn_back.setEnabled(True)
            self.btn_next.setText("Test & Connect")
        else:
            self._test_and_save()

    def _test_and_save(self):
        url = self.edit_url.text().strip().rstrip("/")
        key = self.edit_key.text().strip()

        if not url:
            self.status_lbl.setText("<font color='#EF4444'>Please enter your Convex Site URL.</font>")
            return

        if not url.startswith("http://") and not url.startswith("https://"):
            url = "https://" + url
            self.edit_url.setText(url)

        self.btn_next.setEnabled(False)
        self.btn_back.setEnabled(False)
        self.status_lbl.setText("<font color='#3B82F6'>Connecting to backend...</font>")

        def _worker():
            try:
                headers = {}
                if key:
                    headers["Authorization"] = f"Bearer {key}"
                    headers["X-SafeLauncher-Key"] = key

                resp = requests.get(f"{url}/api/health", headers=headers, timeout=6)
                if resp.status_code != 200:
                    self.test_completed.emit(False, f"Health check failed with HTTP {resp.status_code}")
                    return

                resp_me = requests.get(f"{url}/api/me", headers=headers, timeout=6)
                if resp_me.status_code == 200:
                    data = resp_me.json()
                    quota_mb = data.get("quotaBytes", 0) / (1024 * 1024)
                    self.test_completed.emit(True, f"Connected! Available quota: {quota_mb:.0f} MB")
                else:
                    self.test_completed.emit(True, "Backend reachable!")
            except Exception as e:
                self.test_completed.emit(False, str(e))

        threading.Thread(target=_worker, daemon=True, name="SafeLauncher-WizardTest").start()

    def _on_test_completed(self, success: bool, message: str):
        self.btn_next.setEnabled(True)
        self.btn_back.setEnabled(True)
        if success:
            url = self.edit_url.text().strip().rstrip("/")
            key = self.edit_key.text().strip()
            settings = QSettings("SafeLauncher", "SafeLauncher")
            settings.setValue("cloud_mode", "convex")
            settings.setValue("convex_site_url", url)
            if key:
                settings.setValue("cloud_secret_key", key)
            else:
                settings.remove("cloud_secret_key")

            self.status_lbl.setText(f"<font color='#10B981'>{message}</font>")
            QMessageBox.information(self, "Cloud Connected", "SafeLauncher is now connected to your private cloud backend.")
            self.accept()
        else:
            self.status_lbl.setText(f"<font color='#EF4444'>Connection failed: {message}</font>")
