import sys
from PyQt6.QtWidgets import QApplication

from core.logger import get_logger
from core.bootstrap import setup_application_environment, setup_single_instance_ipc
from database import GameDatabase
from core.firejail_runner import FirejailSandboxRunner
from core.zip_backup import ZipBackupManager
from ui.main_window import MainWindow

logger = get_logger("Main")


def main():
    setup_application_environment()

    app = QApplication(sys.argv)

    # Initialize core services via DIP contracts
    logger.info("Initializing core database and sandbox runners...")
    db = GameDatabase()
    runner = FirejailSandboxRunner()
    backup = ZipBackupManager()

    # Create main window
    window = MainWindow(db, runner, backup)

    # Setup single-instance IPC with activation callback
    server = setup_single_instance_ipc(window._show_and_raise)

    window.show()
    logger.info("SafeLauncher UI started successfully.")
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
