"""Global hotkey listener for in-game screenshot, recording, and replay triggers."""

import threading
import time
from typing import Dict, Optional, Tuple
from PyQt6.QtCore import QObject, pyqtSignal

from core.logger import get_logger

logger = get_logger("GlobalHotkeys")


def _resolve_keysym(disp, key_name: str) -> Tuple[int, int]:
    """Resolve a key name string to (keysym, keycode) via Xlib."""
    from Xlib import XK

    # Aliases for common Qt / X11 key names
    key_aliases = {
        "pageup": "Page_Up",
        "pagedown": "Page_Down",
        "scrolllock": "Scroll_Lock",
        "capslock": "Caps_Lock",
        "numlock": "Num_Lock",
        "space": "space",
        "esc": "Escape",
        "prnt": "Print",
        "printscreen": "Print",
        "snapshot": "Print",
    }

    clean_name = key_name.strip()
    lookup_candidates = [
        clean_name,
        key_aliases.get(clean_name.lower(), clean_name),
        clean_name.upper(),
        clean_name.lower(),
        clean_name.capitalize(),
    ]

    for candidate in lookup_candidates:
        sym = XK.string_to_keysym(candidate)
        if sym != 0:
            kc = disp.keysym_to_keycode(sym)
            if kc != 0:
                return sym, kc

    return 0, 0


def _parse_key_sequence(key_str: str) -> Tuple[str, int]:
    """Parse a portable Qt key sequence string into (bare_key_name, modifier_mask).

    Examples:
      "F9"             -> ("F9", 0)
      "Ctrl+F9"        -> ("F9", ControlMask)
      "Ctrl+Shift+Y"   -> ("Y", ControlMask | ShiftMask)
      "Ctrl+Alt+S"     -> ("S", ControlMask | Mod1Mask)
    """
    try:
        from Xlib import X
        mod_map = {
            "ctrl":    X.ControlMask,
            "control": X.ControlMask,
            "shift":   X.ShiftMask,
            "alt":     X.Mod1Mask,
            "mod1":    X.Mod1Mask,
            "meta":    X.Mod4Mask,
            "super":   X.Mod4Mask,
            "win":     X.Mod4Mask,
            "mod4":    X.Mod4Mask,
        }
    except ImportError:
        return key_str, 0

    parts = [p.strip() for p in key_str.split("+") if p.strip()]
    if not parts:
        return "", 0

    mods = 0
    key_name = parts[-1]
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
        self._bindings_lock = threading.Lock()
        self._bindings: Dict[str, str] = {}   # "F9" / "Ctrl+Shift+Y" -> action_name
        self._dirty = False

    def register_hotkey(self, key_name: str, action: str):
        """Register a key sequence string (e.g. 'F9', 'Ctrl+Shift+Y') to emit an action."""
        if not key_name or key_name.upper() in ("NONE", ""):
            return
        with self._bindings_lock:
            self._bindings[key_name.strip()] = action
            self._dirty = True

    def clear_bindings(self):
        with self._bindings_lock:
            self._bindings.clear()
            self._dirty = True

    def update_bindings(self, new_bindings: Dict[str, str]):
        """Replace all current bindings with a new mapping."""
        with self._bindings_lock:
            self._bindings = {k.strip(): v for k, v in new_bindings.items() if k and k.upper() not in ("NONE", "")}
            self._dirty = True

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
            from Xlib import X, display
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
            active_grab_keycodes: set = set()

            # Bitmask of actual modifiers to evaluate (Shift, Ctrl, Alt, Meta/Super)
            relevant_modifiers = X.ShiftMask | X.ControlMask | X.Mod1Mask | X.Mod4Mask
            # Modifier permutations to grab so hotkeys work with NumLock, CapsLock, AltGr active
            extra_modifiers = [
                0,
                X.Mod2Mask,                           # NumLock
                X.LockMask,                           # CapsLock
                X.Mod2Mask | X.LockMask,
                X.Mod5Mask,                           # AltGr / ISO Level3
                X.Mod2Mask | X.Mod5Mask,
                X.LockMask | X.Mod5Mask,
                X.Mod2Mask | X.LockMask | X.Mod5Mask,
            ]

            def _rebind_keys():
                nonlocal keycode_to_action, active_grab_keycodes
                # Ungrab previous keys
                for kc in active_grab_keycodes:
                    try:
                        root.ungrab_key(kc, X.AnyModifier)
                    except Exception:
                        pass
                active_grab_keycodes.clear()
                keycode_to_action.clear()

                with self._bindings_lock:
                    current_items = list(self._bindings.items())

                for key_str, action in current_items:
                    try:
                        key_name, req_mods = _parse_key_sequence(key_str)
                        sym, kc = _resolve_keysym(disp, key_name)
                        if kc != 0:
                            for extra in extra_modifiers:
                                root.grab_key(kc, (req_mods & relevant_modifiers) | extra, True, X.GrabModeAsync, X.GrabModeAsync)
                            active_grab_keycodes.add(kc)
                            keycode_to_action[(kc, req_mods & relevant_modifiers)] = action
                            logger.info(f"Registered global hotkey '{key_str}' (keycode {kc}, mods {req_mods:#x}) for '{action}'")
                        else:
                            logger.warning(f"Could not resolve keycode for hotkey '{key_str}' (key '{key_name}')")
                    except Exception as ex:
                        logger.debug(f"Failed to grab key '{key_str}': {ex}")

                try:
                    disp.sync()
                except Exception:
                    pass

            _rebind_keys()

            while self._running:
                # Check for dynamic binding updates from the UI thread
                if self._dirty:
                    self._dirty = False
                    _rebind_keys()

                if disp.pending_events() > 0:
                    event = disp.next_event()
                    if event.type == X.KeyPress:
                        # Extract only meaningful modifiers
                        clean_mods = event.state & relevant_modifiers
                        action = keycode_to_action.get((event.detail, clean_mods))
                        if action:
                            logger.info(f"Global hotkey triggered: keycode {event.detail}, mods {clean_mods:#x} -> '{action}'")
                            self.hotkey_triggered.emit(action)
                else:
                    time.sleep(0.03)

            # Cleanup grabs upon termination
            try:
                for kc in active_grab_keycodes:
                    root.ungrab_key(kc, X.AnyModifier)
                disp.sync()
            except Exception:
                pass

        except Exception as e:
            logger.debug(f"Global hotkey loop ended: {e}")
