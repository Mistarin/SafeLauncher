import sys
from PyQt6.QtWidgets import QApplication, QMessageBox

from core.logger import get_logger
from core.bootstrap import setup_application_environment, check_already_running, create_single_instance_server
from database import GameDatabase
from core.firejail_runner import FirejailSandboxRunner
from core.zip_backup import ZipBackupManager
from ui.main_window import MainWindow
from core.dependency_checker import install_requirements, missing_requirements

# Single source of truth for the application version.
__version__ = "0.3.1"

logger = get_logger("Main")


def main():
    # Handle CLI system diagnostics / doctor
    if any(arg in sys.argv for arg in ("--doctor", "doctor", "-d")):
        from core.system_inspector import print_system_report
        print_system_report()
        sys.exit(0)

    # Handle terminal setup wizard before GUI application bootstrap
    if any(arg in sys.argv for arg in ("--setup-cloud", "setup-cloud", "-c")):
        from core.cloud_cli_wizard import run_cloud_setup_wizard
        sys.exit(run_cloud_setup_wizard())

    # Handle desktop shortcut registration
    if any(arg in sys.argv for arg in ("--install-desktop", "install-desktop", "-i")):
        from core.desktop_integration import install_safelauncher_desktop_entry
        ok, msg = install_safelauncher_desktop_entry()
        if ok:
            print(f"\033[92m✔ {msg}\033[0m")
            sys.exit(0)
        else:
            print(f"\033[91m✖ {msg}\033[0m")
            sys.exit(1)

    setup_application_environment()

    # Anonymous player heartbeat to measure active installations
    try:
        from core.telemetry import ping_central_telemetry
        ping_central_telemetry(__version__)
    except Exception:
        pass

    app = QApplication(sys.argv)

    # 1. Fast probe: if already running, focus existing window and exit immediately
    if check_already_running():
        sys.exit(0)

    missing = missing_requirements()
    if missing:
        details = "\n".join(f"• {item}" for item in missing)
        if sys.prefix == sys.base_prefix:
            QMessageBox.warning(
                None,
                "Use SafeLauncher virtual environment",
                "These dependencies are unavailable to the system Python:\n\n"
                f"{details}\n\n"
                "The operating system blocks pip from modifying system packages. "
                "Run SafeLauncher from its virtual environment:\n\n"
                "python -m venv .venv\n"
                ".venv/bin/python -m pip install -r requirements.txt\n"
                ".venv/bin/python main.py",
            )
            missing = []

    if missing:
        answer = QMessageBox.question(
            None,
            "SafeLauncher dependencies missing",
            "Some Python dependencies are missing:\n\n"
            f"{details}\n\nInstall them now using the current Python environment?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.Yes,
        )
        if answer == QMessageBox.StandardButton.Yes:
            try:
                result = install_requirements()
                if result.returncode != 0:
                    QMessageBox.critical(
                        None,
                        "Dependency installation failed",
                        "SafeLauncher will continue, but some features may be unavailable.\n\n"
                        + (result.stderr or result.stdout or "pip exited with an unknown error."),
                    )
                else:
                    missing = missing_requirements()
                    if missing:
                        QMessageBox.warning(
                            None,
                            "Some dependencies are still missing",
                            "Installation completed, but these requirements are still unavailable:\n\n"
                            + "\n".join(missing),
                        )
            except Exception as error:
                QMessageBox.critical(None, "Dependency installation failed", str(error))

    # 2. Initialize core services via DIP contracts
    logger.info("Initializing core database and sandbox runners...")
    db = GameDatabase()
    runner = FirejailSandboxRunner()
    backup = ZipBackupManager()

    # 3. Create main window & bind single-instance listener
    window = MainWindow(db, runner, backup)
    server = create_single_instance_server(window._show_and_raise)

    # 4. Ensure the bundled ludusavi save-detection engine is present.
    #    Runs on a daemon thread: never download on the GUI thread; until it
    #    finishes (or if it fails) detection falls back to heuristics.
    import threading
    from core.ludusavi_installer import ensure_ludusavi
    threading.Thread(
        target=ensure_ludusavi,
        daemon=True,
        name="SafeLauncher-LudusaviBootstrap",
    ).start()

    window.show()
    logger.info("SafeLauncher UI started successfully.")
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
