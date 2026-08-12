import sys
import os
import traceback
import platform
from PyQt6.QtWidgets import (
    QApplication, QDialog, QVBoxLayout, QHBoxLayout, QLabel, QTextEdit, QPushButton, QMessageBox
)
from PyQt6.QtCore import Qt, QUrl
from PyQt6.QtGui import QDesktopServices
from PyQt6.QtNetwork import QLocalServer, QLocalSocket

from core.logger import setup_logging, get_logger, log_crash, LOG_DIR, CRASH_FILE
from database import GameDatabase
from core.firejail_runner import FirejailSandboxRunner
from core.zip_backup import ZipBackupManager
from ui.main_window import MainWindow

SERVER_NAME = "SafeLauncher_SingleInstance_Server"
logger = get_logger("Main")


class CrashReportDialog(QDialog):
    """Graphical crash reporting dialog presented when an unhandled exception occurs."""

    def __init__(self, exc_type, exc_value, exc_tb, parent=None):
        super().__init__(parent)
        self.setWindowTitle("SafeLauncher - Application Error")
        self.setFixedSize(620, 420)

        tb_lines = traceback.format_exception(exc_type, exc_value, exc_tb)
        self.traceback_text = "".join(tb_lines)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 20, 20, 20)

        header = QLabel("⚠️ An unhandled application error occurred")
        header.setStyleSheet("font-size: 16px; font-weight: bold; color: #ef4444;")
        layout.addWidget(header)

        sub = QLabel("Details of the issue have been recorded to the application crash log:")
        sub.setStyleSheet("color: #a1a1aa; font-size: 12px;")
        layout.addWidget(sub)

        self.console = QTextEdit()
        self.console.setReadOnly(True)
        self.console.setPlainText(self.traceback_text)
        self.console.setStyleSheet("""
            QTextEdit {
                background-color: #18181b;
                color: #f4f4f5;
                font-family: monospace;
                font-size: 11px;
                border: 1px solid #3f3f46;
                border-radius: 6px;
            }
        """)
        layout.addWidget(self.console)

        btn_layout = QHBoxLayout()

        btn_copy = QPushButton("📋 Copy Traceback")
        btn_copy.clicked.connect(self._copy_traceback)
        btn_layout.addWidget(btn_copy)

        btn_logs = QPushButton("📁 Open Log Directory")
        btn_logs.clicked.connect(self._open_log_dir)
        btn_layout.addWidget(btn_logs)

        btn_layout.addStretch(1)

        btn_close = QPushButton("Close Launcher")
        btn_close.clicked.connect(self.reject)
        btn_layout.addWidget(btn_close)

        layout.addLayout(btn_layout)

    def _copy_traceback(self):
        clipboard = QApplication.clipboard()
        if clipboard:
            clipboard.setText(self.traceback_text)
            QMessageBox.information(self, "Copied", "Traceback copied to clipboard!")

    def _open_log_dir(self):
        if os.path.exists(LOG_DIR):
            QDesktopServices.openUrl(QUrl.fromLocalFile(LOG_DIR))


def global_exception_hook(exc_type, exc_value, exc_tb):
    """Global handler for unhandled exceptions in the main Qt event loop."""
    if issubclass(exc_type, KeyboardInterrupt):
        sys.__excepthook__(exc_type, exc_value, exc_tb)
        return

    tb_str = "".join(traceback.format_exception(exc_type, exc_value, exc_tb))
    sys_info = (
        f"OS: {platform.system()} {platform.release()} ({platform.machine()})\n"
        f"Python: {platform.python_version()}\n"
        f"Desktop Session: {os.environ.get('XDG_CURRENT_DESKTOP', 'Unknown')} ({os.environ.get('XDG_SESSION_TYPE', 'Unknown')})"
    )
    logger.critical(f"Unhandled Exception: {exc_value}\n{tb_str}")
    log_crash(tb_str, sys_info)

    app = QApplication.instance()
    if app:
        try:
            dialog = CrashReportDialog(exc_type, exc_value, exc_tb)
            dialog.exec()
        except Exception as e:
            sys.stderr.write(f"Failed to display crash dialog: {e}\n")


def main():
    # Install logger & exception hooks
    setup_logging()
    sys.excepthook = global_exception_hook

    import threading
    def thread_exception_hook(args):
        logger.error(f"Unhandled exception in background thread '{args.thread.name}': {args.exc_value}")
        tb_str = "".join(traceback.format_exception(
            args.exc_type, args.exc_value, args.exc_traceback
        ))
        log_crash(tb_str, f"Thread: {args.thread.name}")

    if hasattr(threading, "excepthook"):
        threading.excepthook = thread_exception_hook

    app = QApplication(sys.argv)

    # Single Instance Check via QLocalSocket
    socket = QLocalSocket()
    socket.connectToServer(SERVER_NAME)
    if socket.waitForConnected(500):
        logger.info("Existing SafeLauncher instance detected. Sending activation signal and exiting.")
        socket.write(b"ACTIVATE")
        socket.waitForBytesWritten(1000)
        socket.disconnectFromServer()
        sys.exit(0)

    # Do not remove a server socket until we have confirmed it is stale.
    server = QLocalServer()
    if not server.listen(SERVER_NAME):
        probe = QLocalSocket()
        probe.connectToServer(SERVER_NAME)
        if probe.waitForConnected(1000):
            probe.write(b"ACTIVATE")
            probe.waitForBytesWritten(1000)
            probe.disconnectFromServer()
            sys.exit(0)
        server.removeServer(SERVER_NAME)
        if not server.listen(SERVER_NAME):
            logger.critical(f"Could not create single-instance server: {server.errorString()}")
            raise SystemExit(1)

    # Initialize core components
    logger.info("Initializing core database and sandbox runners...")
    db = GameDatabase()
    runner = FirejailSandboxRunner()
    backup = ZipBackupManager()

    # Create main window
    window = MainWindow(db, runner, backup)

    def _on_new_connection():
        client = server.nextPendingConnection()
        if client:
            client.waitForReadyRead(500)
            msg = client.readAll().data()
            if b"ACTIVATE" in msg:
                logger.info("Received activation request from secondary launcher process.")
                window._show_and_raise()
            client.disconnectFromServer()

    server.newConnection.connect(_on_new_connection)

    window.show()
    logger.info("SafeLauncher UI started successfully.")
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
