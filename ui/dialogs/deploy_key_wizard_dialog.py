"""Guided setup for the Convex deployment-scoped key used by backend updates."""

from __future__ import annotations

import os
import platform
from pathlib import Path

from PyQt6.QtCore import Qt, pyqtSignal
from PyQt6.QtGui import QDesktopServices
from PyQt6.QtWidgets import (
    QButtonGroup, QFileDialog, QHBoxLayout, QLabel, QLineEdit, QMessageBox,
    QPushButton, QRadioButton, QStackedWidget, QVBoxLayout, QWidget,
)
from PyQt6.QtCore import QUrl

from core.cloud_cli_wizard import (
    deploy_key_prerequisites,
    generate_deploy_key,
    validate_deploy_key,
)
from core.cloud_detector import detect_local_cloud_installation
from core.safe_thread import TaskSupervisor
from core.secret_store import set_secret, delete_secret
from ui.components.popup_shell import PopupDialog


class DeployKeyWizardDialog(PopupDialog):
    """Step-by-step deploy-key setup with automatic and manual paths."""

    key_saved = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__("Convex Deploy Key Setup", parent)
        self.setMinimumSize(700, 560)
        self.resize(760, 620)
        self._tasks = TaskSupervisor(self)
        self._pending_key = ""
        self._busy = False
        self._backend_path = ""
        discovered = detect_local_cloud_installation()
        if discovered:
            self._backend_path = str(discovered.get("path") or "")

        root = self.popup_layout(margins=(24, 20, 24, 20), spacing=14)
        heading = QLabel("Set up automatic backend updates")
        heading.setStyleSheet("font-size:18px; font-weight:800; color:#FFFFFF;")
        root.addWidget(heading)
        self.subtitle = QLabel("A deploy key lets SafeLauncher update your Convex functions without asking you to log in on every device.")
        self.subtitle.setWordWrap(True)
        self.subtitle.setStyleSheet("color:#A1A1AA; font-size:12px;")
        root.addWidget(self.subtitle)

        self.pages = QStackedWidget()
        self.pages.addWidget(self._intro_page())
        self.pages.addWidget(self._automatic_page())
        self.pages.addWidget(self._manual_page())
        self.pages.addWidget(self._finish_page())
        root.addWidget(self.pages, 1)

        buttons = QHBoxLayout()
        self.btn_back = QPushButton("Back")
        self.btn_back.clicked.connect(self._back)
        self.btn_back.setEnabled(False)
        buttons.addWidget(self.btn_back)
        buttons.addStretch()
        self.btn_cancel = QPushButton("Cancel")
        self.btn_cancel.clicked.connect(self.reject)
        buttons.addWidget(self.btn_cancel)
        self.btn_next = QPushButton("Next")
        self.btn_next.setObjectName("primaryBtn")
        self.btn_next.clicked.connect(self._next)
        buttons.addWidget(self.btn_next)
        root.addLayout(buttons)

    def _body_label(self, text: str) -> QLabel:
        label = QLabel(text)
        label.setWordWrap(True)
        label.setTextFormat(Qt.TextFormat.RichText)
        label.setStyleSheet("color:#D1D5DB; font-size:12px;")
        return label

    def _intro_page(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.addWidget(self._body_label(
            "<b>Important:</b> this is a Convex deployment credential, not the SafeLauncher API Secret Key. "
            "It authorizes backend code deployment only. It does not read your save files or replace your cloud API secret."
        ))
        layout.addWidget(self._body_label(
            "For this feature, create a production-scoped key with only the <b>deployment:deploy</b> permission. "
            "You can revoke it later from Convex Dashboard → Deployment Settings → Deploy keys."
        ))
        self.radio_auto = QRadioButton("Generate automatically with the authenticated Convex CLI")
        self.radio_auto.setChecked(True)
        self.radio_manual = QRadioButton("Enter an existing deploy key manually")
        group = QButtonGroup(self)
        group.addButton(self.radio_auto)
        group.addButton(self.radio_manual)
        layout.addWidget(self.radio_auto)
        layout.addWidget(self._body_label("Uses the local Convex login and linked SafeLauncherCloud project. The generated key is never shown in terminal output."))
        layout.addWidget(self.radio_manual)
        layout.addWidget(self._body_label(
            "Open the deployment settings page, generate a key, enable only deployment:deploy, and paste it on the next page."
        ))
        layout.addStretch()
        return page

    def _automatic_page(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.addWidget(self._body_label(
            "SafeLauncher will run <code>npx convex deployment token create … --prod</code> in the selected backend project. "
            "Convex CLI authentication must already be available on this machine."
        ))
        path_row = QHBoxLayout()
        self.backend_path_edit = QLineEdit(self._backend_path)
        self.backend_path_edit.setPlaceholderText("Path to SafeLauncherCloud checkout")
        path_row.addWidget(self.backend_path_edit, 1)
        browse = QPushButton("Browse…")
        browse.clicked.connect(self._browse_backend)
        path_row.addWidget(browse)
        layout.addWidget(QLabel("SafeLauncherCloud project:"))
        layout.addLayout(path_row)
        layout.addWidget(QLabel("Deploy-key name:"))
        self.key_name_edit = QLineEdit(f"safelauncher-desktop-{platform.node() or 'device'}")
        self.key_name_edit.setPlaceholderText("safelauncher-desktop-main-pc")
        layout.addWidget(self.key_name_edit)
        self.auto_status = QLabel("")
        self.auto_status.setWordWrap(True)
        layout.addWidget(self.auto_status)
        layout.addStretch()
        return page

    def _manual_page(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        layout.addWidget(self._body_label(
            "<b>Dashboard steps</b><br>"
            "1. Open <a href='https://dashboard.convex.dev'>dashboard.convex.dev</a> and select the deployment.<br>"
            "2. Open Deployment Settings → Deploy keys → Generate a deploy key.<br>"
            "3. Name it, select production, enable <b>deployment:deploy</b>, and generate it.<br>"
            "4. Copy the key once and paste it below. Convex may not show it again."
        ))
        self.manual_key_edit = QLineEdit()
        self.manual_key_edit.setEchoMode(QLineEdit.EchoMode.Password)
        self.manual_key_edit.setPlaceholderText("Paste the Convex deploy key")
        layout.addWidget(self.manual_key_edit)
        toggle = QPushButton("Show")
        toggle.clicked.connect(lambda: self._toggle_key(toggle))
        layout.addWidget(toggle, alignment=Qt.AlignmentFlag.AlignLeft)
        open_dashboard = QPushButton("Open Convex Dashboard ↗")
        open_dashboard.clicked.connect(lambda: QDesktopServices.openUrl(QUrl("https://dashboard.convex.dev")))
        layout.addWidget(open_dashboard, alignment=Qt.AlignmentFlag.AlignLeft)
        layout.addWidget(self._body_label(
            "The key is stored locally in the operating system secret service when available. "
            "It is used only for future backend deployment commands."
        ))
        layout.addStretch()
        return page

    def _finish_page(self) -> QWidget:
        page = QWidget()
        layout = QVBoxLayout(page)
        self.finish_status = QLabel("Ready to securely save the deploy key.")
        self.finish_status.setWordWrap(True)
        layout.addWidget(self.finish_status)
        layout.addWidget(self._body_label(
            "SafeLauncher will first perform a non-destructive Convex CLI dry-run when the project directory is available. "
            "If the CLI is unavailable, the key can still be saved and verified during the next backend update."
        ))
        layout.addStretch()
        return page

    def _toggle_key(self, button: QPushButton) -> None:
        hidden = self.manual_key_edit.echoMode() == QLineEdit.EchoMode.Password
        self.manual_key_edit.setEchoMode(QLineEdit.EchoMode.Normal if hidden else QLineEdit.EchoMode.Password)
        button.setText("Hide" if hidden else "Show")

    def _browse_backend(self) -> None:
        path = QFileDialog.getExistingDirectory(self, "Select SafeLauncherCloud project", self.backend_path_edit.text() or os.path.expanduser("~"))
        if path:
            self.backend_path_edit.setText(path)

    def _next(self) -> None:
        index = self.pages.currentIndex()
        if index == 0:
            target = 1 if self.radio_auto.isChecked() else 2
            self.pages.setCurrentIndex(target)
            self.btn_back.setEnabled(True)
            self.btn_next.setText("Generate Key" if target == 1 else "Continue")
        elif index == 1:
            self._generate()
        elif index == 2:
            self._prepare_manual_key()
        else:
            self._verify_and_save()

    def _back(self) -> None:
        index = self.pages.currentIndex()
        if index == 3:
            self.pages.setCurrentIndex(1 if self.radio_auto.isChecked() else 2)
            self.btn_next.setText("Generate Key" if self.radio_auto.isChecked() else "Continue")
        elif index in (1, 2):
            self.pages.setCurrentIndex(0)
            self.btn_back.setEnabled(False)
            self.btn_next.setText("Next")

    def _generate(self) -> None:
        backend_path = Path(self.backend_path_edit.text().strip()).expanduser()
        name = self.key_name_edit.text().strip()
        prereq = deploy_key_prerequisites(backend_path)
        if not prereq["has_npx"]:
            self.auto_status.setText("<font color='#F87171'>npx was not found. Use the manual dashboard path or install Node.js first.</font>")
            return
        if not prereq["has_backend"]:
            self.auto_status.setText("<font color='#F87171'>Select the SafeLauncherCloud project directory.</font>")
            return
        if not prereq["has_cli_login"]:
            self.auto_status.setText("<font color='#FBBF24'>Convex CLI login/project configuration was not detected. Sign in and link the project, or use manual entry.</font>")
            return
        self._busy = True
        self.btn_next.setEnabled(False)
        self.btn_back.setEnabled(False)
        self.auto_status.setText("Generating a production-scoped deploy key…")

        def work():
            return generate_deploy_key(backend_path, name)

        self._tasks.start("SafeLauncher-GenerateDeployKey", work, self._on_generated)

    def _on_generated(self, result: dict) -> None:
        self._busy = False
        self.btn_next.setEnabled(True)
        self.btn_back.setEnabled(True)
        if not result.get("ok"):
            self.auto_status.setText(f"<font color='#F87171'>{result.get('error', 'Deploy-key generation failed.')}</font>")
            return
        self._pending_key = str(result.get("key", ""))
        if not set_secret("convex_deploy_key", self._pending_key):
            self._pending_key = ""
            self.auto_status.setText(
                "<font color='#F87171'>The key was generated, but SafeLauncher could not securely store it. "
                "It may still be active in Convex; revoke it from the dashboard before retrying.</font>"
            )
            return
        self._backend_path = self.backend_path_edit.text().strip()
        self.pages.setCurrentIndex(3)
        self.btn_next.setText("Verify & Finish")
        self.finish_status.setText("<font color='#34D399'>Deploy key generated and stored securely. Continue to verify it with a non-destructive dry run.</font>")

    def _prepare_manual_key(self) -> None:
        key = self.manual_key_edit.text().strip()
        if not key or any(ch.isspace() for ch in key):
            QMessageBox.warning(self, "Deploy Key", "Paste a non-empty Convex deploy key without whitespace.")
            return
        syntax = validate_deploy_key(key)
        if not syntax.get("ok"):
            QMessageBox.warning(
                self,
                "Deploy Key",
                str(syntax.get("error", "This does not look like a Convex deployment key.")),
            )
            return
        self._pending_key = key
        discovered = detect_local_cloud_installation()
        self._backend_path = str(discovered.get("path") or "") if discovered else ""
        self.pages.setCurrentIndex(3)
        self.btn_next.setText("Verify & Save")
        self.finish_status.setText("Ready to verify and save the manually entered deploy key.")

    def _verify_and_save(self) -> None:
        if self._busy or not self._pending_key:
            return
        backend = Path(self._backend_path).expanduser() if self._backend_path else None
        if not backend or not backend.is_dir():
            self._store_key("Saved; CLI verification was unavailable on this machine.")
            return
        self._busy = True
        self.btn_next.setEnabled(False)
        self.btn_back.setEnabled(False)
        self.finish_status.setText("Verifying with a non-destructive Convex dry-run…")

        def work():
            return validate_deploy_key(self._pending_key, backend)

        self._tasks.start("SafeLauncher-VerifyDeployKey", work, self._on_verified)

    def _on_verified(self, result: dict) -> None:
        self._busy = False
        self.btn_next.setEnabled(True)
        self.btn_back.setEnabled(True)
        if not result.get("ok"):
            self.finish_status.setText(f"<font color='#F87171'>{result.get('error', 'Deploy-key verification failed.')}</font>")
            return
        self._store_key(str(result.get("message", "Deploy key verified and saved.")))

    def _store_key(self, message: str) -> None:
        if not set_secret("convex_deploy_key", self._pending_key):
            QMessageBox.critical(self, "Deploy Key", "SafeLauncher could not securely persist the deploy key.")
            return
        self.finish_status.setText(f"<font color='#34D399'>{message}</font>")
        self.key_saved.emit()
        self.accept()

    def forget_key(self) -> bool:
        """Forget the local key; this does not revoke it from Convex."""
        return delete_secret("convex_deploy_key")

    def closeEvent(self, event):
        self._tasks.cancel_all(250)
        super().closeEvent(event)
