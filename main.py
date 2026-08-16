import sys
from PyQt6.QtWidgets import QApplication

from core.logger import get_logger
from core.bootstrap import setup_application_environment, check_already_running, create_single_instance_server
from database import GameDatabase
from core.firejail_runner import FirejailSandboxRunner
from core.zip_backup import ZipBackupManager
from ui.main_window import MainWindow

logger = get_logger("Main")


def main():
    setup_application_environment()

    app = QApplication(sys.argv)

    # 1. Fast probe: if already running, focus existing window and exit immediately
    if check_already_running():
        sys.exit(0)

    # 2. Initialize core services via DIP contracts
    logger.info("Initializing core database and sandbox runners...")
    db = GameDatabase()
    runner = FirejailSandboxRunner()
    backup = ZipBackupManager()

    # 3. Create main window & bind single-instance listener
    window = MainWindow(db, runner, backup)
    server = create_single_instance_server(window._show_and_raise)

    window.show()
    logger.info("SafeLauncher UI started successfully.")
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
