"""
Intelligent PC Game Save File Detection Engine.
Integrates with host Ludusavi CLI when present, and provides a built-in heuristics
engine supporting Wine, Proton, and UMU prefix hierarchies.
"""

import os
import shutil
import json
import subprocess
from dataclasses import dataclass, field
from typing import List, Dict, Optional
from core.logger import get_logger

logger = get_logger("SaveDetector")


@dataclass
class SaveLocation:
    """Represents a discovered game save folder or file set."""
    display_name: str
    path: str
    is_directory: bool
    file_count: int = 0
    total_size_bytes: int = 0
    last_modified: float = 0.0
    relative_to_prefix: str = ""
    source: str = "heuristics"  # "ludusavi_cli" or "heuristics"


def _scan_folder_stats(folder_path: str) -> tuple[int, int, float]:
    """Calculate file count, total size in bytes, and latest modification time."""
    count = 0
    total_size = 0
    latest_mtime = 0.0
    
    if not os.path.exists(folder_path):
        return 0, 0, 0.0

    if os.path.isfile(folder_path):
        try:
            stat = os.stat(folder_path)
            return 1, stat.st_size, stat.st_mtime
        except OSError:
            return 1, 0, 0.0

    for root, _, files in os.walk(folder_path):
        for f in files:
            fp = os.path.join(root, f)
            try:
                stat = os.stat(fp)
                count += 1
                total_size += stat.st_size
                if stat.st_mtime > latest_mtime:
                    latest_mtime = stat.st_mtime
            except OSError:
                continue

    return count, total_size, latest_mtime


class LudusaviDetector:
    """Finds exact game save paths inside isolated Wine/UMU prefixes and game folders."""

    @staticmethod
    def is_cli_available() -> bool:
        """Check if official ludusavi executable is installed on host."""
        return shutil.which("ludusavi") is not None

    @classmethod
    def detect_saves(cls, game_name: str, game_path: str, steam_id: str = "") -> List[SaveLocation]:
        """Detect all save files/folders for a game using CLI with heuristic fallback."""
        results: List[SaveLocation] = []

        # 1. Try host Ludusavi CLI if available
        if cls.is_cli_available():
            cli_results = cls._detect_via_cli(game_name, game_path, steam_id)
            if cli_results:
                logger.info(f"Discovered {len(cli_results)} save locations via Ludusavi CLI for '{game_name}'")
                return cli_results

        # 2. Built-in heuristics engine (Supports UMU / Wine / Proton prefixes)
        results = cls._detect_via_heuristics(game_name, game_path, steam_id)
        logger.info(f"Discovered {len(results)} save locations via heuristics for '{game_name}'")
        return results

    @classmethod
    def _detect_via_cli(cls, game_name: str, game_path: str, steam_id: str) -> List[SaveLocation]:
        """Run ludusavi CLI in preview API mode."""
        locations: List[SaveLocation] = []
        prefix_path = os.path.join(game_path, "prefix")
        if not os.path.isdir(prefix_path):
            prefix_path = os.path.join(game_path, "prefix", "pfx")

        cmd = ["ludusavi", "backup", "--preview", "--api"]
        if os.path.isdir(prefix_path):
            cmd.extend(["--wine-prefix", prefix_path])
        if steam_id and steam_id.isdigit() and int(steam_id) > 0:
            cmd.extend(["--steam-id", str(steam_id)])
        cmd.append(game_name)

        try:
            res = subprocess.run(cmd, capture_output=True, text=True, timeout=10)
            if res.returncode == 0 and res.stdout.strip():
                data = json.loads(res.stdout)
                games = data.get("games", {})
                game_info = games.get(game_name, {})
                files = game_info.get("files", {})
                for file_path, file_meta in files.items():
                    if os.path.exists(file_path):
                        count, size, mtime = _scan_folder_stats(file_path)
                        rel = os.path.relpath(file_path, prefix_path) if os.path.isdir(prefix_path) else os.path.basename(file_path)
                        locations.append(SaveLocation(
                            display_name=os.path.basename(file_path),
                            path=file_path,
                            is_directory=os.path.isdir(file_path),
                            file_count=count,
                            total_size_bytes=size,
                            last_modified=mtime,
                            relative_to_prefix=rel,
                            source="ludusavi_cli",
                        ))
        except Exception as e:
            logger.warning(f"Ludusavi CLI scan failed: {e}")

        return locations

    @classmethod
    def _detect_via_heuristics(cls, game_name: str, game_path: str, steam_id: str) -> List[SaveLocation]:
        """Deep scan Wine/UMU prefix hierarchy and game root for candidate save directories."""
        locations: List[SaveLocation] = []
        seen_paths = set()

        # Identify Wine/Proton prefixes
        candidate_prefixes = [
            os.path.join(game_path, "prefix"),
            os.path.join(game_path, "prefix", "pfx"),
            game_path
        ]

        normalized_game_names = {
            game_name.strip(),
            game_name.lower().strip(),
            "".join(c for c in game_name if c.isalnum()),
            game_name.replace(" ", "_"),
            game_name.replace(" ", "-"),
            game_name.replace(":", ""),
            game_name.replace("'", ""),
        }

        for prefix in candidate_prefixes:
            if not os.path.isdir(prefix):
                continue

            drive_c = os.path.join(prefix, "drive_c")
            if not os.path.isdir(drive_c):
                continue

            users_dir = os.path.join(drive_c, "users")
            if not os.path.isdir(users_dir):
                continue

            # Check every user directory created by Wine/Proton/UMU (e.g. 'steamuser', '$USER', 'Public')
            for user_name in os.listdir(users_dir):
                user_root = os.path.join(users_dir, user_name)
                if not os.path.isdir(user_root) or user_name.lower() in ("public", "all users"):
                    continue

                # Standard Windows Save Locations inside user folder
                search_targets = [
                    # Saved Games
                    os.path.join(user_root, "Saved Games"),
                    # Documents and My Games
                    os.path.join(user_root, "Documents", "My Games"),
                    os.path.join(user_root, "Documents"),
                    os.path.join(user_root, "My Documents"),
                    # AppData Roaming
                    os.path.join(user_root, "AppData", "Roaming"),
                    # AppData Local & LocalLow
                    os.path.join(user_root, "AppData", "Local"),
                    os.path.join(user_root, "AppData", "LocalLow"),
                    os.path.join(user_root, "Local Settings", "Application Data"),
                ]

                for base_dir in search_targets:
                    if not os.path.isdir(base_dir):
                        continue

                    try:
                        for entry in os.listdir(base_dir):
                            entry_path = os.path.join(base_dir, entry)
                            clean_entry = "".join(c for c in entry if c.isalnum()).lower()
                            
                            # Match game title or subfolders containing game title
                            matched = False
                            for gn in normalized_game_names:
                                if gn and (gn.lower() in entry.lower() or (len(gn) > 3 and clean_entry in gn.lower()) or (len(clean_entry) > 3 and gn.lower() in clean_entry)):
                                    matched = True
                                    break

                            if matched and entry_path not in seen_paths:
                                count, size, mtime = _scan_folder_stats(entry_path)
                                if count > 0:
                                    seen_paths.add(entry_path)
                                    rel = os.path.relpath(entry_path, prefix)
                                    locations.append(SaveLocation(
                                        display_name=f"{entry} ({os.path.basename(base_dir)})",
                                        path=entry_path,
                                        is_directory=os.path.isdir(entry_path),
                                        file_count=count,
                                        total_size_bytes=size,
                                        last_modified=mtime,
                                        relative_to_prefix=rel,
                                        source="heuristics"
                                    ))
                    except OSError:
                        continue

            # Steam Userdata inside prefix (e.g. Steam / Goldberg / UMU emulators)
            steam_userdata_candidates = [
                os.path.join(drive_c, "Program Files (x86)", "Steam", "userdata"),
                os.path.join(drive_c, "Program Files", "Steam", "userdata"),
                os.path.join(drive_c, "users", "Public", "Documents", "Steam"),
                os.path.join(drive_c, "ProgramData", "Steam"),
            ]
            for s_cand in steam_userdata_candidates:
                if os.path.isdir(s_cand) and s_cand not in seen_paths:
                    count, size, mtime = _scan_folder_stats(s_cand)
                    if count > 0:
                        seen_paths.add(s_cand)
                        rel = os.path.relpath(s_cand, prefix)
                        locations.append(SaveLocation(
                            display_name="Steam Emulated Cloud / Userdata",
                            path=s_cand,
                            is_directory=True,
                            file_count=count,
                            total_size_bytes=size,
                            last_modified=mtime,
                            relative_to_prefix=rel,
                            source="heuristics"
                        ))

        # 3. Check inside game directory itself for common standalone save directories
        game_local_save_names = [
            "save", "saves", "savedata", "SaveData", "SaveGames", "saved_games",
            "Saves", "savegame", "SaveGame", "storage", "Profile", "profiles"
        ]
        for sname in game_local_save_names:
            local_save = os.path.join(game_path, sname)
            if os.path.isdir(local_save) and local_save not in seen_paths:
                count, size, mtime = _scan_folder_stats(local_save)
                if count > 0:
                    seen_paths.add(local_save)
                    locations.append(SaveLocation(
                        display_name=f"Game Folder / {sname}",
                        path=local_save,
                        is_directory=True,
                        file_count=count,
                        total_size_bytes=size,
                        last_modified=mtime,
                        relative_to_prefix=sname,
                        source="heuristics"
                    ))

        # Fallback: if no specific directory matched, check whole drive_c/users
        if not locations:
            prefix_users = os.path.join(game_path, "prefix", "drive_c", "users")
            if os.path.isdir(prefix_users):
                count, size, mtime = _scan_folder_stats(prefix_users)
                if count > 0:
                    locations.append(SaveLocation(
                        display_name="Full Wine/UMU User Directory (Fallback)",
                        path=prefix_users,
                        is_directory=True,
                        file_count=count,
                        total_size_bytes=size,
                        last_modified=mtime,
                        relative_to_prefix="drive_c/users",
                        source="heuristics"
                    ))

        # Sort with most recently modified and non-empty locations first
        locations.sort(key=lambda loc: loc.last_modified, reverse=True)
        return locations
