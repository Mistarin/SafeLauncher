"""Detect connected Linux game controllers through the kernel input listing."""

from __future__ import annotations

import re
from pathlib import Path

from PyQt6.QtCore import QObject, QTimer, pyqtSignal


_JS_HANDLER = re.compile(r"\bjs\d+\b")
_EVENT_HANDLER = re.compile(r"\bevent\d+\b")
_CONTROLLER_NAME = re.compile(
    r"controller|gamepad|joystick|x.?box|dualshock|dualsense|joy-con|steam controller",
    re.IGNORECASE,
)
_EVENT_NODES = re.compile(r"\bevent\d+\b")


def connected_game_controller_inputs(devices_text: str) -> tuple[int, tuple[str, ...]]:
    """Return controller count and evdev nodes without confusing peripherals."""
    count = 0
    nodes: set[str] = set()
    for block in str(devices_text or "").split("\n\n"):
        name = ""
        handlers = ""
        has_keys = False
        has_axes = False
        for line in block.splitlines():
            if line.startswith("N: Name="):
                name = line.split("=", 1)[1].strip().strip('"')
            elif line.startswith("H: Handlers="):
                handlers = line.split("=", 1)[1]
            elif line.startswith("B: KEY="):
                has_keys = True
            elif line.startswith("B: ABS="):
                has_axes = True
        is_controller = bool(_JS_HANDLER.search(handlers))
        if not is_controller:
            is_controller = bool(
                _EVENT_HANDLER.search(handlers)
                and _CONTROLLER_NAME.search(name)
                and has_keys
                and has_axes
            )
        if is_controller:
            count += 1
            nodes.update(_EVENT_NODES.findall(handlers))
    return count, tuple(sorted(nodes, key=lambda node: int(node[5:])))


def count_connected_game_controllers(devices_text: str) -> int:
    """Count joystick handlers, with a guarded fallback for event-only pads."""
    return connected_game_controller_inputs(devices_text)[0]


class GameControllerMonitor(QObject):
    """Poll kernel input metadata and report presence changes to the UI."""

    controller_count_changed = pyqtSignal(int)
    controller_event_nodes_changed = pyqtSignal(object)

    def __init__(
        self,
        *,
        interval_ms: int = 3_000,
        devices_path: str | Path = "/proc/bus/input/devices",
        parent=None,
    ) -> None:
        super().__init__(parent)
        self.devices_path = Path(devices_path)
        self.controller_count: int | None = None
        self.controller_event_nodes: tuple[str, ...] | None = None
        self.timer = QTimer(self)
        self.timer.setInterval(max(500, int(interval_ms)))
        self.timer.timeout.connect(self.poll)

    def start(self) -> None:
        self.poll()
        self.timer.start()

    def stop(self) -> None:
        self.timer.stop()

    def poll(self) -> None:
        try:
            devices_text = self.devices_path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            devices_text = ""
        count, event_nodes = connected_game_controller_inputs(devices_text)
        if count != self.controller_count:
            self.controller_count = count
            self.controller_count_changed.emit(count)
        if event_nodes != self.controller_event_nodes:
            self.controller_event_nodes = event_nodes
            self.controller_event_nodes_changed.emit(event_nodes)


__all__ = [
    "GameControllerMonitor",
    "connected_game_controller_inputs",
    "count_connected_game_controllers",
]
