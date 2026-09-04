"""Interactive setup wizard dialog for SafeLauncher private cloud saves."""

import os
import threading
import requests
from PyQt6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QLabel, QLineEdit,
    QPushButton, QStackedWidget, QWidget, QMessageBox, QApplication,
    QRadioButton, QButtonGroup, QFrame, QScrollArea
)
from PyQt6.QtCore import Qt, pyqtSignal, QSettings, QUrl
from PyQt6.QtGui import QDesktopServices

from core.cloud_detector import discover_local_cloud_backend, inspect_system_compatibility
from core.cloud_backend import get_site_url


class CloudWizardDialog(QDialog):
    """Interactive wizard to guide users through choosing setup mode, deploying, and connecting Convex cloud saves."""

    test_completed = pyqtSignal(bool, str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Cloud Save Setup Wizard")
        self.resize(620, 520)
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
            QRadioButton {
                color: #FFFFFF;
                font-size: 13px;
                spacing: 8px;
            }
            QRadioButton::indicator {
                width: 16px;
                height: 16px;
            }
            QScrollArea {
                background: transparent;
                border: none;
            }
        """)

        self.layout = QVBoxLayout(self)
        self.layout.setContentsMargins(24, 24, 24, 24)
        self.layout.setSpacing(16)

        # Preflight system compatibility
        self.compat = inspect_system_compatibility()

        # Header
        self.title_lbl = QLabel("Private Cloud Save Setup")
        self.title_lbl.setStyleSheet("font-size: 18px; font-weight: bold; color: #FFFFFF;")
        self.layout.addWidget(self.title_lbl)

        self.subtitle_lbl = QLabel("Choose your setup mode")
        self.subtitle_lbl.setStyleSheet("color: #9CA3AF; font-size: 13px;")
        self.layout.addWidget(self.subtitle_lbl)

        # Stacked Pages
        self.pages = QStackedWidget()
        self.pages.addWidget(self._create_mode_page())      # Page 0: Mode selection
        self.pages.addWidget(self._create_deploy_page())    # Page 1: Deploy backend
        self.pages.addWidget(self._create_connect_page())   # Page 2: Connect URL & Key
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

    def _create_mode_page(self) -> QWidget:
        widget = QWidget()
        layout = QVBoxLayout(widget)
        layout.setContentsMargins(0, 8, 0, 0)
        layout.setSpacing(14)

        # Preflight Host Compatibility Diagnostic Banner
        banner_box = QFrame()
        bb_layout = QHBoxLayout(banner_box)
        bb_layout.setContentsMargins(12, 10, 12, 10)

        if self.compat["can_deploy_locally"]:
            banner_box.setStyleSheet("""
                QFrame {
                    background-color: rgba(16, 185, 129, 0.12);
                    border: 1px solid #10B981;
                    border-radius: 8px;
                }
            """)
            lbl_diag = QLabel(f"🟢 <b>Host Environment Ready:</b> Node.js ({self.compat['node_version'] or 'detected'}) & npm available for local deployment.")
            lbl_diag.setStyleSheet("color: #6EE7B7; font-size: 12px;")
        elif self.compat["is_steamos"] or self.compat["is_immutable"]:
            banner_box.setStyleSheet("""
                QFrame {
                    background-color: rgba(245, 158, 11, 0.12);
                    border: 1px solid #F59E0B;
                    border-radius: 8px;
                }
            """)
            lbl_diag = QLabel("⚠️ <b>Steam Deck / Immutable OS:</b> Rootfs is read-only. 1-Click web deployment or Connect mode is recommended.")
            lbl_diag.setStyleSheet("color: #FCD34D; font-size: 12px;")
            lbl_diag.setWordWrap(True)
        else:
            banner_box.setStyleSheet("""
                QFrame {
                    background-color: rgba(59, 130, 246, 0.12);
                    border: 1px solid #3B82F6;
                    border-radius: 8px;
                }
            """)
            lbl_diag = QLabel("ℹ️ <b>Host Notice:</b> Node.js & npm not detected. 1-Click web deployment or Connect mode is recommended.")
            lbl_diag.setStyleSheet("color: #93C5FD; font-size: 12px;")
            lbl_diag.setWordWrap(True)

        bb_layout.addWidget(lbl_diag)
        layout.addWidget(banner_box)

        intro = QLabel("Choose how you would like to set up cloud synchronization on this device:")
        intro.setStyleSheet("color: #EDEDED; font-size: 13px;")
        layout.addWidget(intro)

        self.mode_group = QButtonGroup(self)

        # Option 1: Connect to existing cloud database (Secondary device / Already deployed)
        frame_connect = QFrame()
        frame_connect.setObjectName("optionBox")
        frame_connect.setStyleSheet("""
            QFrame#optionBox {
                background-color: #18181B;
                border: 1px solid #27272A;
                border-radius: 8px;
                padding: 14px;
            }
            QFrame#optionBox:hover {
                border: 1px solid #3B82F6;
            }
        """)
        f1_layout = QVBoxLayout(frame_connect)
        f1_layout.setContentsMargins(4, 4, 4, 4)
        f1_layout.setSpacing(6)

        self.radio_connect = QRadioButton("🔗 Connect to an already created cloud database")
        self.radio_connect.setStyleSheet("font-weight: bold; font-size: 14px; color: #38BDF8;")
        self.radio_connect.setChecked(True)
        self.mode_group.addButton(self.radio_connect, 0)
        f1_layout.addWidget(self.radio_connect)

        desc1 = QLabel(
            "<b>Recommended for secondary devices</b> (such as a laptop, Steam Deck, or another PC).<br>"
            "You do <b>not</b> need Node.js, npm, git, or the SafeLauncherCloud server files at all! "
            "Simply enter your <code>.convex.site</code> URL (and optional Secret Key) and SafeLauncher will start syncing your AES-256-GCM encrypted saves immediately."
        )
        desc1.setWordWrap(True)
        desc1.setStyleSheet("color: #A1A1AA; font-size: 12px; margin-left: 24px;")
        f1_layout.addWidget(desc1)
        layout.addWidget(frame_connect)

        # Option 2: Set up a new private cloud database from scratch
        frame_new = QFrame()
        frame_new.setObjectName("optionBox")
        frame_new.setStyleSheet("""
            QFrame#optionBox {
                background-color: #18181B;
                border: 1px solid #27272A;
                border-radius: 8px;
                padding: 14px;
            }
            QFrame#optionBox:hover {
                border: 1px solid #3B82F6;
            }
        """)
        f2_layout = QVBoxLayout(frame_new)
        f2_layout.setContentsMargins(4, 4, 4, 4)
        f2_layout.setSpacing(6)

        self.radio_new = QRadioButton("🚀 Set up a new private cloud database from scratch")
        self.radio_new.setStyleSheet("font-weight: bold; font-size: 14px; color: #10B981;")
        self.mode_group.addButton(self.radio_new, 1)
        f2_layout.addWidget(self.radio_new)

        desc2 = QLabel(
            "<b>First-time setup</b>: Deploy a brand new private Convex cloud backend "
            "(1 GB free cloud storage without monthly fees). Supports 1-click web deployment or local automated CLI setup."
        )
        desc2.setWordWrap(True)
        desc2.setStyleSheet("color: #A1A1AA; font-size: 12px; margin-left: 24px;")
        f2_layout.addWidget(desc2)
        layout.addWidget(frame_new)

        layout.addStretch()
        return widget

    def _create_deploy_page(self) -> QWidget:
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)

        content = QWidget()
        layout = QVBoxLayout(content)
        layout.setContentsMargins(0, 4, 8, 8)
        layout.setSpacing(12)

        # Section 1: Zero-CLI 1-Click Web Deployment
        web_card = QFrame()
        web_card.setStyleSheet("""
            QFrame {
                background-color: #18181B;
                border: 1px solid #2563EB;
                border-radius: 8px;
                padding: 12px;
            }
        """)
        wc_layout = QVBoxLayout(web_card)
        wc_layout.setContentsMargins(6, 6, 6, 6)
        wc_layout.setSpacing(8)

        wc_title = QLabel("🌐 <b>1-Click Cloud Deploy (Recommended for Steam Deck & Zero-Terminal Users)</b>")
        wc_title.setStyleSheet("color: #60A5FA; font-size: 13px;")
        wc_layout.addWidget(wc_title)

        wc_desc = QLabel(
            "Deploy your personal Convex database directly in your browser with zero CLI setup.<br>"
            "• 100% Free · 1 GB Storage · No credit card required · Zero Node.js or terminal required on this machine."
        )
        wc_desc.setStyleSheet("color: #D1D5DB; font-size: 12px;")
        wc_desc.setWordWrap(True)
        wc_layout.addWidget(wc_desc)

        btn_web_deploy = QPushButton("Deploy on Convex.dev (Free) ↗")
        btn_web_deploy.setObjectName("primaryBtn")
        btn_web_deploy.setCursor(Qt.CursorShape.PointingHandCursor)
        btn_web_deploy.clicked.connect(self._open_web_deploy)
        wc_layout.addWidget(btn_web_deploy)
        layout.addWidget(web_card)

        # Section 2: Automated Terminal Deployment (if Node.js available)
        desc_cli = QLabel(
            "<b>Option B: Automated or Manual Terminal Deployment</b><br>"
            "If you have Node.js and npm installed, SafeLauncher can launch an automated setup terminal, "
            "or you can run manual commands:"
        )
        desc_cli.setWordWrap(True)
        desc_cli.setStyleSheet("color: #D1D5DB; font-size: 12px; margin-top: 4px;")
        layout.addWidget(desc_cli)

        btn_auto = QPushButton("🚀 Launch Automated Setup Terminal…")
        btn_auto.setStyleSheet("background: #0284C7; font-weight: bold; padding: 10px;")
        btn_auto.clicked.connect(self._launch_automated_terminal)
        layout.addWidget(btn_auto)

        cmd_box = QLabel(
            "# 1. Deploy Convex backend (Node.js required):\n"
            "git clone https://github.com/Mistarin/SafeLauncherCloud.git\n"
            "cd SafeLauncherCloud && npm install && npx convex deploy\n\n"
            "# 2. (Recommended) Set a secret key to lock your storage:\n"
            "npx convex env set SAFELAUNCHER_SECRET_KEY \"your-secret-passphrase\""
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

        btn_copy = QPushButton("Copy Manual Commands to Clipboard")
        btn_copy.clicked.connect(lambda: self._copy_commands(cmd_box.text()))
        layout.addWidget(btn_copy)

        # Section 3: Steam Deck NVM Fallback Instructions
        nvm_card = QFrame()
        nvm_card.setStyleSheet("""
            QFrame {
                background-color: #18181B;
                border: 1px solid #3F3F46;
                border-radius: 6px;
                padding: 10px;
            }
        """)
        nc_layout = QVBoxLayout(nvm_card)
        nc_layout.setContentsMargins(6, 6, 6, 6)
        nc_layout.setSpacing(6)

        nc_title = QLabel("🎮 <b>Steam Deck / Immutable OS NVM Fallback</b>")
        nc_title.setStyleSheet("color: #FBBF24; font-size: 12px;")
        nc_layout.addWidget(nc_title)

        nc_desc = QLabel(
            "To use the CLI on Steam Deck without modifying the read-only partition, "
            "install Node.js into your user profile via NVM:"
        )
        nc_desc.setStyleSheet("color: #9CA3AF; font-size: 11px;")
        nc_desc.setWordWrap(True)
        nc_layout.addWidget(nc_desc)

        nvm_cmd = "curl -o- https://raw.githubusercontent.com/nvm-sh/nvm/v0.39.7/install.sh | bash && source ~/.bashrc && nvm install 20"
        lbl_nvm = QLabel(nvm_cmd)
        lbl_nvm.setStyleSheet("""
            background-color: #121214;
            border: 1px solid #27272A;
            border-radius: 4px;
            padding: 8px;
            font-family: monospace;
            font-size: 11px;
            color: #34D399;
        """)
        lbl_nvm.setWordWrap(True)
        nc_layout.addWidget(lbl_nvm)

        btn_copy_nvm = QPushButton("Copy NVM Install Command")
        btn_copy_nvm.clicked.connect(lambda: self._copy_commands(nvm_cmd))
        nc_layout.addWidget(btn_copy_nvm)
        layout.addWidget(nvm_card)

        hint = QLabel("💡 After deploying, click 'Next' to enter your Convex Site URL.")
        hint.setStyleSheet("color: #FBBF24; font-size: 12px;")
        hint.setWordWrap(True)
        layout.addWidget(hint)

        scroll.setWidget(content)
        return scroll

    def _open_web_deploy(self):
        """Open browser to deploy SafeLauncherCloud repository on Convex."""
        QDesktopServices.openUrl(QUrl("https://github.com/Mistarin/SafeLauncherCloud"))

    def _launch_automated_terminal(self):
        import shutil
        import subprocess
        import sys
        main_py = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "main.py"))
        cmd_args = [sys.executable, main_py, "--setup-cloud"]
        terms = [
            ["ptyxis", "--", *cmd_args],
            ["gnome-terminal", "--", *cmd_args],
            ["konsole", "-e", *cmd_args],
            ["xfce4-terminal", "-e", " ".join(cmd_args)],
            ["kitty", *cmd_args],
            ["alacritty", "-e", *cmd_args],
            ["foot", *cmd_args],
            ["wezterm", "start", "--", *cmd_args],
            ["x-terminal-emulator", "-e", *cmd_args],
            ["xterm", "-e", *cmd_args],
        ]
        launched = False
        for term in terms:
            if shutil.which(term[0]):
                try:
                    subprocess.Popen(term)
                    launched = True
                    break
                except Exception:
                    pass
        if not launched:
            QMessageBox.information(
                self, "Terminal Setup",
                f"Could not automatically detect terminal emulator.\nPlease run in your terminal:\n\n{sys.executable} {main_py} --setup-cloud"
            )

    def _create_connect_page(self) -> QWidget:
        widget = QWidget()
        layout = QVBoxLayout(widget)
        layout.setContentsMargins(0, 8, 0, 0)
        layout.setSpacing(12)

        info_lbl = QLabel(
            "Enter your Convex backend details to start syncing saves encrypted with client-side AES-256-GCM:"
        )
        info_lbl.setWordWrap(True)
        layout.addWidget(info_lbl)

        layout.addWidget(QLabel("<b>Convex Site URL:</b>"))

        settings = QSettings("SafeLauncher", "SafeLauncher")
        existing_url = settings.value("convex_site_url", "", type=str) or get_site_url()

        self.edit_url = QLineEdit(existing_url)
        self.edit_url.setPlaceholderText("https://your-project.convex.site")
        layout.addWidget(self.edit_url)

        # Secret Access Key explanation & field
        secret_box = QFrame()
        secret_box.setStyleSheet("""
            QFrame {
                background-color: #18181B;
                border: 1px solid #27272A;
                border-left: 3px solid #F59E0B;
                border-radius: 6px;
                padding: 8px 10px;
            }
        """)
        sb_layout = QVBoxLayout(secret_box)
        sb_layout.setContentsMargins(4, 4, 4, 4)
        sb_layout.setSpacing(4)

        sb_title = QLabel("🔐 <b>Secret Access Key</b> (Recommended)")
        sb_title.setStyleSheet("color: #FBBF24; font-size: 13px;")
        sb_layout.addWidget(sb_title)

        sb_desc = QLabel(
            "Acts as a private password for your server endpoint. It stops anyone else on the internet "
            "who discovers your public <code>.convex.site</code> URL from uploading files and filling up your 1 GB storage quota.<br>"
            "<span style='color: #9CA3AF; font-size: 11px;'>• If you set <code>SAFELAUNCHER_SECRET_KEY</code> on your backend, enter it below.<br>"
            "• If you did not set a secret key on your server, you can leave this blank.</span>"
        )
        sb_desc.setWordWrap(True)
        sb_desc.setStyleSheet("color: #D1D5DB; font-size: 12px;")
        sb_layout.addWidget(sb_desc)
        layout.addWidget(secret_box)

        key_row = QHBoxLayout()
        existing_key = settings.value("cloud_secret_key", "", type=str)
        self.edit_key = QLineEdit(existing_key)
        self.edit_key.setEchoMode(QLineEdit.EchoMode.Password)
        self.edit_key.setPlaceholderText("Enter your secret key (or leave blank if none)")
        key_row.addWidget(self.edit_key, 1)

        self.btn_toggle_key = QPushButton("👁")
        self.btn_toggle_key.setFixedWidth(38)
        self.btn_toggle_key.setToolTip("Show / Hide Secret Key")
        self.btn_toggle_key.clicked.connect(self._toggle_key_visibility)
        key_row.addWidget(self.btn_toggle_key)
        layout.addLayout(key_row)

        self.status_lbl = QLabel("")
        self.status_lbl.setWordWrap(True)
        layout.addWidget(self.status_lbl)

        layout.addStretch()
        return widget

    def _toggle_key_visibility(self):
        if self.edit_key.echoMode() == QLineEdit.EchoMode.Password:
            self.edit_key.setEchoMode(QLineEdit.EchoMode.Normal)
            self.btn_toggle_key.setText("🙈")
        else:
            self.edit_key.setEchoMode(QLineEdit.EchoMode.Password)
            self.btn_toggle_key.setText("👁")

    def _copy_commands(self, text: str):
        clipboard = QApplication.clipboard()
        if clipboard:
            clipboard.setText(text)
            QMessageBox.information(self, "Copied", "Commands copied to clipboard!")

    def _go_back(self):
        cur = self.pages.currentIndex()
        if cur == 1:
            self.pages.setCurrentIndex(0)
            self.subtitle_lbl.setText("Choose your setup mode")
            self.btn_back.setEnabled(False)
            self.btn_next.setText("Next")
        elif cur == 2:
            if self.radio_new.isChecked():
                self.pages.setCurrentIndex(1)
                self.subtitle_lbl.setText("Deploy your free Convex backend")
                self.btn_back.setEnabled(True)
                self.btn_next.setText("Next")
            else:
                self.pages.setCurrentIndex(0)
                self.subtitle_lbl.setText("Choose your setup mode")
                self.btn_back.setEnabled(False)
                self.btn_next.setText("Next")

    def _go_next(self):
        cur = self.pages.currentIndex()
        if cur == 0:
            if self.radio_new.isChecked():
                self.pages.setCurrentIndex(1)
                self.subtitle_lbl.setText("Deploy your free Convex backend")
                self.btn_back.setEnabled(True)
                self.btn_next.setText("Next")
            else:
                self.pages.setCurrentIndex(2)
                self.subtitle_lbl.setText("Connect to your cloud database")
                self.btn_back.setEnabled(True)
                self.btn_next.setText("Test & Connect")
        elif cur == 1:
            self.pages.setCurrentIndex(2)
            self.subtitle_lbl.setText("Connect to your cloud database")
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
