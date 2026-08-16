"""Focused maintenance dialogs; orchestration stays in MainWindow."""

import os
from PyQt6.QtWidgets import QDialog, QVBoxLayout, QHBoxLayout, QPushButton, QLabel, QTableWidget, QTableWidgetItem, QMessageBox, QFileDialog
from PyQt6.QtCore import QUrl
from PyQt6.QtGui import QDesktopServices
from core.runtime_inventory import RuntimeInventory
from core.prefix_manager import PrefixManager


class RuntimeInventoryDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Proton / Runtime Inventory")
        self.setMinimumSize(700, 380)
        self.resize(900, 460)
        self.setSizeGripEnabled(True)
        self.inventory = RuntimeInventory()
        layout = QVBoxLayout(self)
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


class PrefixMaintenanceDialog(QDialog):
    def __init__(self, game_path: str, parent=None):
        super().__init__(parent)
        self.game_path = game_path
        self.manager = PrefixManager()
        self.setWindowTitle("Prefix Maintenance")
        self.resize(620, 440)
        layout = QVBoxLayout(self)
        self.summary = QLabel()
        self.summary.setWordWrap(True)
        layout.addWidget(self.summary)
        buttons = QHBoxLayout()
        for label, callback in (("Repair / reset prefix", self.reset), ("Backup prefix", self.backup), ("Restore prefix", self.restore), ("Migrate prefix", self.migrate), ("Clear shader cache", self.clear_cache), ("Open prefix folder", self.open_folder)):
            button = QPushButton(label)
            button.clicked.connect(callback)
            buttons.addWidget(button)
        layout.addLayout(buttons)
        close = QPushButton("Close")
        close.clicked.connect(self.accept)
        layout.addWidget(close)
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
