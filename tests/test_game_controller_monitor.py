from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from PyQt6.QtWidgets import QApplication

from core.game_controller_monitor import (
    GameControllerMonitor,
    connected_game_controller_inputs,
    count_connected_game_controllers,
)


class GameControllerMonitorTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = QApplication.instance() or QApplication([])

    def test_counts_joystick_handlers_and_event_only_gamepads(self):
        devices = '''
I: Bus=0003 Vendor=045e Product=0b12 Version=0111
N: Name="Xbox Wireless Controller"
H: Handlers=event4 js0
B: KEY=some-buttons
B: ABS=some-axes

I: Bus=0003 Vendor=1234 Product=5678 Version=0001
N: Name="DualSense Wireless Controller"
H: Handlers=event8
B: KEY=some-buttons
B: ABS=some-axes
'''

        self.assertEqual(count_connected_game_controllers(devices), 2)
        self.assertEqual(
            connected_game_controller_inputs(devices)[1],
            ("event4", "event8"),
        )

    def test_does_not_treat_regular_input_devices_as_controllers(self):
        devices = '''
I: Bus=0011 Vendor=0001 Product=0001 Version=0001
N: Name="AT Translated Set 2 keyboard"
H: Handlers=event0
B: KEY=keyboard-keys

I: Bus=0003 Vendor=0001 Product=0002 Version=0001
N: Name="Touchscreen"
H: Handlers=event1
B: ABS=touch-axes
'''

        self.assertEqual(count_connected_game_controllers(devices), 0)

    def test_monitor_emits_only_presence_changes(self):
        with tempfile.TemporaryDirectory() as temporary_directory:
            devices_path = Path(temporary_directory) / "devices"
            devices_path.write_text("", encoding="utf-8")
            monitor = GameControllerMonitor(
                devices_path=devices_path,
                interval_ms=60_000,
            )
            self.addCleanup(monitor.deleteLater)
            changes = []
            monitor.controller_count_changed.connect(changes.append)

            monitor.poll()
            monitor.poll()
            devices_path.write_text(
                'N: Name="USB Gamepad"\nH: Handlers=event7 js0\n',
                encoding="utf-8",
            )
            monitor.poll()
            monitor.poll()

        self.assertEqual(changes, [0, 1])


if __name__ == "__main__":
    unittest.main()
