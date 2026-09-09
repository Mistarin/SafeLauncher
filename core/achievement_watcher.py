"""
Real-Time Game Achievement State File Locator and Watcher.
Detects achievement state files across Wine/Proton emulators (Goldberg, CODEX,
RUNE, FLT, Linux Native) and monitors unlock events in real-time via QFileSystemWatcher.
"""

from __future__ import annotations

import os
import json
import configparser
import math
import re
import time
from pathlib import Path
from typing import Optional, Dict, Any, List

from PyQt6.QtCore import QObject, QFileSystemWatcher, pyqtSignal, QTimer

from core.logger import get_logger
from core.achievement_models import AchievementStateParseResult

logger = get_logger("AchievementWatcher")

_MAX_STATE_BYTES = 8 * 1024 * 1024
_MAX_STATE_DEPTH = 16
_MAX_STATE_RECORDS = 10_000
_API_NAME_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$")
_APP_ID_RE = re.compile(r"^[0-9]{1,16}$")
_ACHIEVEMENT_CONTAINERS = frozenset({
    "achievements", "achievement", "unlocks", "user_achievements",
    "userachievements", "achievement_state", "achievement_states",
})


def _valid_api_name(value: Any) -> str:
    name = str(value or "").strip()
    return name if _API_NAME_RE.fullmatch(name) else ""


def _valid_app_id(value: Any) -> str:
    app_id = str(value or "").strip()
    return app_id if _APP_ID_RE.fullmatch(app_id) and app_id != "0" else ""


def _safe_unlock_time(raw: Any, observed_at: Optional[float] = None) -> float:
    """Normalize hostile/missing timestamps without rejecting an unlock."""
    observed = float(observed_at or time.time())
    try:
        value = float(raw)
    except (TypeError, ValueError, OverflowError):
        value = 0.0
    if not math.isfinite(value) or value <= 0 or value > observed + 86_400:
        return observed
    return value


def _achievement_data_roots() -> List[Path]:
    """Return user data roots used by Linux Steam emulators.

    Goldberg/GSE normally follows ``XDG_DATA_HOME`` and falls back to
    ``~/.local/share``.  Keeping both roots is important when the launcher
    was started from a desktop entry with a different environment than the
    game, or when an emulator was configured before XDG was changed.
    """
    roots: List[Path] = []
    configured = os.environ.get("XDG_DATA_HOME", "").strip()
    if configured:
        roots.append(Path(configured).expanduser())
    roots.append(Path.home() / ".local/share")

    unique: List[Path] = []
    for root in roots:
        if root not in unique:
            unique.append(root)
    return unique


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
    app_id = _valid_app_id(app_id)
    if not app_id:
        return []
    prefix = resolve_achievement_prefix(prefix_path, game_path)
    game_dir = Path(game_path).resolve() if game_path else None
    candidates: List[Path] = []
    configured_save_folders: List[str] = []

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
                    configured_save_folders.append(save_folder)
                    add_state_dir(game_dir / save_folder / app_id)
                    if prefix:
                        for user_root in (prefix / "drive_c/users/steamuser", prefix / "drive_c/users/Public"):
                            add_state_dir(user_root / "AppData/Roaming" / save_folder / app_id)
            except (configparser.Error, OSError):
                pass

    # The configured folder is checked in the user data roots as well as in
    # the game/prefix locations above.  A GSE config commonly changes the
    # default Goldberg folder name, so only checking the two defaults loses
    # the real state file and leaves the UI at 0 forever.
    save_roots: List[str] = []
    for save_root in configured_save_folders + ["Goldberg SteamEmu Saves", "GSE Saves"]:
        if save_root and save_root not in save_roots:
            save_roots.append(save_root)
    for data_root in _achievement_data_roots():
        for save_root in save_roots:
            add_state_dir(data_root / save_root / app_id)
    return candidates


def locate_achievements_file(prefix_path: str, game_path: str, app_id: str) -> Optional[Path]:
    """
    Locates the active achievements file for a given Wine prefix, game directory, and AppID.
    Returns Path if an existing file is found, or None.
    """
    if not _valid_app_id(app_id):
        return None

    existing = [p for p in achievement_state_candidates(prefix_path, game_path, app_id) if p.is_file()]
    if not existing:
        return None
    # Emulators commonly replace files atomically. Prefer a file that actually
    # contains an unlocked state over a newer non-achievement stats/schema
    # file, then prefer the newest candidate.  ``parse_achievements_state`` is
    # defined below but is available by the time this function is called.
    def priority(path: Path) -> tuple[int, int, int, int]:
        try:
            size = path.stat().st_size
            # If no file contains a parseable unlock, prefer the explicitly
            # named achievement file over a generic stats file. This keeps the
            # watcher attached to an empty bootstrap file until it is filled.
            name_score = 2 if "achieve" in path.stem.lower() else 1
            return (name_score, int(size > 2), path.stat().st_mtime_ns, size)
        except OSError:
            return (0, 0, 0, 0)

    parsed = []
    for path in existing:
        if parse_achievements_state(path):
            try:
                parsed.append((path, path.stat().st_mtime_ns))
            except OSError:
                continue
    if parsed:
        return max(parsed, key=lambda item: item[1])[0]

    non_empty = [p for p in existing if priority(p)[0]]
    if non_empty:
        return max(non_empty, key=priority)
    # Do not create placeholders, but do monitor a real empty file.  Returning
    # it lets the watcher observe the first subsequent atomic write.
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


def parse_achievements_state_detailed(file_path: Path) -> AchievementStateParseResult:
    """Parse a bounded, format-specific local achievement state file.

    The old parser recursively treated arbitrary positive values as unlocks.
    That turns ordinary game statistics into permanent achievements.  This
    parser only accepts explicit achievement containers/flags, validates API
    names, caps input size/depth, and reports malformed input to diagnostics.
    """
    if not file_path or not file_path.is_file():
        return AchievementStateParseResult(reason="state file is missing")

    try:
        stat = file_path.stat()
        if stat.st_size > _MAX_STATE_BYTES:
            return AchievementStateParseResult(reason="state file exceeds safety limit")
        content = file_path.read_text(encoding="utf-8", errors="strict").strip()
    except (OSError, UnicodeError) as exc:
        return AchievementStateParseResult(reason=f"state file unreadable: {exc}")
    if not content:
        return AchievementStateParseResult(format="empty", valid=True)

    observed_at = time.time()
    results: Dict[str, float] = {}
    record_count = 0
    truncated = False

    def add(name: Any, raw_time: Any = 0.0) -> None:
        nonlocal record_count, truncated
        api_name = _valid_api_name(name)
        if not api_name:
            return
        if record_count >= _MAX_STATE_RECORDS:
            truncated = True
            return
        record_count += 1
        stamp = _safe_unlock_time(raw_time, observed_at)
        old = results.get(api_name)
        results[api_name] = min(old, stamp) if old and stamp else (old or stamp)

    def is_unlocked(value: Any) -> bool:
        if isinstance(value, bool):
            return value
        if isinstance(value, (int, float)) and not isinstance(value, bool):
            return math.isfinite(float(value)) and value > 0
        if isinstance(value, str):
            return value.strip().lower() in {"1", "true", "yes", "on", "unlocked", "earned", "achieved"}
        return False

    def collect_json(items: Any, *, context: bool = False, depth: int = 0) -> None:
        if depth > _MAX_STATE_DEPTH:
            return
        if isinstance(items, list):
            for item in items:
                if isinstance(item, dict):
                    # Steam-like arrays use apiname/api_name plus achieved.
                    name = item.get("apiname", item.get("api_name", item.get("achievement_id")))
                    flag = item.get("achieved", item.get("unlocked", item.get("earned")))
                    if name is not None and is_unlocked(flag):
                        add(name, item.get("unlocktime", item.get("unlock_time", item.get("earned_time", item.get("timestamp", 0)))))
                    elif context:
                        collect_json(item, context=True, depth=depth + 1)
            return
        if not isinstance(items, dict):
            return
        for key, value in items.items():
            key_text = str(key).strip()
            key_lower = key_text.lower()
            if isinstance(value, dict):
                if is_unlocked(value.get("earned", value.get("unlocked", value.get("achieved", False)))):
                    add(key_text, value.get("earned_time", value.get("unlock_time", value.get("unlocktime", value.get("timestamp", 0)))))
                elif key_lower in _ACHIEVEMENT_CONTAINERS or context:
                    collect_json(value, context=True, depth=depth + 1)
            elif context and is_unlocked(value):
                add(key_text, value)
            elif not context and key_lower.startswith(("ach_", "achievement_", "unlock_")) and is_unlocked(value):
                add(key_text, value)

    # JSON is selected only if the whole document parses.  A malformed JSON
    # state file is not silently reinterpreted as a permissive INI document.
    if content.startswith(("{", "[")):
        try:
            data = json.loads(content)
        except json.JSONDecodeError as exc:
            # INI achievement formats conventionally begin with a section
            # header. A malformed object is invalid JSON; a leading '[' may
            # still be a valid INI document and gets its strict adapter below.
            if content.startswith("{"):
                return AchievementStateParseResult(format="json", reason=f"invalid JSON: {exc}")
            data = None
        if data is not None:
            if not isinstance(data, (dict, list)):
                return AchievementStateParseResult(format="json", valid=False, reason="JSON root is not an object or array")
            collect_json(data)
            return AchievementStateParseResult(
                state=results,
                format="json",
                valid=not truncated,
                reason="record limit reached" if truncated else "",
            )

    try:
        cfg = configparser.ConfigParser(interpolation=None, strict=True)
        cfg.optionxform = str
        cfg.read_string(content)
    except (configparser.Error, ValueError) as exc:
        return AchievementStateParseResult(format="ini", reason=f"invalid INI: {exc}")

    for section in cfg.sections():
        section_name = section.lower()
        achievement_section = "achieve" in section_name or "unlock" in section_name
        mixed_section = "stats" in section_name
        if not (achievement_section or mixed_section):
            continue
        for key, value in cfg.items(section):
            key_text = key.strip()
            key_lower = key_text.lower()
            if mixed_section and not achievement_section and not key_lower.startswith(("ach_", "achievement_", "unlock_")):
                continue
            if is_unlocked(value):
                add(key_text, value if str(value).strip().replace(".", "", 1).isdigit() else 0)
    return AchievementStateParseResult(
        state=results,
        format="ini",
        valid=not truncated,
        reason="record limit reached" if truncated else "",
    )


def parse_achievements_state(file_path: Path) -> Dict[str, float]:
    """Compatibility wrapper returning only parsed unlocks."""
    return parse_achievements_state_detailed(file_path).state


def filter_achievement_state(state: Dict[str, float], allowed_api_names: Optional[set[str]]) -> tuple[Dict[str, float], Dict[str, float]]:
    """Split observations into schema-backed and quarantined API names."""
    if not allowed_api_names:
        return dict(state or {}), {}
    known, unknown = {}, {}
    for name, stamp in (state or {}).items():
        (known if name in allowed_api_names else unknown)[name] = stamp
    return known, unknown


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

        # Also watch the stable roots.  The configured emulator directory may
        # not exist until the first achievement is written, so watching only a
        # candidate's parent would miss the nested directory creation event.
        # The polling fallback remains in place for files created outside
        # inotify's filesystem boundary.
        stable_roots: List[Path] = []
        if self.game_path:
            stable_roots.append(Path(self.game_path).expanduser())
        prefix = resolve_achievement_prefix(self.prefix_path, self.game_path)
        if prefix:
            stable_roots.append(prefix)
        stable_roots.extend(_achievement_data_roots())
        for root in stable_roots:
            if root.is_dir() and str(root) not in self._watched_dirs:
                self.watcher.addPath(str(root))
                self._watched_dirs.add(str(root))

        if target_file and target_file.is_file():
            initial_parse = parse_achievements_state_detailed(target_file)
            self.known_unlocked = initial_parse.state if initial_parse.valid else {}
            if not initial_parse.valid and initial_parse.reason:
                logger.debug("Achievement state unavailable at %s: %s", target_file, initial_parse.reason)
            self.watcher.addPath(str(target_file))
            logger.info(f"Achievement watcher hooked to {target_file} ({len(self.known_unlocked)} initially unlocked)")
            if self.known_unlocked:
                # Reconcile an already-populated emulator file immediately;
                # the background schema worker may still be starting and must
                # not be the only path that can persist local unlocks.
                self.state_refreshed.emit(self.game_id, self.app_id, self.known_unlocked)
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
        # Re-resolve on every poll, not only when the old path disappears.
        # Games may switch from a bootstrap/empty file to a prefix or GSE file
        # and may replace files atomically without producing a useful Qt
        # directory event.  The resolver prefers the newest non-empty state.
        found = locate_achievements_file(self.prefix_path, self.game_path, self.app_id)
        if found and found != self.watch_file:
            self.watch_file = found
            self._reattach_file()

        if not self.watch_file or not self.watch_file.is_file():
            return

        parsed = parse_achievements_state_detailed(self.watch_file)
        if not parsed.valid:
            if parsed.reason:
                logger.debug("Achievement state unavailable at %s: %s", self.watch_file, parsed.reason)
            return
        current_state = parsed.state
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
                    "provenance": "local_emulator",
                    "verified": False,
                    "source_format": "json" if self.watch_file.suffix.lower() == ".json" else "ini",
                    "source_path": str(self.watch_file),
                })
            self.state_refreshed.emit(self.game_id, self.app_id, self.known_unlocked)
