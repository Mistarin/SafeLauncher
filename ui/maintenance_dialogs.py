"""Focused maintenance dialogs; orchestration stays in MainWindow."""

import os
from PyQt6.QtWidgets import (
    QVBoxLayout, QHBoxLayout, QGridLayout, QPushButton, QLabel, QFrame,
    QTableWidget, QTableWidgetItem, QMessageBox, QFileDialog, QSizePolicy,
)
from PyQt6.QtCore import QUrl, Qt
from PyQt6.QtGui import QDesktopServices
from core.runtime_inventory import RuntimeInventory
from core.prefix_manager import PrefixManager
from ui.components.popup_shell import PopupDialog


class RuntimeInventoryDialog(PopupDialog):
    def __init__(self, parent=None):
        super().__init__("Proton / Runtime Inventory", parent)
        self.setMinimumSize(700, 380)
        self.resize(900, 460)
        self.setSizeGripEnabled(True)
        self.inventory = RuntimeInventory()
        layout = self.popup_layout()
        layout.addWidget(QLabel("System Proton, GE-Proton, UMU-Proton and Steam Runtime installations"))
        self.table = QTableWidget(0, 7)
        self.table.setHorizontalHeaderLabels(["Name", "Type", "Architecture", "Status", "Version", "Disk", "Path"])
        self.table.setSelectionBehavior(QTableWidget.SelectionBehavior.SelectRows)
        layout.addWidget(self.table)
        buttons = QHBoxLayout()
        refresh = QPushButton("Refresh")
        refresh.clicked.connect(self.refresh)
        buttons.addWidget(refresh)
        verify = QPushButton("Verify installation")
        verify.clicked.connect(self.verify)
        buttons.addWidget(verify)
        remove = QPushButton("Remove runtime")
        remove.clicked.connect(self.remove)
        buttons.addWidget(remove)
        buttons.addStretch()
        close = QPushButton("Close")
        close.clicked.connect(self.accept)
        buttons.addWidget(close)
        layout.addLayout(buttons)
        self.refresh()

    def refresh(self):
        self.records = self.inventory.scan()
        self.table.setRowCount(len(self.records))
        for row, record in enumerate(self.records):
            for col, value in enumerate((record.name, record.kind, record.architecture, record.status, record.version, record.size_text, record.path)):
                self.table.setItem(row, col, QTableWidgetItem(str(value)))
        self.table.resizeColumnsToContents()

    def _selected(self):
        rows = self.table.selectionModel().selectedRows()
        return self.records[rows[0].row()] if rows else None

    def verify(self):
        record = self._selected()
        if record:
            ok, message = self.inventory.verify(record.path)
            QMessageBox.information(self, "Runtime verification", ("Passed: " if ok else "Failed: ") + message)

    def remove(self):
        record = self._selected()
        if not record:
            return
        if QMessageBox.question(self, "Remove runtime", f"Remove {record.name}?") != QMessageBox.StandardButton.Yes:
            return
        try:
            self.inventory.remove(record.path)
            self.refresh()
        except Exception as error:
            QMessageBox.critical(self, "Cannot remove runtime", str(error))


class PrefixMaintenanceDialog(PopupDialog):
    def __init__(self, game_path: str, parent=None):
        super().__init__("Prefix Maintenance", parent)
        self.game_path = game_path
        self.manager = PrefixManager()
        self.setMinimumSize(680, 430)
        self.resize(760, 500)
        layout = self.popup_layout(margins=(20, 16, 20, 16), spacing=12)

        summary_card = QFrame()
        summary_card.setObjectName("maintenanceSummary")
        summary_layout = QVBoxLayout(summary_card)
        summary_layout.setContentsMargins(16, 14, 16, 14)
        summary_layout.setSpacing(6)
        summary_title = QLabel("Prefix status")
        summary_title.setStyleSheet("font-size: 13px; font-weight: 700; color: #F4F4F5;")
        summary_layout.addWidget(summary_title)
        self.summary = QLabel()
        self.summary.setObjectName("propertyValue")
        self.summary.setWordWrap(True)
        self.summary.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        summary_layout.addWidget(self.summary)
        layout.addWidget(summary_card)

        action_card = QFrame()
        action_card.setObjectName("maintenanceActions")
        action_layout = QVBoxLayout(action_card)
        action_layout.setContentsMargins(12, 12, 12, 12)
        action_layout.setSpacing(8)
        action_title = QLabel("Maintenance actions")
        action_title.setStyleSheet("font-size: 13px; font-weight: 700; color: #F4F4F5;")
        action_layout.addWidget(action_title)
        buttons = QGridLayout()
        buttons.setContentsMargins(0, 0, 0, 0)
        buttons.setHorizontalSpacing(8)
        buttons.setVerticalSpacing(8)
        buttons.setColumnStretch(0, 1)
        buttons.setColumnStretch(1, 1)
        buttons.setColumnStretch(2, 1)
        actions = (
            ("Repair / reset prefix", self.reset),
            ("Backup prefix", self.backup),
            ("Restore prefix", self.restore),
            ("Migrate prefix", self.migrate),
            ("Clear shader cache", self.clear_cache),
            ("Open prefix folder", self.open_folder),
        )
        for index, (label, callback) in enumerate(actions):
            button = QPushButton(label)
            button.setObjectName("maintenanceAction")
            button.setFixedHeight(36)
            button.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
            button.clicked.connect(callback)
            buttons.addWidget(button, index // 3, index % 3)
        action_layout.addLayout(buttons)
        layout.addWidget(action_card)

        footer = QHBoxLayout()
        footer.setContentsMargins(0, 0, 0, 0)
        footer.addStretch(1)
        close = QPushButton("Close")
        close.setObjectName("maintenanceClose")
        close.setFixedHeight(34)
        close.clicked.connect(self.accept)
        footer.addWidget(close)
        footer.addStretch(1)
        layout.addLayout(footer)
        self.refresh()

    def refresh(self):
        info = self.manager.inspect(self.game_path)
        warnings = "\n".join(f"- {warning}" for warning in info.warnings) or "No symlink or structural warnings detected."
        self.summary.setText(f"Prefix: {info.path}\nSize: {info.size_bytes / (1024 * 1024):.1f} MB\nHealth: {'healthy' if info.healthy else 'needs attention'}\nUsers: {info.user_count}\n\n{warnings}")

    def reset(self):
        if QMessageBox.warning(self, "Reset prefix", "This removes the entire Wine prefix. Game files remain, but installed dependencies and settings are lost.", QMessageBox.StandardButton.Cancel | QMessageBox.StandardButton.Yes) != QMessageBox.StandardButton.Yes:
            return
        self.manager.reset(self.game_path)
        self.refresh()

    def backup(self):
        target, _ = QFileDialog.getSaveFileName(self, "Save prefix backup", os.path.join(self.game_path, "prefix-backup.tar.gz"), "Tar gzip (*.tar.gz)")
        if target:
            try:
                self.manager.backup(self.game_path, target)
                QMessageBox.information(self, "Prefix backup", "Prefix backup created.")
            except Exception as error:
                QMessageBox.critical(self, "Backup failed", str(error))

    def restore(self):
        source, _ = QFileDialog.getOpenFileName(self, "Restore prefix backup", "", "Tar archives (*.tar.gz *.tar)")
        if source:
            try:
                self.manager.restore(self.game_path, source)
                self.refresh()
            except Exception as error:
                QMessageBox.critical(self, "Restore failed", str(error))

    def clear_cache(self):
        removed = self.manager.clear_shader_cache(self.game_path)
        QMessageBox.information(self, "Shader cache", f"Removed {removed} cached item(s).")
        self.refresh()

    def migrate(self):
        destination = QFileDialog.getExistingDirectory(self, "Select destination game directory", os.path.dirname(self.game_path))
        if not destination:
            return
        try:
            self.manager.migrate(self.game_path, destination)
            QMessageBox.information(self, "Prefix migration", "Prefix copied successfully. The original prefix was kept as a rollback copy.")
        except Exception as error:
            QMessageBox.critical(self, "Migration failed", str(error))

    def open_folder(self):
        path = os.path.join(self.game_path, "prefix")
        os.makedirs(path, exist_ok=True)
        QDesktopServices.openUrl(QUrl.fromLocalFile(path))
