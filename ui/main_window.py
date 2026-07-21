import os
from PyQt6.QtWidgets import (
    QMainWindow, QWidget, QVBoxLayout, QHBoxLayout, QPushButton,
    QListWidget, QListWidgetItem, QFileDialog, QMessageBox, QDialog,
    QLabel, QLineEdit, QComboBox, QFormLayout
)
from PyQt6.QtCore import Qt
from core.interfaces import ISandboxRunner, IBackupManager
from database import GameDatabase

class AddGameDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Add Game")
        self.setGeometry(100, 100, 400, 200)
        
        layout = QFormLayout()
        
        self.name_input = QLineEdit()
        layout.addRow("Game Name:", self.name_input)
        
        self.path_input = QLineEdit()
        browse_btn = QPushButton("Browse...")
        browse_btn.clicked.connect(self._browse_path)
        path_layout = QHBoxLayout()
        path_layout.addWidget(self.path_input)
        path_layout.addWidget(browse_btn)
        layout.addRow("Game Path:", path_layout)
        
        self.exe_input = QLineEdit()
        layout.addRow("Executable:", self.exe_input)
        
        self.mode_combo = QComboBox()
        self.mode_combo.addItems(["umu", "wine"])
        layout.addRow("Launch Mode:", self.mode_combo)
        
        buttons_layout = QHBoxLayout()
        add_btn = QPushButton("Add")
        cancel_btn = QPushButton("Cancel")
        add_btn.clicked.connect(self.accept)
        cancel_btn.clicked.connect(self.reject)
        buttons_layout.addWidget(add_btn)
        buttons_layout.addWidget(cancel_btn)
        layout.addRow(buttons_layout)
        
        self.setLayout(layout)
    
    def _browse_path(self):
        path = QFileDialog.getExistingDirectory(self, "Select Game Directory")
        if path:
            self.path_input.setText(path)
    
    def get_values(self):
        return (
            self.name_input.text(),
            self.path_input.text(),
            self.exe_input.text(),
            self.mode_combo.currentText()
        )

class MainWindow(QMainWindow):
    def __init__(self, db: GameDatabase, runner: ISandboxRunner, backup: IBackupManager):
        super().__init__()
        self.db = db
        self.runner = runner
        self.backup = backup

        self.setWindowTitle("Game Sandbox Launcher")
        self.resize(700, 500)

        # UI Setup
        central_widget = QWidget()
        main_layout = QVBoxLayout(central_widget)
        
        # Title
        title = QLabel("Game Library")
        title.setStyleSheet("font-size: 16px; font-weight: bold;")
        main_layout.addWidget(title)

        # Game list
        self.game_list = QListWidget()
        self.game_list.itemDoubleClicked.connect(self._on_launch)
        main_layout.addWidget(self.game_list)

        # Buttons layout
        buttons_layout = QHBoxLayout()
        
        self.btn_launch = QPushButton("▶ Launch Selected Game")
        self.btn_launch.clicked.connect(self._on_launch)
        buttons_layout.addWidget(self.btn_launch)
        
        self.btn_add = QPushButton("➕ Add Game")
        self.btn_add.clicked.connect(self._on_add)
        buttons_layout.addWidget(self.btn_add)
        
        self.btn_remove = QPushButton("🗑️ Remove Game")
        self.btn_remove.clicked.connect(self._on_remove)
        buttons_layout.addWidget(self.btn_remove)

        main_layout.addLayout(buttons_layout)
        
        # Export/Import buttons
        save_layout = QHBoxLayout()
        
        self.btn_export = QPushButton("💾 Export Save")
        self.btn_export.clicked.connect(self._on_export)
        save_layout.addWidget(self.btn_export)
        
        self.btn_import = QPushButton("📂 Import Save")
        self.btn_import.clicked.connect(self._on_import)
        save_layout.addWidget(self.btn_import)
        
        main_layout.addLayout(save_layout)
        
        self.setCentralWidget(central_widget)
        self._refresh_library()

    def _refresh_library(self):
        self.game_list.clear()
        self.games = self.db.get_all_games()
        for game in self.games:
            # game = (id, name, path, executable, mode)
            item = QListWidgetItem(f"🎮 {game[1]} ({game[4].upper()})")
            item.setData(Qt.ItemDataRole.UserRole, game[0])
            self.game_list.addItem(item)
        
        if not self.games:
            self.game_list.addItem("(No games in library)")

    def _get_selected_game(self):
        row = self.game_list.currentRow()
        if row >= 0 and row < len(self.games):
            return self.games[row]
        return None

    def _on_launch(self):
        game = self._get_selected_game()
        if not game:
            QMessageBox.warning(self, "Warning", "Please select a game to launch.")
            return
        
        # game = (id, name, path, executable, mode)
        try:
            self.runner.launch(game[2], game[3], game[4])
            QMessageBox.information(self, "Info", f"Launching {game[1]}...")
        except Exception as e:
            QMessageBox.critical(self, "Error", f"Failed to launch game: {str(e)}")
    
    def _on_add(self):
        dialog = AddGameDialog(self)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            name, path, exe, mode = dialog.get_values()
            if not name or not path or not exe:
                QMessageBox.warning(self, "Error", "All fields are required.")
                return
            if not os.path.isdir(path):
                QMessageBox.warning(self, "Error", "Invalid game path.")
                return
            
            self.db.add_game(name, path, exe, mode)
            self._refresh_library()
            QMessageBox.information(self, "Success", f"Game '{name}' added to library.")
    
    def _on_remove(self):
        game = self._get_selected_game()
        if not game:
            QMessageBox.warning(self, "Warning", "Please select a game to remove.")
            return
        
        reply = QMessageBox.question(
            self,
            "Confirm",
            f"Remove '{game[1]}' from library? (Game files won't be deleted.)",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No
        )
        
        if reply == QMessageBox.StandardButton.Yes:
            self.db.remove_game(game[0])
            self._refresh_library()
            QMessageBox.information(self, "Success", "Game removed from library.")
    
    def _on_export(self):
        game = self._get_selected_game()
        if not game:
            QMessageBox.warning(self, "Warning", "Please select a game.")
            return
        
        save_path = os.path.join(game[2], "prefix", "drive_c", "users")
        
        if not os.path.exists(save_path):
            QMessageBox.warning(self, "Warning", "Save directory not found.")
            return
        
        export_path, _ = QFileDialog.getSaveFileName(
            self,
            "Export Save",
            f"{game[1]}_save.zip",
            "ZIP Files (*.zip)"
        )
        
        if export_path:
            if self.backup.export_save(save_path, export_path):
                QMessageBox.information(self, "Success", "Save exported successfully.")
            else:
                QMessageBox.critical(self, "Error", "Failed to export save.")
    
    def _on_import(self):
        game = self._get_selected_game()
        if not game:
            QMessageBox.warning(self, "Warning", "Please select a game.")
            return
        
        import_path, _ = QFileDialog.getOpenFileName(
            self,
            "Import Save",
            "",
            "ZIP Files (*.zip)"
        )
        
        if import_path:
            dest_path = os.path.join(game[2], "prefix", "drive_c", "users")
            os.makedirs(dest_path, exist_ok=True)
            
            if self.backup.import_save(import_path, dest_path):
                QMessageBox.information(self, "Success", "Save imported successfully.")
            else:
                QMessageBox.critical(self, "Error", "Failed to import save.")
        game = self.games[row]
        save_dir = f"{game[2]}/prefix/drive_c/users/steamuser/AppData"
        
        export_file, _ = QFileDialog.getSaveFileName(self, "Export Save File", f"{game[1]}_save.zip", "Zip Files (*.zip)")
        if export_file:
            success = self.backup.export_save(save_dir, export_file)
            if success:
                QMessageBox.information(self, "Success", "Save exported successfully!")
            else:
                QMessageBox.warning(self, "Error", "Failed to locate save directory.")