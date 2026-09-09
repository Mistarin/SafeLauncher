"""
Real-Time Game Achievement State File Locator and Watcher.
Detects achievement state files across Wine/Proton emulators (Goldberg, CODEX,
RUNE, FLT, Linux Native) and monitors unlock events in real-time via QFileSystemWatcher.
"""

from __future__ import annotations

import os
import json
import configparser
from pathlib import Path
from typing import Optional, Dict, Any, List

from PyQt6.QtCore import QObject, QFileSystemWatcher, pyqtSignal, QTimer

from core.logger import get_logger

logger = get_logger("AchievementWatcher")


def resolve_achievement_prefix(prefix_path: str = "", game_path: str = "") -> Optional[Path]:
    """Resolve the Wine prefix used by a game.

    ``proton_path`` in the game record is normally a Proton/UMU runtime path,
    not a WINEPREFIX.  Only accept an explicitly supplied path when it has a
    Wine prefix shape; otherwise derive the prefix owned by the game.
    """
    candidates: List[Path] = []
    supplied = Path(str(prefix_path)).expanduser() if prefix_path else None
    if supplied and supplied.is_dir() and (supplied / "drive_c").is_dir():
        candidates.append(supplied)

    if game_path:
        game_dir = Path(game_path).expanduser()
        candidates.extend((game_dir / "prefix", game_dir / "prefix" / "pfx"))

    for candidate in candidates:
        try:
            if candidate.is_dir() and (candidate / "drive_c").is_dir():
                return candidate.resolve()
        except OSError:
            continue
    return None


def achievement_state_candidates(prefix_path: str, game_path: str, app_id: str) -> List[Path]:
    """Return known achievement-state locations in discovery order.

    The generic Windows locations are included because some games and
    emulators store their state beside normal saves rather than in the
    emulator's standard directory.  We only look for known state filenames;
    this does not recursively scan user data.
    """
    if not app_id or str(app_id).strip() in ("", "0"):
        return []

    app_id = str(app_id).strip()
    prefix = resolve_achievement_prefix(prefix_path, game_path)
    game_dir = Path(game_path).resolve() if game_path else None
    candidates: List[Path] = []

    def add(path: Path) -> None:
        if path not in candidates:
            candidates.append(path)

    state_names = ("achievements.json", "stats.json", "achievements.ini", "stats.ini",
                   "achievement.json", "achievement.ini")

    def add_state_dir(directory: Path) -> None:
        for filename in state_names:
            add(directory / filename)

    if prefix and prefix.is_dir():
        users = prefix / "drive_c/users"
        for user_root in users.glob("*"):
            if not user_root.is_dir() or user_root.name.lower() in ("public", "all users", "default", "default user"):
                continue
            # Generic Windows save roots.  These are intentionally limited to
            # the game's folder name elsewhere by the save detector; here the
            # app-id variants are the unambiguous achievement forms.
            for save_root in ("Goldberg SteamEmu Saves", "GSE Saves", "FLT", "Steam/CODEX", "Steam/RUNE"):
                add_state_dir(user_root / "AppData/Roaming" / save_root / app_id)
            for root in (
                user_root / "Documents",
                user_root / "Documents/My Games",
                user_root / "AppData/Roaming",
                user_root / "AppData/Local",
            ):
                add_state_dir(root)
                # Common layout: <Windows root>/<game name>/<state file>.
                # Probe only one level; never recursively crawl user data.
                if root.is_dir():
                    try:
                        for game_folder in root.iterdir():
                            if game_folder.is_dir():
                                add_state_dir(game_folder)
                    except OSError:
                        pass

        for save_root in ("Steam/CODEX", "Steam/RUNE", "Goldberg SteamEmu Saves", "GSE Saves"):
            add_state_dir(prefix / "drive_c/users/Public/Documents" / save_root / app_id)

    if game_dir and game_dir.is_dir():
        for directory in (
            game_dir / "steam_settings",
            game_dir / f"steam_settings/{app_id}",
            game_dir / f"Goldberg SteamEmu Saves/{app_id}",
            game_dir / f"GSE Saves/{app_id}",
            game_dir / "SmartSteamEmu",
            game_dir,
        ):
            add_state_dir(directory)

        # Goldberg-compatible builds can redirect the emulator save folder
        # through configs.user.ini.  Read only this small settings file.
        settings_file = game_dir / "steam_settings/configs.user.ini"
        if settings_file.is_file():
            try:
                settings = configparser.ConfigParser()
                settings.optionxform = str
                settings.read(settings_file, encoding="utf-8")
                save_folder = settings.get("user::saves", "saves_folder_name", fallback="").strip()
                if save_folder:
                    add_state_dir(game_dir / save_folder / app_id)
                    if prefix:
                        for user_root in (prefix / "drive_c/users/steamuser", prefix / "drive_c/users/Public"):
                            add_state_dir(user_root / "AppData/Roaming" / save_folder / app_id)
            except (configparser.Error, OSError):
                pass

    for save_root in ("Goldberg SteamEmu Saves", "GSE Saves"):
        add_state_dir(Path.home() / ".local/share" / save_root / app_id)
    return candidates


def locate_achievements_file(prefix_path: str, game_path: str, app_id: str) -> Optional[Path]:
    """
    Locates the active achievements file for a given Wine prefix, game directory, and AppID.
    Returns Path if an existing file is found, or None.
    """
    if not app_id or str(app_id).strip() in ("", "0"):
        return None

    existing = [p for p in achievement_state_candidates(prefix_path, game_path, app_id) if p.is_file()]
    if not existing:
        return None
    # Emulators commonly replace files atomically. Prefer non-empty state and
    # then the newest file when several real candidates exist.
    def priority(path: Path) -> tuple[int, int, int]:
        try:
            size = path.stat().st_size
            return (int(size > 2), path.stat().st_mtime_ns, size)
        except OSError:
            return (0, 0, 0)
    non_empty = [p for p in existing if priority(p)[0]]
    if non_empty:
        return max(non_empty, key=priority)
    # Ignore an empty host-global file left by older SafeLauncher versions
    # when the game has its own prefix. It is not evidence of game state.
    if resolve_achievement_prefix(prefix_path, game_path):
        return None
    return max(existing, key=priority)


def ensure_achievement_watch_target(prefix_path: str, game_path: str, app_id: str) -> Optional[Path]:
    """
    Return an existing or predicted target without creating synthetic state.
    """
    found = locate_achievements_file(prefix_path, game_path, app_id)
    if found:
        return found

    candidates = achievement_state_candidates(prefix_path, game_path, app_id)
    return candidates[0] if candidates else None


def parse_achievements_state(file_path: Path) -> Dict[str, float]:
    """
    Parse an achievements state file (JSON or INI).
    Returns mapping of {api_name: unlock_timestamp}.
    """
    if not file_path or not file_path.is_file():
        return {}

    results: Dict[str, float] = {}

    try:
        content = file_path.read_text(encoding="utf-8", errors="ignore").strip()
        if not content:
            return {}

        # Case 1: JSON format (Goldberg). INI files also start with ``[``;
        # only claim that branch when the complete document parses as JSON.
        data = None
        if content.startswith(("{", "[")):
            try:
                data = json.loads(content)
            except json.JSONDecodeError:
                data = None
        if data is not None:
            def collect(items: Any, nested: bool = False, achievement_context: bool = False) -> None:
                if isinstance(items, list):
                    for item in items:
                        if isinstance(item, dict):
                            collect(item, nested=True, achievement_context=achievement_context)
                    return
                if isinstance(items, dict):
                    for key, val in items.items():
                        api_name = str(key).strip()
                        if isinstance(val, dict):
                            if val.get("earned") or val.get("unlocked") or val.get("achieved"):
                                raw_ts = val.get("earned_time", val.get("unlock_time", val.get("timestamp", 0.0)))
                                try:
                                    results[api_name] = float(raw_ts or 1.0)
                                except (TypeError, ValueError):
                                    results[api_name] = 1.0
                            elif nested or api_name.lower() in ("achievements", "unlocks", "user_achievements"):
                                collect(val, nested=True, achievement_context=achievement_context or api_name.lower() in ("achievements", "unlocks", "user_achievements"))
                        elif isinstance(val, bool) and val:
                            if achievement_context or api_name.lower().startswith(("ach_", "achievement_", "unlock_")):
                                results[api_name] = 1.0
                        elif (achievement_context or api_name.lower().startswith(("ach_", "achievement_", "unlock_"))) and isinstance(val, (int, float)) and not isinstance(val, bool) and val > 0:
                            results[api_name] = float(val)

            collect(data)
            return results

        # Case 2: INI format (CODEX / RUNE / FLT / SSE).  A section called
        # ``Stats`` commonly contains counters such as money, kills, or play
        # time; treating every positive counter as an achievement creates
        # permanent false unlocks.  Only achievement sections are authoritative
        # (or explicitly achievement-named keys in mixed sections).
        cfg = configparser.ConfigParser()
        cfg.optionxform = str
        cfg.read_string(content)
        for section in cfg.sections():
            section_name = section.lower()
            achievement_section = "achieve" in section_name or "unlock" in section_name
            mixed_section = "stats" in section_name
            if achievement_section or mixed_section:
                for key, val in cfg.items(section):
                    key_str = key.strip()
                    key_name = key_str.lower()
                    if mixed_section and not achievement_section and not key_name.startswith(("ach_", "achievement_", "unlock_")):
                        continue
                    val_str = val.strip()
                    lowered = val_str.lower()
                    try:
                        numeric = float(val_str)
                    except ValueError:
                        numeric = 0.0
                    if lowered in ("1", "true", "yes", "on") or numeric > 0:
                        results[key_str] = numeric if numeric > 0 else 1.0
    except Exception as e:
        logger.debug(f"Error parsing achievement state file {file_path}: {e}")

    return results


class AchievementWatcher(QObject):
    """
    Watches an achievement state file in real-time during a game session.
    Emits achievement_unlocked signal whenever new achievements are unlocked.
    """
    achievement_unlocked = pyqtSignal(int, str, dict)  # game_id, app_id, {api_name, unlock_time}
    state_refreshed = pyqtSignal(int, str, dict)       # game_id, app_id, all_unlocked_dict

    def __init__(self, game_id: int, app_id: str, prefix_path: str, game_path: str, parent=None):
        super().__init__(parent)
        self.game_id = game_id
        self.app_id = str(app_id).strip()
        self.prefix_path = prefix_path
        self.game_path = game_path

        self.watch_file: Optional[Path] = None
        self.known_unlocked: Dict[str, float] = {}
        self._watched_dirs: set[str] = set()

        self.watcher = QFileSystemWatcher(self)
        self.watcher.fileChanged.connect(self._on_file_changed)
        self.watcher.directoryChanged.connect(self._on_directory_changed)

        # Polling fallback timer (every 10s) in case inotify misses cross-filesystem events
        self.poll_timer = QTimer(self)
        self.poll_timer.timeout.connect(self.check_updates)

    def start(self):
        """Locate file, load initial state, and begin watching."""
        if not self.app_id:
            return

        target_file = ensure_achievement_watch_target(self.prefix_path, self.game_path, self.app_id)
        self.watch_file = target_file

        # Watch every already-existing candidate directory, not just the
        # predicted Goldberg path.  This catches games whose state file is
        # created in Documents/AppData after launch.
        for candidate in achievement_state_candidates(self.prefix_path, self.game_path, self.app_id):
            parent = candidate.parent
            if parent.is_dir() and str(parent) not in self._watched_dirs:
                self.watcher.addPath(str(parent))
                self._watched_dirs.add(str(parent))

        if target_file and target_file.is_file():
            self.known_unlocked = parse_achievements_state(target_file)
            self.watcher.addPath(str(target_file))
            logger.info(f"Achievement watcher hooked to {target_file} ({len(self.known_unlocked)} initially unlocked)")
        elif target_file and target_file.parent.is_dir():
            # Watch parent directory for file creation
            self.watcher.addPath(str(target_file.parent))
            logger.info(f"Achievement watcher monitoring folder {target_file.parent} for achievement file creation")
        else:
            logger.info(
                "Achievement watcher waiting for a state file (AppID %s); no placeholder was created.",
                self.app_id,
            )

        self.poll_timer.start(10000)

    def stop(self):
        """Stop watching and cleanup."""
        self.poll_timer.stop()
        if self.watcher.files():
            self.watcher.removePaths(self.watcher.files())
        if self.watcher.directories():
            self.watcher.removePaths(self.watcher.directories())
        logger.debug(f"Achievement watcher stopped for game {self.game_id} (AppID {self.app_id})")

    def _on_directory_changed(self, path: str):
        """Directory modification handler (file created)."""
        found = locate_achievements_file(self.prefix_path, self.game_path, self.app_id)
        if found:
            self.watch_file = found
            self._reattach_file()
            self.check_updates()
            # Some emulators create then populate a file in separate writes.
            # Re-read once after the filesystem event instead of permanently
            # accepting an intermediate empty/partial parse.
            QTimer.singleShot(250, self.check_updates)

    def _on_file_changed(self, path: str):
        """File modification handler (real-time inotify)."""
        # Re-add path because some text editors/emulators replace inodes atomically on write
        self._reattach_file()
        self.check_updates()
        QTimer.singleShot(250, self.check_updates)

    def _reattach_file(self) -> None:
        """Re-add a file watch after an atomic rename/replacement."""
        if self.watch_file and self.watch_file.is_file() and str(self.watch_file) not in self.watcher.files():
            self.watcher.addPath(str(self.watch_file))
        for candidate in achievement_state_candidates(self.prefix_path, self.game_path, self.app_id):
            parent = candidate.parent
            if parent.is_dir() and str(parent) not in self._watched_dirs:
                self.watcher.addPath(str(parent))
                self._watched_dirs.add(str(parent))

    def check_updates(self):
        """Compare current state file against known unlocked items."""
        if not self.watch_file or not self.watch_file.is_file():
            # Try relocating in case it was created in an alternate path
            found = locate_achievements_file(self.prefix_path, self.game_path, self.app_id)
            if found and found.is_file():
                self.watch_file = found
                self._reattach_file()
            else:
                return

        current_state = parse_achievements_state(self.watch_file)
        newly_unlocked: Dict[str, float] = {}

        for api_name, unlock_time in current_state.items():
            if api_name not in self.known_unlocked:
                newly_unlocked[api_name] = unlock_time
                self.known_unlocked[api_name] = unlock_time

        if newly_unlocked:
            logger.info(f"New achievements unlocked for game {self.game_id} (AppID {self.app_id}): {list(newly_unlocked.keys())}")
            for api_name, unlock_time in newly_unlocked.items():
                self.achievement_unlocked.emit(self.game_id, self.app_id, {
                    "api_name": api_name,
                    "unlock_time": unlock_time,
                })
            self.state_refreshed.emit(self.game_id, self.app_id, self.known_unlocked)
