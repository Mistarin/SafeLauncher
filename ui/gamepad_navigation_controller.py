"""Non-exclusive Linux evdev input translated into ordinary Qt navigation."""

from __future__ import annotations

import os
import struct
from typing import Callable

from PyQt6 import sip
from PyQt6.QtCore import QObject, QPoint, Qt, QSocketNotifier, QTimer, pyqtSignal
from PyQt6.QtGui import QKeyEvent
from PyQt6.QtWidgets import (
    QApplication,
    QAbstractButton,
    QComboBox,
    QDialog,
    QFrame,
    QWidget,
)


_INPUT_EVENT = struct.Struct("@llHHi")
_EV_KEY = 0x01
_EV_ABS = 0x03
_BTN_SOUTH = 0x130
_BTN_EAST = 0x131
_BTN_TL = 0x136
_BTN_TR = 0x137
_BTN_START = 0x13B
_BTN_DPAD_UP = 0x220
_BTN_DPAD_DOWN = 0x221
_BTN_DPAD_LEFT = 0x222
_BTN_DPAD_RIGHT = 0x223
_ABS_X = 0x00
_ABS_Y = 0x01
_ABS_HAT0X = 0x10
_ABS_HAT0Y = 0x11
_AXIS_THRESHOLD = 16_000

_DIRECTION_KEYS = {
    _BTN_DPAD_UP: Qt.Key.Key_Up,
    _BTN_DPAD_DOWN: Qt.Key.Key_Down,
    _BTN_DPAD_LEFT: Qt.Key.Key_Left,
    _BTN_DPAD_RIGHT: Qt.Key.Key_Right,
}


class GamepadNavigationController(QObject):
    """Map D-pad/stick to focus navigation and South/East to select/back.

    Devices are opened read-only and never grabbed, so game engines and the
    desktop retain their normal input stream. Callers can suspend actions
    while a game session is active while continuing to monitor presence.
    """

    navigation_available_changed = pyqtSignal(bool)
    help_requested = pyqtSignal()

    def __init__(
        self,
        *,
        game_running: Callable[[], bool],
        input_directory: str = "/dev/input",
        parent=None,
    ) -> None:
        super().__init__(parent)
        self.game_running = game_running
        self.input_directory = input_directory
        self._devices: dict[int, dict] = {}
        self._event_nodes: tuple[str, ...] = ()
        self._held_directions: dict[tuple[int, int], Qt.Key] = {}
        self._available = False
        self._repeat_timer = QTimer(self)
        self._repeat_timer.setInterval(280)
        self._repeat_timer.timeout.connect(self._repeat_navigation)
        self._focus_ring: QFrame | None = None
        self._focus_ring_timer = QTimer(self)
        self._focus_ring_timer.setSingleShot(True)
        self._focus_ring_timer.setInterval(1_500)
        self._focus_ring_timer.timeout.connect(self._hide_focus_ring)
        self._navigation_notifier = QTimer(self)
        self._navigation_notifier.setInterval(2_000)
        self._navigation_notifier.timeout.connect(self._retry_unavailable_devices)

    @property
    def navigation_available(self) -> bool:
        return self._available

    def set_event_nodes(self, nodes) -> None:
        self._event_nodes = tuple(nodes or ())
        wanted = {
            os.path.join(self.input_directory, str(node))
            for node in self._event_nodes
            if str(node).startswith("event") and str(node)[5:].isdigit()
        }
        current = {device["path"] for device in self._devices.values()}
        for fd, device in tuple(self._devices.items()):
            if device["path"] not in wanted:
                self._close_device(fd)
        for path in sorted(wanted - current):
            self._open_device(path)
        if self._event_nodes:
            self._navigation_notifier.start()
        else:
            self._navigation_notifier.stop()
        self._update_availability()

    def set_gameplay_active(self, active: bool) -> None:
        """Cancel repeats and focus decoration as soon as a game starts."""
        if not active:
            return
        self._held_directions.clear()
        self._repeat_timer.stop()
        self._focus_ring_timer.stop()
        self._hide_focus_ring()

    def _open_device(self, path: str) -> None:
        try:
            fd = os.open(path, os.O_RDONLY | os.O_NONBLOCK | os.O_CLOEXEC)
            notifier = QSocketNotifier(fd, QSocketNotifier.Type.Read, self)
            notifier.activated.connect(lambda *_args, device_fd=fd: self._read_device(device_fd))
        except (OSError, RuntimeError):
            try:
                os.close(fd)
            except (OSError, UnboundLocalError):
                pass
            return
        self._devices[fd] = {
            "path": path,
            "notifier": notifier,
            "buffer": bytearray(),
            "axes": {},
        }

    def _close_device(self, fd: int) -> None:
        device = self._devices.pop(fd, None)
        if device is None:
            return
        self._held_directions = {
            source: key for source, key in self._held_directions.items()
            if source[0] != fd
        }
        if not self._held_directions:
            self._repeat_timer.stop()
        notifier = device["notifier"]
        try:
            notifier.setEnabled(False)
            notifier.deleteLater()
        except RuntimeError:
            pass
        try:
            os.close(fd)
        except OSError:
            pass

    def _read_device(self, fd: int) -> None:
        device = self._devices.get(fd)
        if device is None:
            return
        try:
            while True:
                chunk = os.read(fd, _INPUT_EVENT.size * 32)
                if not chunk:
                    self._close_device(fd)
                    self._update_availability()
                    return
                device["buffer"].extend(chunk)
                while len(device["buffer"]) >= _INPUT_EVENT.size:
                    raw = bytes(device["buffer"][:_INPUT_EVENT.size])
                    del device["buffer"][:_INPUT_EVENT.size]
                    _seconds, _microseconds, event_type, code, value = _INPUT_EVENT.unpack(raw)
                    self._handle_input_event(fd, event_type, code, value)
        except BlockingIOError:
            return
        except OSError:
            self._close_device(fd)
            self._update_availability()

    def _handle_input_event(self, fd: int, event_type: int, code: int, value: int) -> None:
        if event_type == _EV_KEY:
            direction = _DIRECTION_KEYS.get(code)
            if direction is not None:
                source = (fd, code)
                if value == 1:
                    if not self.game_running():
                        self._navigate(direction)
                        self._held_directions[source] = direction
                        self._repeat_timer.start()
                elif value == 0:
                    self._held_directions.pop(source, None)
                    if not self._held_directions:
                        self._repeat_timer.stop()
                return
            if value != 1 or self.game_running():
                return
            if code == _BTN_SOUTH:
                self._activate_focused_widget()
            elif code == _BTN_EAST:
                self._send_key(Qt.Key.Key_Escape)
            elif code == _BTN_TL:
                self._focus_next_control(forward=False)
            elif code == _BTN_TR:
                self._focus_next_control(forward=True)
            elif code == _BTN_START:
                self.help_requested.emit()
            return
        if event_type != _EV_ABS:
            return
        if code not in (_ABS_X, _ABS_Y, _ABS_HAT0X, _ABS_HAT0Y):
            return
        device = self._devices.get(fd)
        if device is None:
            return
        previous = device["axes"].get(code, 0)
        if code in (_ABS_HAT0X, _ABS_HAT0Y):
            current = -1 if value < 0 else 1 if value > 0 else 0
        else:
            current = -1 if value < -_AXIS_THRESHOLD else 1 if value > _AXIS_THRESHOLD else 0
        device["axes"][code] = current
        source = (fd, code)
        if current == 0:
            self._held_directions.pop(source, None)
            if not self._held_directions:
                self._repeat_timer.stop()
            return
        if code in (_ABS_X, _ABS_HAT0X):
            direction = Qt.Key.Key_Left if current < 0 else Qt.Key.Key_Right
        else:
            direction = Qt.Key.Key_Up if current < 0 else Qt.Key.Key_Down
        self._held_directions[source] = direction
        if self.game_running():
            self._held_directions.clear()
            self._repeat_timer.stop()
            return
        if current == previous:
            return
        self._navigate(direction)
        self._repeat_timer.start()

    def _active_window(self) -> QWidget | None:
        if self.game_running():
            return None
        app = QApplication.instance()
        window = app.activeWindow() if app is not None else None
        root = self.parent()
        if (
            app is None
            or app.applicationState() != Qt.ApplicationState.ApplicationActive
            or window is None
            or not isinstance(root, QWidget)
        ):
            return None
        candidate = window
        while candidate is not None:
            if candidate is root:
                return window
            candidate = candidate.parentWidget()
        return None

    @staticmethod
    def _send_key_to(widget: QWidget, key: Qt.Key) -> bool:
        press = QKeyEvent(QKeyEvent.Type.KeyPress, key, Qt.KeyboardModifier.NoModifier)
        press.setAccepted(False)
        QApplication.sendEvent(widget, press)
        release = QKeyEvent(QKeyEvent.Type.KeyRelease, key, Qt.KeyboardModifier.NoModifier)
        release.setAccepted(False)
        QApplication.sendEvent(widget, release)
        return press.isAccepted()

    def _focused_widget(self, window: QWidget) -> QWidget | None:
        app = QApplication.instance()
        focus = app.focusWidget() if app is not None else None
        root = self.parent()
        candidate = focus
        belongs_to_app_window = False
        while candidate is not None:
            if candidate is window or candidate is root:
                belongs_to_app_window = True
                break
            candidate = candidate.parentWidget()
        if focus is None or not belongs_to_app_window:
            return None
        return focus

    def _navigate(self, key: Qt.Key) -> None:
        window = self._active_window()
        if window is None:
            return
        focus = self._focused_widget(window)
        if focus is not None:
            before = self._navigation_state(focus)
            accepted = self._send_key_to(focus, key)
            if accepted and before != self._navigation_state(focus):
                self._show_focus_ring(window, focus)
                return
        self._focus_spatial_neighbor(window, focus, key)
        self._show_focus_ring(window, self._focused_widget(window))

    @staticmethod
    def _navigation_state(widget: QWidget) -> tuple:
        """Snapshot common native-navigation state to detect list boundaries."""
        state = []
        for attribute in ("currentIndex", "value", "cursorPosition"):
            getter = getattr(widget, attribute, None)
            if callable(getter):
                try:
                    state.append((attribute, getter()))
                except RuntimeError:
                    pass
        for attribute in ("horizontalScrollBar", "verticalScrollBar"):
            getter = getattr(widget, attribute, None)
            if callable(getter):
                try:
                    bar = getter()
                    state.append((attribute, bar.value()))
                except (AttributeError, RuntimeError):
                    pass
        text_cursor = getattr(widget, "textCursor", None)
        if callable(text_cursor):
            try:
                state.append(("textCursor", text_cursor().position()))
            except RuntimeError:
                pass
        return tuple(state)

    def _focus_next_control(self, *, forward: bool) -> None:
        window = self._active_window()
        if window is None:
            return
        window.focusNextPrevChild(bool(forward))
        self._show_focus_ring(window, self._focused_widget(window))

    def _focus_spatial_neighbor(
        self,
        window: QWidget,
        focus: QWidget | None,
        key: Qt.Key,
    ) -> None:
        app = QApplication.instance()
        if app is None:
            return
        candidates = [
            widget
            for widget in window.findChildren(QWidget)
            if widget.isVisible()
            and widget.isEnabled()
            and widget.focusPolicy() & Qt.FocusPolicy.TabFocus
            and widget.window() is window
            and widget is not focus
        ]
        if not candidates:
            window.focusNextPrevChild(key in (Qt.Key.Key_Right, Qt.Key.Key_Down))
            return
        if focus is None:
            window.focusNextPrevChild(key in (Qt.Key.Key_Right, Qt.Key.Key_Down))
            return

        origin = focus.mapToGlobal(focus.rect().center())
        directional = []
        for candidate in candidates:
            point = candidate.mapToGlobal(candidate.rect().center())
            dx, dy = point.x() - origin.x(), point.y() - origin.y()
            if key == Qt.Key.Key_Right and dx > 4:
                primary, cross = dx, abs(dy)
            elif key == Qt.Key.Key_Left and dx < -4:
                primary, cross = -dx, abs(dy)
            elif key == Qt.Key.Key_Down and dy > 4:
                primary, cross = dy, abs(dx)
            elif key == Qt.Key.Key_Up and dy < -4:
                primary, cross = -dy, abs(dx)
            else:
                continue
            directional.append((primary + cross * 1.7, candidate))
        if directional:
            _, target = min(directional, key=lambda entry: entry[0])
            target.setFocus(Qt.FocusReason.TabFocusReason)
        else:
            window.focusNextPrevChild(key in (Qt.Key.Key_Right, Qt.Key.Key_Down))

    def _activate_focused_widget(self) -> None:
        window = self._active_window()
        if window is None:
            return
        focus = self._focused_widget(window)
        if focus is None:
            window.focusNextPrevChild(True)
            focus = self._focused_widget(window)
        if focus is not None:
            self._show_focus_ring(window, focus)
            if isinstance(focus, QAbstractButton):
                focus.click()
            elif isinstance(focus, QComboBox):
                focus.showPopup()
            else:
                self._send_key_to(focus, Qt.Key.Key_Return)

    def _send_key(self, key: Qt.Key) -> None:
        window = self._active_window()
        if window is None:
            return
        focus = self._focused_widget(window)
        if key == Qt.Key.Key_Escape and isinstance(focus, QComboBox):
            popup = focus.view()
            if popup is not None and popup.isVisible():
                focus.hidePopup()
                return
        if key == Qt.Key.Key_Escape and isinstance(window, QDialog):
            window.reject()
            return
        target = focus or window
        self._send_key_to(target, key)

    def _show_focus_ring(self, window: QWidget, focus: QWidget | None) -> None:
        if focus is None or sip.isdeleted(focus):
            return
        if self._focus_ring is None or sip.isdeleted(self._focus_ring):
            self._focus_ring = QFrame(window)
            self._focus_ring.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
            self._focus_ring.setFocusPolicy(Qt.FocusPolicy.NoFocus)
            self._focus_ring.setStyleSheet(
                "QFrame { background: transparent; border: 2px solid #58D6A2; "
                "border-radius: 6px; }"
            )
        elif self._focus_ring.parentWidget() is not window:
            self._focus_ring.setParent(window)
        top_left = focus.mapTo(window, QPoint(0, 0))
        self._focus_ring.setGeometry(focus.rect().translated(top_left).adjusted(-3, -3, 3, 3))
        self._focus_ring.show()
        self._focus_ring.raise_()
        self._focus_ring_timer.start()

    def _hide_focus_ring(self) -> None:
        if self._focus_ring is not None and not sip.isdeleted(self._focus_ring):
            self._focus_ring.hide()

    def _repeat_navigation(self) -> None:
        if self.game_running():
            self._held_directions.clear()
            self._repeat_timer.stop()
            return
        if self._held_directions:
            self._navigate(next(reversed(self._held_directions.values())))

    def _retry_unavailable_devices(self) -> None:
        self.set_event_nodes(self._event_nodes)

    def _update_availability(self) -> None:
        available = bool(self._devices)
        if available == self._available:
            return
        self._available = available
        self.navigation_available_changed.emit(available)

    def stop(self) -> None:
        self._navigation_notifier.stop()
        self._repeat_timer.stop()
        self._held_directions.clear()
        self._focus_ring_timer.stop()
        self._hide_focus_ring()
        for fd in tuple(self._devices):
            self._close_device(fd)
        self._update_availability()


__all__ = ["GamepadNavigationController"]
