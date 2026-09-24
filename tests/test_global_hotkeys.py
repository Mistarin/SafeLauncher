"""Lifecycle tests for the optional global hotkey listener."""

from __future__ import annotations

import unittest
from unittest.mock import patch

from core.global_hotkeys import GlobalHotkeyListener


class _FakeThread:
    def __init__(self, **kwargs):
        self.kwargs = kwargs
        self.started = False

    def start(self):
        self.started = True

    def is_alive(self):
        return False


class GlobalHotkeyLifecycleTests(unittest.TestCase):
    def test_listener_thread_is_daemon_fallback(self):
        listener = GlobalHotkeyListener()
        with patch("core.global_hotkeys.threading.Thread", _FakeThread):
            listener.start()
            listener.stop()
        self.assertIsNone(listener._thread)

    def test_stop_is_idempotent_without_a_started_thread(self):
        listener = GlobalHotkeyListener()
        self.assertTrue(listener.stop())
        self.assertTrue(listener.stop())

    def test_stop_leaves_display_cleanup_to_listener_thread(self):
        listener = GlobalHotkeyListener()

        class _Display:
            def __init__(self):
                self.closed = False

            def close(self):
                self.closed = True

        display = _Display()
        listener._display = display
        self.assertTrue(listener.stop())
        self.assertFalse(display.closed)
        self.assertTrue(listener._stop_event.is_set())


if __name__ == "__main__":
    unittest.main()
