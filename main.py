import os
import sys
from PyQt6.QtWidgets import QApplication, QMessageBox
from PyQt6.QtGui import QIcon

from core.logger import get_logger
from core.bootstrap import setup_application_environment, check_already_running, create_single_instance_server
from database import GameDatabase
from core.firejail_runner import FirejailSandboxRunner
from core.zip_backup import ZipBackupManager
from core.dependency_checker import install_requirements, missing_requirements
from core.desktop_integration import is_desktop_entry_installed

from core.version import APP_VERSION, __version__
from ui.icons import LOGO_PATH

logger = get_logger("Main")


def main():
    # Apply the command-line override before any Qt objects or background
    # services are created.  The shared policy is then seen consistently by
    # MainWindow, telemetry, update checks, and managed downloads.
    if "--offline" in sys.argv:
        os.environ["SAFELAUNCHER_OFFLINE_MODE"] = "1"

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

    logger.info("Creating Qt application instance...")
    app = QApplication(sys.argv)
    logger.info("Qt application instance created.")
    # Set the application-level icon before any windows are created. This is
    # what Linux taskbars/window managers use for a Python-launched process.
    if LOGO_PATH:
        try:
            app_icon = QIcon(LOGO_PATH)
            if not app_icon.isNull():
                app.setWindowIcon(app_icon)
        except Exception as exc:
            logger.warning(f"Could not load application icon; continuing without it: {exc}")
    # Registering an application ID with the desktop portal before the XDG
    # desktop entry exists produces a noisy, harmless QDBus warning.  The
    # desktop ID is still set for installed launchers and AppImages.
    if is_desktop_entry_installed():
        app.setDesktopFileName("safelauncher")

    # 1. Fast probe: if already running, focus existing window and exit immediately
    if check_already_running():
        sys.exit(0)

    missing = [] if getattr(sys, "frozen", False) else missing_requirements()
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
            # Do not continue into imports that require unavailable modules;
            # the warning already explains how to launch with the venv.
            return

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
    from ui.main_window import MainWindow
    window = MainWindow(db, runner, backup)
    # Run telemetry through the same owned worker lifecycle as every other
    # startup task; it remains silent and bounded when disabled/offline.
    from core.network_policy import automatic_network_allowed
    if automatic_network_allowed(window.settings):
        from core.telemetry import send_central_telemetry
        window._start_managed_task(
            "SafeLauncher-TelemetryPing",
            lambda: send_central_telemetry(__version__),
        )
    server = create_single_instance_server(window._show_and_raise)
    if not server.isListening():
        # A second process may have won the bind race. Do not start a second
        # visible application when the IPC endpoint belongs to that process.
        window.close()
        sys.exit(0)

    # 4. Ensure the bundled ludusavi save-detection engine is present.  The
    #    main window owns this task so shutdown can cancel and report it.
    if automatic_network_allowed(window.settings):
        from core.ludusavi_installer import ensure_ludusavi
        window._start_managed_task(
            "SafeLauncher-LudusaviBootstrap",
            ensure_ludusavi,
            lambda result: logger.info("Ludusavi bootstrap finished: %s", result),
        )

    window.show()
    logger.info("SafeLauncher UI started successfully.")
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
