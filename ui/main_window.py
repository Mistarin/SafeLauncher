import os
from PyQt6.QtWidgets import (
    QMainWindow, QWidget, QVBoxLayout, QHBoxLayout, QPushButton,
    QGridLayout, QFileDialog, QMessageBox, QDialog, QLabel, QLineEdit,
    QComboBox, QFormLayout, QScrollArea, QFrame
)
from PyQt6.QtCore import Qt, QSize
from PyQt6.QtGui import QPixmap, QFont
from core.interfaces import ISandboxRunner, IBackupManager
from core.steamgriddb_client import SteamGridDBClient
from database import GameDatabase
import threading

class GameBannerWidget(QFrame):
    """Individual game banner card"""
    def __init__(self, game_id: int, name: str, banner_path: str = None):
        super().__init__()
        self.game_id = game_id
        self.name = name
        self.setFrameStyle(QFrame.Shape.StyledPanel | QFrame.Shadow.Raised)
        self.setLineWidth(2)
        self.setFixedSize(QSize(231, 150))
        self.setCursor(Qt.CursorShape.PointingHandCursor)
        self.selected = False
        self.setStyleSheet("border: 1px solid #666; border-radius: 5px; background: #2a2a2a;")
        
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        
        # Banner image
        self.image_label = QLabel()
        self.image_label.setFixedSize(QSize(231, 87))
        self.image_label.setScaledContents(True)
        layout.addWidget(self.image_label)
        
        # Game name
        name_label = QLabel(name)
        name_label.setWordWrap(True)
        name_label.setFont(QFont("Arial", 8))
        name_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        name_label.setStyleSheet("padding: 5px; background: #1a1a1a; color: #fff;")
        layout.addWidget(name_label)
        
        self.set_banner(banner_path)
    
    def set_banner(self, banner_path: str):
        """Set the banner image"""
        if banner_path and os.path.exists(banner_path):
            pixmap = QPixmap(banner_path)
            if not pixmap.isNull():
                self.image_label.setPixmap(pixmap)
                return
        
        # Fallback: solid color
        pixmap = QPixmap(231, 87)
        pixmap.fill("#444444")
        self.image_label.setPixmap(pixmap)
    
    def set_selected(self, selected: bool):
        """Highlight when selected"""
        self.selected = selected
        if selected:
            self.setStyleSheet("border: 3px solid #00ff00; border-radius: 5px; background: #2a2a2a;")
        else:
            self.setStyleSheet("border: 1px solid #666; border-radius: 5px; background: #2a2a2a;")

class AddGameDialog(QDialog):
    def __init__(self, parent=None, sgdb_client: SteamGridDBClient = None):
        super().__init__(parent)
        self.setWindowTitle("Add Game")
        self.setGeometry(100, 100, 500, 350)
        self.sgdb_client = sgdb_client
        self.banner_path = None
        
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
        
        # Banner preview
        self.banner_label = QLabel()
        self.banner_label.setFixedSize(QSize(231, 87))
        self.banner_label.setStyleSheet("border: 1px solid #666;")
        layout.addRow("Banner Preview:", self.banner_label)
        
        fetch_banner_btn = QPushButton("🔍 Fetch Banner from SteamGridDB")
        fetch_banner_btn.clicked.connect(self._fetch_banner)
        layout.addRow(fetch_banner_btn)
        
        buttons_layout = QHBoxLayout()
        add_btn = QPushButton("Add Game")
        cancel_btn = QPushButton("Cancel")
        add_btn.clicked.connect(self.accept)
        cancel_btn.clicked.connect(self.reject)
        buttons_layout.addWidget(add_btn)
        buttons_layout.addWidget(cancel_btn)
        layout.addRow(buttons_layout)
        
        self.setLayout(layout)
        self.setStyleSheet("""
            QDialog { background: #1a1a1a; }
            QLabel { color: #fff; }
            QLineEdit { background: #2a2a2a; color: #fff; border: 1px solid #666; padding: 5px; border-radius: 3px; }
            QPushButton { background: #0d47a1; color: white; border: none; padding: 8px; border-radius: 5px; font-weight: bold; }
            QPushButton:hover { background: #1565c0; }
            QComboBox { background: #2a2a2a; color: #fff; border: 1px solid #666; padding: 5px; border-radius: 3px; }
        """)
    
    def _browse_path(self):
        path = QFileDialog.getExistingDirectory(self, "Select Game Directory")
        if path:
            self.path_input.setText(path)
    
    def _fetch_banner(self):
        game_name = self.name_input.text()
        if not game_name:
            QMessageBox.warning(self, "Error", "Please enter a game name first.")
            return
        
        if not self.sgdb_client:
            QMessageBox.warning(self, "Error", "Banner fetcher not available.")
            return
        
        # Fetch in background
        def fetch():
            try:
                result = self.sgdb_client.search_game(game_name)
                if result and result.get("banner_url"):
                    self.banner_path = self.sgdb_client.download_banner(result["banner_url"], 0)
                    if self.banner_path and os.path.exists(self.banner_path):
                        pixmap = QPixmap(self.banner_path)
                        self.banner_label.setPixmap(pixmap)
                        QMessageBox.information(self, "Success", "Banner fetched!")
                        return
                
                QMessageBox.information(self, "Info", "No banner found. You can still add the game.")
            except Exception as e:
                QMessageBox.warning(self, "Error", f"Failed to fetch banner: {str(e)}")
        
        threading.Thread(target=fetch, daemon=True).start()
    
    def get_values(self):
        return (
            self.name_input.text(),
            self.path_input.text(),
            self.exe_input.text(),
            self.mode_combo.currentText(),
            self.banner_path
        )

class MainWindow(QMainWindow):
    def __init__(self, db: GameDatabase, runner: ISandboxRunner, backup: IBackupManager):
        super().__init__()
        self.db = db
        self.runner = runner
        self.backup = backup
        self.sgdb_client = SteamGridDBClient()
        self.games = []
        self.selected_game = None
        self.banner_widgets = {}

        self.setWindowTitle("🎮 Game Sandbox Launcher - Banner Edition")
        self.resize(1000, 700)

        # UI Setup
        central_widget = QWidget()
        central_widget.setStyleSheet("background: #1a1a1a;")
        main_layout = QVBoxLayout(central_widget)
        main_layout.setContentsMargins(15, 15, 15, 15)
        main_layout.setSpacing(10)
        
        # Title
        title = QLabel("🎮 My Game Library")
        title.setFont(QFont("Arial", 20, QFont.Weight.Bold))
        title.setStyleSheet("color: #fff;")
        main_layout.addWidget(title)

        # Games grid in scroll area
        scroll_area = QScrollArea()
        scroll_area.setWidgetResizable(True)
        scroll_area.setStyleSheet("QScrollArea { background: #1a1a1a; border: none; }")
        
        self.games_container = QWidget()
        self.games_container.setStyleSheet("background: #1a1a1a;")
        self.games_layout = QGridLayout(self.games_container)
        self.games_layout.setSpacing(15)
        
        scroll_area.setWidget(self.games_container)
        main_layout.addWidget(scroll_area)

        # Buttons layout
        buttons_layout = QHBoxLayout()
        buttons_layout.setSpacing(10)
        
        self.btn_launch = QPushButton("▶ Launch Selected")
        self.btn_launch.clicked.connect(self._on_launch)
        self.btn_launch.setMinimumHeight(40)
        buttons_layout.addWidget(self.btn_launch)
        
        self.btn_add = QPushButton("➕ Add Game")
        self.btn_add.clicked.connect(self._on_add)
        self.btn_add.setMinimumHeight(40)
        buttons_layout.addWidget(self.btn_add)
        
        self.btn_remove = QPushButton("🗑️ Remove")
        self.btn_remove.clicked.connect(self._on_remove)
        self.btn_remove.setMinimumHeight(40)
        buttons_layout.addWidget(self.btn_remove)

        main_layout.addLayout(buttons_layout)
        
        # Export/Import buttons
        save_layout = QHBoxLayout()
        save_layout.setSpacing(10)
        
        self.btn_export = QPushButton("💾 Export Save")
        self.btn_export.clicked.connect(self._on_export)
        self.btn_export.setMinimumHeight(35)
        save_layout.addWidget(self.btn_export)
        
        self.btn_import = QPushButton("📂 Import Save")
        self.btn_import.clicked.connect(self._on_import)
        self.btn_import.setMinimumHeight(35)
        save_layout.addWidget(self.btn_import)
        
        main_layout.addLayout(save_layout)
        
        self.setCentralWidget(central_widget)
        self.setStyleSheet("""
            QMainWindow { background: #1a1a1a; }
            QPushButton { 
                background: #0d47a1; 
                color: white; 
                border: none; 
                padding: 8px 15px;
                border-radius: 5px; 
                font-weight: bold;
                font-size: 12px;
            }
            QPushButton:hover { background: #1565c0; }
            QLabel { color: #fff; }
        """)
        
        self._refresh_library()

    def _refresh_library(self):
        """Clear and reload game banners"""
        # Clear existing widgets
        while self.games_layout.count() > 0:
            item = self.games_layout.takeAt(0)
            if item.widget():
                item.widget().deleteLater()
        
        self.banner_widgets.clear()
        self.games = self.db.get_all_games()
        
        if not self.games:
            label = QLabel("🎮 No games in library yet.\nClick 'Add Game' to get started!")
            label.setAlignment(Qt.AlignmentFlag.AlignCenter)
            label.setStyleSheet("color: #999; font-size: 14px; padding: 40px;")
            self.games_layout.addWidget(label, 0, 0, 1, 3)
            return
        
        # Add game banners in grid
        col = 0
        row = 0
        for game in self.games:
            game_id, name, path, executable, mode, banner_url, steam_id = game
            
            widget = GameBannerWidget(game_id, name, banner_url)
            widget.mousePressEvent = lambda e, g_id=game_id: self._select_game(g_id)
            
            self.games_layout.addWidget(widget, row, col)
            self.banner_widgets[game_id] = widget
            
            col += 1
            if col >= 3:  # 3 columns
                col = 0
                row += 1
    
    def _select_game(self, game_id: int):
        """Select a game by clicking its banner"""
        # Deselect all
        for widget in self.banner_widgets.values():
            widget.set_selected(False)
        
        # Select clicked game
        for game in self.games:
            if game[0] == game_id:
                self.selected_game = game
                if game_id in self.banner_widgets:
                    self.banner_widgets[game_id].set_selected(True)
                break

    def _get_selected_game(self):
        """Get the currently selected game"""
        return self.selected_game

    def _on_launch(self):
        game = self._get_selected_game()
        if not game:
            QMessageBox.warning(self, "Warning", "Please select a game to launch.")
            return
        
        # game = (id, name, path, executable, mode, banner_url, steam_id)
        try:
            self.runner.launch(game[2], game[3], game[4])
            QMessageBox.information(self, "Info", f"Launching {game[1]}...")
        except Exception as e:
            QMessageBox.critical(self, "Error", f"Failed to launch game: {str(e)}")
    
    def _on_add(self):
        dialog = AddGameDialog(self, self.sgdb_client)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            name, path, exe, mode, banner_path = dialog.get_values()
            if not name or not path or not exe:
                QMessageBox.warning(self, "Error", "All fields are required.")
                return
            if not os.path.isdir(path):
                QMessageBox.warning(self, "Error", "Invalid game path.")
                return
            
            self.db.add_game(name, path, exe, mode, banner_path)
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
            self.selected_game = None
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
