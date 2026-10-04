import sys
from PyQt6.QtWidgets import QApplication, QMessageBox
from PyQt6.QtGui import QIcon

from core.logger import get_logger
from core.bootstrap import setup_application_environment, check_already_running, create_single_instance_server
from database import DatabaseRecoveryError, GameDatabase
from core.firejail_runner import FirejailSandboxRunner
from core.zip_backup import ZipBackupManager
from core.dependency_checker import install_requirements, missing_requirements
from core.desktop_integration import is_desktop_entry_installed

from core.version import __version__
from ui.icons import LOGO_PATH

logger = get_logger("Main")


def run_gui(argv=None):
    setup_application_environment()

    logger.info("Creating Qt application instance...")
    app = QApplication(argv if argv is not None else sys.argv)
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
        return 0

    activation_target = []
    server = create_single_instance_server(
        lambda: activation_target[0].activate_window() if activation_target else None
    )
    if not server.isListening():
        return 0

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
            server.close()
            return 1

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
    try:
        db = GameDatabase()
    except DatabaseRecoveryError as error:
        logger.critical("SafeLauncher could not recover its local library: %s", error)
        QMessageBox.critical(
            None,
            "Could not open game library",
            f"{error}\n\nSafeLauncher will close without changing your library data.",
        )
        server.close()
        return 1
    except BaseException:
        server.close()
        raise
    runner = None
    runtime = None
    # Keep feature/runtime imports behind the dependency check above. A missing
    # optional package should produce the install prompt, not prevent bootstrap.
    try:
        runner = FirejailSandboxRunner()
        backup = ZipBackupManager()
        from ui.application_runtime import ApplicationRuntime
        from ui.main_window import MainWindow
        runtime = ApplicationRuntime(db, runner, backup, parent=app)
        window = MainWindow(db, runner, backup, runtime=runtime)
    except BaseException:
        try:
            if runtime is not None:
                runtime.close_resources()
        finally:
            try:
                if db is not None:
                    db.close()
            finally:
                server.close()
        raise
    activation_target.append(window)
    start_optional_tasks(window, runtime.settings)

    window.show()
    logger.info("SafeLauncher UI started successfully.")
    try:
        return app.exec()
    finally:
        try:
            server.close()
            runtime.close_resources()
        finally:
            db.close()



def start_optional_tasks(task_host, settings):
    """Schedule optional startup work through the public task interface."""
    from core.network_policy import automatic_network_allowed
    if not automatic_network_allowed(settings):
        return
    from core.telemetry import send_central_telemetry
    from core.ludusavi_installer import ensure_ludusavi
    task_host.start_background_task("SafeLauncher-TelemetryPing", lambda: send_central_telemetry(__version__))
    task_host.start_background_task(
        "SafeLauncher-LudusaviBootstrap", ensure_ludusavi,
        lambda result: logger.info("Ludusavi bootstrap finished: %s", result),
    )
