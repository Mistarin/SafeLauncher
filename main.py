import sys
from PyQt6.QtWidgets import QApplication
from database import GameDatabase
from core.firejail_runner import FirejailSandboxRunner
from core.zip_backup import ZipBackupManager
from ui.main_window import MainWindow

def main():
    app = QApplication(sys.argv)
    
    # Initialize components
    db = GameDatabase()
    runner = FirejailSandboxRunner()
    backup = ZipBackupManager()
    
    # Create and show main window
    window = MainWindow(db, runner, backup)
    window.show()
    
    sys.exit(app.exec())

if __name__ == "__main__":
    main()