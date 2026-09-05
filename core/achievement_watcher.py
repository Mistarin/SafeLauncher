"""
Real-Time Game Achievement State File Locator and Watcher.
Detects achievement state files across Wine/Proton emulators (Goldberg, CODEX,
RUNE, FLT, Linux Native) and monitors unlock events in real-time via QFileSystemWatcher.
"""

from __future__ import annotations

import os
import glob
import json
import configparser
from pathlib import Path
from typing import Optional, Dict, Any, List

from PyQt6.QtCore import QObject, QFileSystemWatcher, pyqtSignal, QTimer

from core.logger import get_logger

logger = get_logger("AchievementWatcher")


def locate_achievements_file(prefix_path: str, game_path: str, app_id: str) -> Optional[Path]:
    """
    Locates the active achievements file for a given Wine prefix, game directory, and AppID.
    Returns Path if an existing file is found, or None.
    """
    if not app_id or str(app_id).strip() in ("", "0"):
        return None

    app_id = str(app_id).strip()
    prefix = Path(prefix_path).resolve() if prefix_path else None
    game_dir = Path(game_path).resolve() if game_path else None

    search_patterns = []

    # 1. Wine Prefix User AppData (Goldberg Steam Emu - Default & Most Common)
    if prefix and prefix.is_dir():
        search_patterns.extend([
            prefix / "drive_c/users/*/AppData/Roaming/Goldberg SteamEmu Saves" / app_id / "achievements.json",
            prefix / "drive_c/users/Public/Documents/Steam/CODEX" / app_id / "achievements.ini",
            prefix / "drive_c/users/Public/Documents/Steam/RUNE" / app_id / "achievements.ini",
            prefix / "drive_c/users/*/AppData/Roaming/FLT" / app_id / "achievements.ini",
            prefix / "drive_c/users/*/AppData/Roaming/FLT" / app_id / "stats.ini",
            prefix / "drive_c/users/*/AppData/Roaming/Steam/CODEX" / app_id / "achievements.ini",
        ])

    # 2. Game Installation Directory (Local overrides & portable setups)
    if game_dir and game_dir.is_dir():
        search_patterns.extend([
            game_dir / "steam_settings/achievements.json",
            game_dir / f"steam_settings/{app_id}/achievements.json",
            game_dir / f"Goldberg SteamEmu Saves/{app_id}/achievements.json",
            game_dir / "SmartSteamEmu/achievements.ini",
        ])

    # 3. Linux Native fallback
    search_patterns.append(
        Path.home() / ".local/share/Goldberg SteamEmu Saves" / app_id / "achievements.json"
    )

    # Evaluate patterns in order
    for pattern in search_patterns:
        matches = glob.glob(str(pattern))
        for match in matches:
            p = Path(match)
            if p.is_file():
                return p

    return None


def ensure_achievement_watch_target(prefix_path: str, game_path: str, app_id: str) -> Path:
    """
    Ensures a target achievements file exists or returns the predicted location so
    QFileSystemWatcher can attach immediately before game launch.
    """
    found = locate_achievements_file(prefix_path, game_path, app_id)
    if found:
        return found

    app_id = str(app_id).strip()
    prefix = Path(prefix_path).resolve() if prefix_path else None

    # Default to standard Goldberg Wine prefix path
    if prefix and prefix.is_dir():
        # Check if steamuser exists, otherwise use first user directory
        users_dir = prefix / "drive_c" / "users"
        user_name = "steamuser"
        if users_dir.is_dir():
            for u in users_dir.iterdir():
                if u.is_dir() and u.name.lower() not in ("public", "all users", "default", "default user"):
                    user_name = u.name
                    break
        target_dir = prefix / "drive_c" / "users" / user_name / "AppData" / "Roaming" / "Goldberg SteamEmu Saves" / app_id
    else:
        target_dir = Path.home() / ".local/share" / "Goldberg SteamEmu Saves" / app_id

    target_dir.mkdir(parents=True, exist_ok=True)
    target_file = target_dir / "achievements.json"
    if not target_file.exists():
        try:
            target_file.write_text("{}", encoding="utf-8")
        except Exception as e:
            logger.debug(f"Could not pre-seed empty achievements.json: {e}")

    return target_file


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

        # Case 1: JSON format (Goldberg)
        if content.startswith("{"):
            data = json.loads(content)
            if isinstance(data, dict):
                for key, val in data.items():
                    api_name = str(key).strip()
                    if isinstance(val, dict):
                        earned = bool(val.get("earned", False))
                        ts = float(val.get("earned_time", 0.0) or 0.0)
                        if earned:
                            results[api_name] = ts
                    elif isinstance(val, bool) and val:
                        results[api_name] = 1.0
                    elif isinstance(val, (int, float)) and val > 0:
                        results[api_name] = float(val)
            return results

        # Case 2: INI format (CODEX / RUNE / FLT / SSE)
        cfg = configparser.ConfigParser()
        cfg.optionxform = str
        cfg.read_string(content)
        for section in cfg.sections():
            if "achieve" in section.lower() or "stats" in section.lower():
                for key, val in cfg.items(section):
                    key_str = key.strip()
                    val_str = val.strip()
                    if val_str in ("1", "true", "True") or (val_str.isdigit() and int(val_str) > 0):
                        results[key_str] = 1.0
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

        if target_file and target_file.is_file():
            self.known_unlocked = parse_achievements_state(target_file)
            self.watcher.addPath(str(target_file))
            logger.info(f"Achievement watcher hooked to {target_file} ({len(self.known_unlocked)} initially unlocked)")
        elif target_file:
            # Watch parent directory for file creation
            self.watcher.addPath(str(target_file.parent))
            logger.info(f"Achievement watcher monitoring folder {target_file.parent} for achievement file creation")

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
        if self.watch_file and self.watch_file.is_file():
            if str(self.watch_file) not in self.watcher.files():
                self.watcher.addPath(str(self.watch_file))
            self.check_updates()

    def _on_file_changed(self, path: str):
        """File modification handler (real-time inotify)."""
        # Re-add path because some text editors/emulators replace inodes atomically on write
        if self.watch_file and self.watch_file.is_file() and str(self.watch_file) not in self.watcher.files():
            self.watcher.addPath(str(self.watch_file))
        self.check_updates()

    def check_updates(self):
        """Compare current state file against known unlocked items."""
        if not self.watch_file or not self.watch_file.is_file():
            # Try relocating in case it was created in an alternate path
            found = locate_achievements_file(self.prefix_path, self.game_path, self.app_id)
            if found and found.is_file():
                self.watch_file = found
                if str(found) not in self.watcher.files():
                    self.watcher.addPath(str(found))
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
