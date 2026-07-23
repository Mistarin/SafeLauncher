import sys
from PyQt6.QtWidgets import QApplication
from PyQt6.QtNetwork import QLocalServer, QLocalSocket
from database import GameDatabase
from core.firejail_runner import FirejailSandboxRunner
from core.zip_backup import ZipBackupManager
from ui.main_window import MainWindow

SERVER_NAME = "MGLauncher_SingleInstance_Server"

def main():
    app = QApplication(sys.argv)

    # Single Instance Check via QLocalSocket
    socket = QLocalSocket()
    socket.connectToServer(SERVER_NAME)
    if socket.waitForConnected(500):
        # An existing instance of MGLauncher is already running!
        # Send activate signal to bring existing window to front, then exit.
        socket.write(b"ACTIVATE")
        socket.waitForBytesWritten(1000)
        socket.disconnectFromServer()
        sys.exit(0)

    # Clean up stale socket if a previous crash occurred
    server = QLocalServer()
    server.removeServer(SERVER_NAME)
    server.listen(SERVER_NAME)

    # Initialize core components
    db = GameDatabase()
    runner = FirejailSandboxRunner()
    backup = ZipBackupManager()
    
    # Create main window
    window = MainWindow(db, runner, backup)

    # Slot to handle incoming connection from a second launch attempt
    def _on_new_connection():
        client = server.nextPendingConnection()
        if client:
            client.waitForReadyRead(500)
            msg = client.readAll().data()
            if b"ACTIVATE" in msg:
                window._show_and_raise()
            client.disconnectFromServer()

    server.newConnection.connect(_on_new_connection)

    window.show()
    sys.exit(app.exec())

if __name__ == "__main__":
    main()