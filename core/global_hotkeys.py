"""Global hotkey listener for in-game screenshot, recording, and replay triggers."""

import threading
import time
from typing import Dict, Optional, Tuple
from PyQt6.QtCore import QObject, pyqtSignal

from core.logger import get_logger

logger = get_logger("GlobalHotkeys")

# Map Qt portable text modifier names -> Xlib modifier masks (filled at runtime)
_QT_MOD_NAMES = {
    "ctrl":  None,   # X.ControlMask
    "shift": None,   # X.ShiftMask
    "alt":   None,   # X.Mod1Mask
    "meta":  None,   # X.Mod4Mask / Super
}


def _parse_key_sequence(key_str: str) -> Tuple[str, int]:
    """Parse a portable Qt key sequence string into (xlib_key_name, modifier_mask).

    Examples:
      "F9"         -> ("F9", 0)
      "Ctrl+F9"    -> ("F9", ControlMask)
      "Ctrl+Shift+R" -> ("R", ControlMask | ShiftMask)
    """
    try:
        from Xlib import X
        mod_map = {
            "ctrl":  X.ControlMask,
            "shift": X.ShiftMask,
            "alt":   X.Mod1Mask,
            "meta":  X.Mod4Mask,
        }
    except ImportError:
        return key_str, 0

    parts = key_str.split("+")
    mods = 0
    key_name = parts[-1]          # last part is always the bare key
    for mod in parts[:-1]:
        mods |= mod_map.get(mod.lower(), 0)
    return key_name, mods


class GlobalHotkeyListener(QObject):
    """Background daemon thread listening for global hotkeys across fullscreen games and desktop."""
    hotkey_triggered = pyqtSignal(str)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._running = False
        self._thread: Optional[threading.Thread] = None
        self._bindings: Dict[str, str] = {}   # "F9" / "Ctrl+F9" -> action_name

    def register_hotkey(self, key_name: str, action: str):
        """Register a key sequence string (e.g. 'F9', 'Ctrl+F9') to emit an action."""
        if key_name and key_name.upper() not in ("NONE", ""):
            self._bindings[key_name] = action

    def clear_bindings(self):
        self._bindings.clear()

    def start(self):
        """Start global hotkey listener thread."""
        if self._running:
            return
        self._running = True
        self._thread = threading.Thread(target=self._run_loop, daemon=True, name="SafeLauncher-GlobalHotkeys")
        self._thread.start()

    def stop(self):
        self._running = False

    def _run_loop(self):
        try:
            from Xlib import X, display, XK
            import Xlib.threaded
        except ImportError as e:
            logger.warning(f"python-xlib not available for global hotkeys: {e}")
            return

        try:
            disp = display.Display()
            disp.set_error_handler(lambda err, req: None)
            root = disp.screen().root
            root.change_attributes(event_mask=X.KeyPressMask)

            # (keycode, required_mod_mask) -> action
            keycode_to_action: Dict[Tuple[int, int], str] = {}

            for key_str, action in self._bindings.items():
                try:
                    key_name, req_mods = _parse_key_sequence(key_str)
                    keysym = XK.string_to_keysym(key_name)
                    if keysym == 0:
                        # Try lower-case for single letters
                        keysym = XK.string_to_keysym(key_name.lower())
                    if keysym != 0:
                        kc = disp.keysym_to_keycode(keysym)
                        if kc != 0:
                            # Grab with modifier + NumLock/CapsLock permutations
                            extra_mods = (0, X.Mod2Mask, X.LockMask, X.Mod2Mask | X.LockMask)
                            for extra in extra_mods:
                                root.grab_key(kc, req_mods | extra, True, X.GrabModeAsync, X.GrabModeAsync)
                            keycode_to_action[(kc, req_mods)] = action
                            logger.info(f"Registered global hotkey '{key_str}' (keycode {kc}, mods {req_mods:#x}) for '{action}'")
                except Exception as ex:
                    logger.debug(f"Failed to grab key '{key_str}': {ex}")

            disp.sync()

            while self._running:
                if disp.pending_events() > 0:
                    event = disp.next_event()
                    if event.type == X.KeyPress:
                        # Strip NumLock / CapsLock from reported state
                        clean_mods = event.state & ~(X.Mod2Mask | X.LockMask)
                        action = keycode_to_action.get((event.detail, clean_mods))
                        if action:
                            logger.debug(f"Global hotkey triggered action: {action}")
                            self.hotkey_triggered.emit(action)
                else:
                    time.sleep(0.04)

            # Cleanup grabs
            try:
                for kc, mods in keycode_to_action:
                    root.ungrab_key(kc, X.AnyModifier)
                disp.sync()
            except Exception:
                pass

        except Exception as e:
            logger.debug(f"Global hotkey loop ended: {e}")
