import os
import time
import json
import socket
import struct
import threading

from core.logger import get_logger

logger = get_logger("discord_rpc")

CLIENT_ID = "1200000000000000000"  # Default Discord Application ID for SafeLauncher


class DiscordRPC:
    """Lightweight native Discord Rich Presence IPC client for Linux using Unix sockets."""

    def __init__(self, client_id: str = "1200000000000000000"):
        self.client_id = client_id
        self.sock = None
        self.connected = False
        self.lock = threading.Lock()

    def _get_ipc_path(self):
        """Locate Discord IPC socket on Linux across native and Flatpak installs."""
        env_vars = ["XDG_RUNTIME_DIR", "TMPDIR", "TMP", "TEMP"]
        base_paths = []
        for env in env_vars:
            val = os.environ.get(env)
            if val and os.path.exists(val):
                base_paths.append(val)
        base_paths.extend(["/tmp", "/tmp/app/com.discordapp.Discord"])

        for base in base_paths:
            for i in range(10):
                path = os.path.join(base, f"discord-ipc-{i}")
                if os.path.exists(path):
                    return path
        return None

    def connect(self) -> bool:
        """Establish handshake connection with Discord client IPC socket."""
        with self.lock:
            if self.connected:
                return True

            ipc_path = self._get_ipc_path()
            if not ipc_path:
                return False

            try:
                self.sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
                self.sock.settimeout(2.0)
                self.sock.connect(ipc_path)

                # Send Handshake (Opcode 0)
                payload = json.dumps({"v": 1, "client_id": self.client_id})
                data = struct.pack("<II", 0, len(payload)) + payload.encode("utf-8")
                self.sock.sendall(data)

                # Read Handshake Response
                header = self.sock.recv(8)
                if len(header) == 8:
                    op, length = struct.unpack("<II", header)
                    self.sock.recv(length)
                    self.connected = True
                    return True
                self._disconnect()
                return False
            except Exception:
                self._disconnect()
                return False

    def set_activity(self, game_name: str, start_timestamp: int = None, details: str = "Playing via SafeLauncher"):
        """Update Discord Rich Presence activity."""
        if not self.connected:
            if not self.connect():
                return False

        if start_timestamp is None:
            start_timestamp = int(time.time())

        activity = {
            "cmd": "SET_ACTIVITY",
            "args": {
                "pid": os.getpid(),
                "activity": {
                    "details": game_name,
                    "state": details,
                    "timestamps": {
                        "start": start_timestamp
                    },
                    "assets": {
                        "large_image": "gamepad",
                        "large_text": game_name,
                        "small_image": "shield",
                        "small_text": "SafeLauncher Sandbox"
                    }
                }
            },
            "nonce": str(time.time())
        }

        with self.lock:
            sock = self.sock
            if sock is None:
                return False
            try:
                payload = json.dumps(activity)
                data = struct.pack("<II", 1, len(payload)) + payload.encode("utf-8")
                sock.sendall(data)
                return True
            except Exception:
                self._disconnect()
                return False

    def clear_activity(self):
        """Clear Discord Rich Presence activity."""
        if not self.connected:
            return

        activity = {
            "cmd": "SET_ACTIVITY",
            "args": {
                "pid": os.getpid(),
                "activity": None
            },
            "nonce": str(time.time())
        }

        with self.lock:
            try:
                payload = json.dumps(activity)
                data = struct.pack("<II", 1, len(payload)) + payload.encode("utf-8")
                self.sock.sendall(data)
            except Exception:
                logger.debug("Ignoring non-fatal error in best-effort step", exc_info=True)
            finally:
                self._disconnect()

    def _disconnect(self):
        self.connected = False
        if self.sock:
            try:
                self.sock.close()
            except Exception:
                logger.debug("Ignoring non-fatal error in best-effort step", exc_info=True)
            self.sock = None
