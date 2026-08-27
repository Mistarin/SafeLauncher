"""
Intelligent PC Game Save File Detection Engine.
Integrates with host Ludusavi CLI when present (managed automatically by
core.ludusavi_installer), and provides a built-in heuristics engine supporting
Wine, Proton, and UMU prefix hierarchies.

CLI and heuristic results are merged: ludusavi knows exact, curated save
locations; heuristics additionally catch install-dir saves and emulator
directories that no database can know about.
"""

import os
import shutil
import subprocess
import re
import json
from dataclasses import dataclass, asdict
from typing import List, Optional, Dict
from core.logger import get_logger

logger = get_logger("SaveDetector")

_CACHE_FILE = os.path.expanduser("~/.local/share/safelauncher/save_locations_cache.json")


def _load_persisted_cache() -> Dict[tuple, List['SaveLocation']]:
    cache: Dict[tuple, List['SaveLocation']] = {}
    if not os.path.exists(_CACHE_FILE):
        return cache
    try:
        with open(_CACHE_FILE, "r", encoding="utf-8") as f:
            raw = json.load(f)
            for k_str, locs_raw in raw.items():
                parts = tuple(k_str.split("|||"))
                if len(parts) == 3:
                    locs = [SaveLocation(**item) for item in locs_raw]
                    cache[parts] = locs
    except Exception as e:
        logger.debug(f"Could not load save location cache: {e}")
    return cache


def _save_persisted_cache(cache: Dict[tuple, List['SaveLocation']]):
    try:
        os.makedirs(os.path.dirname(_CACHE_FILE), exist_ok=True)
        raw = {}
        for k_tuple, locs in cache.items():
            k_str = f"{k_tuple[0]}|||{k_tuple[1]}|||{k_tuple[2]}"
            raw[k_str] = [asdict(l) for l in locs]
        with open(_CACHE_FILE + ".tmp", "w", encoding="utf-8") as f:
            json.dump(raw, f, indent=2)
        os.replace(_CACHE_FILE + ".tmp", _CACHE_FILE)
    except Exception as e:
        logger.debug(f"Could not save save location cache: {e}")


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


CLI_JUNK_SUFFIXES = (".log",)

# Components treated as structural Windows user-profile folders rather than
# app-specific data when clustering ludusavi file hits into save folders.
_PROFILE_STRUCTURAL = {
    "users",
    "appdata", "roaming", "local", "locallow",
    "documents", "my documents", "saved games",
    "local settings", "application data", "my games",
}


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
            return 0, 0, 0.0

    try:
        for root, _, files in os.walk(folder_path):
            for file in files:
                if any(file.endswith(sfx) for sfx in CLI_JUNK_SUFFIXES):
                    continue
                fp = os.path.join(root, file)
                try:
                    stat = os.stat(fp)
                    count += 1
                    total_size += stat.st_size
                    if stat.st_mtime > latest_mtime:
                        latest_mtime = stat.st_mtime
                except OSError:
                    pass
    except OSError:
        pass

    return count, total_size, latest_mtime


class LudusaviDetector:
    """Finds exact game save paths inside isolated Wine/UMU prefixes and game folders."""

    _LOCATION_CACHE: Dict[tuple, List[SaveLocation]] = _load_persisted_cache()

    @classmethod
    def clear_cache(cls, game_name: Optional[str] = None):
        """Clear discovery cache for a game or all games."""
        if game_name is None:
            cls._LOCATION_CACHE.clear()
        else:
            cls._LOCATION_CACHE = {k: v for k, v in cls._LOCATION_CACHE.items() if k[0] != game_name}
        _save_persisted_cache(cls._LOCATION_CACHE)

    # ------------------------------------------------------------------ #
    # Binary discovery                                                   #
    # ------------------------------------------------------------------ #

    @staticmethod
    def _ludusavi_binaries() -> List[str]:
        """Candidate executables: explicit env override > managed copy > PATH."""
        candidates = []
        override = os.environ.get("SAFELAUNCHER_LUDUSAVI", "").strip()
        if override:
            candidates.append(override)
        try:
            from core.ludusavi_installer import get_managed_ludusavi_path
            candidates.append(get_managed_ludusavi_path())
        except Exception:
            pass
        on_path = shutil.which("ludusavi")
        if on_path:
            candidates.append(on_path)
        return [c for c in candidates if c and os.path.isfile(c)]

    @classmethod
    def is_cli_available(cls) -> bool:
        """True when a usable ludusavi executable exists (no network involved)."""
        return bool(cls._ludusavi_binaries())

    @staticmethod
    def _run_json(binary: str, args: List[str], timeout: float = 15.0) -> Optional[dict]:
        """Run ludusavi and parse its JSON API payload; tolerate extra stderr text."""
        try:
            res = subprocess.run(
                [binary, *args],
                capture_output=True,
                text=True,
                timeout=timeout,
                errors="replace",
            )
        except (subprocess.TimeoutExpired, OSError) as e:
            logger.warning(f"Ludusavi invocation failed: {e}")
            return None

        out = (res.stdout or "").strip()
        if not out:
            return None
        # Some subcommands append human-readable notices after the JSON blob;
        # decode progressively from the end until a clean object is obtained.
        try:
            return json_loads_tolerant(out)
        except ValueError as e:
            logger.debug(f"Ludusavi returned unparsable output ({e}).")
            return None

    # ------------------------------------------------------------------ #
    # Detection                                                          #
    # ------------------------------------------------------------------ #

    @classmethod
    def detect_saves(cls, game_name: str, game_path: str, steam_id: str = "") -> List[SaveLocation]:
        """Detect save files/folders using ludusavi merged with local heuristics."""
        cache_key = (game_name, game_path, steam_id)
        cached = cls._LOCATION_CACHE.get(cache_key)
        if cached is not None:
            if not cached:
                # Fast-path for games known to have no saves yet
                return []
            # Fast-path: refresh file counts and mtimes in < 0.1ms without spawning CLI subprocess
            refreshed: List[SaveLocation] = []
            all_valid = True
            for loc in cached:
                if not os.path.exists(loc.path):
                    all_valid = False
                    break
                count, size, mtime = _scan_folder_stats(loc.path)
                refreshed.append(SaveLocation(
                    display_name=loc.display_name,
                    path=loc.path,
                    is_directory=loc.is_directory,
                    file_count=count,
                    total_size_bytes=size,
                    last_modified=mtime,
                    relative_to_prefix=loc.relative_to_prefix,
                    source=loc.source
                ))
            if all_valid:
                return refreshed

        cli_results: List[SaveLocation] = []
        if cls.is_cli_available():
            cli_results = cls._detect_via_cli(game_name, game_path, steam_id)
            if cli_results:
                logger.info(f"Discovered {len(cli_results)} save locations via Ludusavi CLI for '{game_name}'")

        results = cls._detect_via_heuristics(game_name, game_path, steam_id)

        # Merge with realpath de-duplication: Wine junction aliases
        # ("Local Settings/Application Data"), sibling user accounts and
        # prefix/pfx twins otherwise report identical data multiple times.
        seen = set()
        merged: List[SaveLocation] = []
        ordered = cli_results + [h for h in results if not (
            h.display_name.startswith("Full Wine/UMU User Directory") and cli_results)]
        for loc in ordered:
            try:
                key = os.path.realpath(loc.path)
            except OSError:
                key = loc.path
            if key in seen and not os.path.isfile(key):
                continue
            if key in seen:
                continue
            seen.add(key)
            merged.append(loc)

        merged.sort(key=lambda l: l.last_modified, reverse=True)

        # Ancestor suppression: a fuzzy heuristic hit that envelopes a curated
        # (or more specific) hit and holds no additional files is redundant —
        # e.g. AppData/Local/<Game> vs AppData/Local/<Game>/Saved.
        kept: List[SaveLocation] = []
        realpaths = {}
        for loc in merged:
            try:
                realpaths[id(loc)] = os.path.realpath(loc.path)
            except OSError:
                realpaths[id(loc)] = loc.path
        for loc in merged:
            covered = False
            for other in merged:
                if other is loc:
                    continue
                if realpaths[id(other)].startswith(realpaths[id(loc)] + os.sep) \
                        and other.file_count >= loc.file_count:
                    # The nested hit already accounts for every file the
                    # broader folder would contribute — drop the broader one.
                    covered = True
                    break
            if not covered:
                kept.append(loc)

        cls._LOCATION_CACHE[cache_key] = kept
        _save_persisted_cache(cls._LOCATION_CACHE)

        return kept

    @classmethod
    def _detect_via_cli(cls, game_name: str, game_path: str, steam_id: str) -> List[SaveLocation]:
        """Query ludusavi for this game's save locations (curated manifest)."""
        binaries = cls._ludusavi_binaries()
        if not binaries:
            return []
        binary = binaries[0]

        prefix_path = os.path.join(game_path, "prefix")
        if not os.path.isdir(prefix_path):
            prefix_path = os.path.join(game_path, "prefix", "pfx")
        prefix_known = os.path.isdir(prefix_path)

        # Identify a canonical title: current releases removed `--steam-id`
        # from `backup`; IDs resolve via `find --steam-id` first, which also
        # fixes casing mismatches between library titles and official names.
        candidate_titles: List[str] = []
        sid = str(steam_id).strip()
        if sid.isdigit():
            data = cls._run_json(binary, ["find", "--api", "--steam-id", sid])
            found = list((data or {}).get("games", {}).keys())
            if found:
                candidate_titles.append(found[0])
        normalized = cls._run_json(binary, ["find", "--api", "--normalized", "--", game_name])
        for t in (normalized or {}).get("games", {}):
            if t not in candidate_titles:
                candidate_titles.append(t)
        if game_name and game_name not in candidate_titles:
            candidate_titles.append(game_name)

        locations: List[SaveLocation] = []
        for title in candidate_titles[:4]:
            cmd = ["backup", "--preview", "--api"]
            if prefix_known:
                cmd.extend(["--wine-prefix", prefix_path])
            # Terminal "--" stops ludusavi parsing titles starting with "-"
            cmd.extend(["--", title])

            data = cls._run_json(binary, cmd)
            if not data:
                continue

            game_hits = {}
            for key, info in (data.get("games") or {}).items():
                if key.lower() == title.lower():
                    game_hits = info
                    break
            if not game_hits:
                game_hits = next(iter((data.get("games") or {}).values()), {})

            file_paths = list((game_hits or {}).get("files", {}).keys())
            meaningful = [
                p for p in file_paths
                if os.path.exists(p) and not p.lower().endswith(CLI_JUNK_SUFFIXES)
            ]
            if meaningful:
                locations.extend(cls._build_cli_locations(meaningful, prefix_path, prefix_known))
                break

        return locations

    @staticmethod
    def _location_root_for(file_path: str, prefix_path: str, prefix_known: bool) -> str:
        """Cluster an individual save file into its owning save-folder root."""
        parts = os.path.normpath(file_path).split(os.sep)
        if "users" in parts:
            u = parts.index("users")
            j = u + 2  # skip user account name
            while j < len(parts) and parts[j].lower() in _PROFILE_STRUCTURAL:
                j += 1
            # Include the vendor/app-specific folder(s) that follow.
            root_parts = parts[: min(j + 2, len(parts))]
            root = os.sep.join(root_parts)
            if os.path.isdir(root):
                return root
        return os.path.dirname(file_path)

    @classmethod
    def _build_cli_locations(cls, file_paths: List[str], prefix_path: str, prefix_known: bool) -> List[SaveLocation]:
        """Group ludusavi file hits into coherent SaveLocation entries."""
        reg_prefix_abs = os.path.abspath(prefix_path) if prefix_known else None
        roots_by_cluster = {}

        for p in file_paths:
            base = os.path.basename(p).lower()
            if base.endswith(".reg"):
                key = "__wine_registry__"
            elif prefix_known and os.path.dirname(os.path.abspath(p)) == reg_prefix_abs:
                # Loose prefix-root files: one entry per file (as historically).
                locations_key = f"__file__:{p}"
                roots_by_cluster.setdefault(locations_key, []).append(p)
                continue
            elif os.path.isdir(prefix_root := cls._location_root_for(p, prefix_path, prefix_known)):
                key = prefix_root
            else:
                key = os.path.dirname(p)
            roots_by_cluster.setdefault(key, []).append(p)

        locations: List[SaveLocation] = []
        rel_base = prefix_path if prefix_known else None
        for key, members in roots_by_cluster.items():
            if key == "__wine_registry__":
                for p in members:
                    count, size, mtime = _scan_folder_stats(p)
                    locations.append(SaveLocation(
                        display_name=f"Wine Registry ({os.path.basename(p)})",
                        path=p,
                        is_directory=False,
                        file_count=count,
                        total_size_bytes=size,
                        last_modified=mtime,
                        relative_to_prefix=(
                            os.path.relpath(p, rel_base) if rel_base and os.path.isabs(rel_base) else os.path.basename(p)
                        ),
                        source="ludusavi_cli",
                    ))
                continue

            root = key
            count, size, mtime = _scan_folder_stats(root)
            display = os.path.basename(root.rstrip(os.sep)) or root
            user_label = ""
            m = re.search(r"/users/([^/]+)/", root.replace("\\", "/") + "/")
            if m and m.group(1).lower() not in ("public", "all users"):
                user_label = m.group(1)
            if user_label:
                display = f"{display} [{user_label}]"
            locations.append(SaveLocation(
                display_name=display,
                path=root,
                is_directory=True,
                file_count=count,
                total_size_bytes=size,
                last_modified=mtime,
                relative_to_prefix=(
                    os.path.relpath(root, rel_base) if rel_base and os.path.isabs(rel_base) else root
                ),
                source="ludusavi_cli",
            ))
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

        # Terms commonly holding launcher/cache/temp noise rather than saves;
        # excluded from fuzzy matches to avoid pointless duplicates.
        non_save_terms = (
            "temp", "cache", "crash", "logs", "microsoft", "unrealengine",
            "openvr", "nvidia", "amd", "dxvk", "vulkan",
        )

        def name_matches(entry: str) -> bool:
            lower = entry.lower()
            if any(t in lower for t in non_save_terms):
                return False
            clean_entry = "".join(c for c in entry if c.isalnum()).lower()
            for gn in normalized_game_names:
                g = gn.lower()
                if gn and (g in lower or (len(g) > 3 and g in clean_entry)):
                    return True
                if len(clean_entry) > 3 and clean_entry in g:
                    return True
            return False

        def consider(entry_path: str, base_dir: str, prefix: str, label_extra: str = "") -> None:
            if entry_path in seen_paths or not os.path.isdir(entry_path):
                return
            count, size, mtime = _scan_folder_stats(entry_path)
            if count > 0:
                seen_paths.add(entry_path)
                entry = os.path.basename(entry_path)
                locations.append(SaveLocation(
                    display_name=f"{entry}{label_extra}",
                    path=entry_path,
                    is_directory=True,
                    file_count=count,
                    total_size_bytes=size,
                    last_modified=mtime,
                    relative_to_prefix=os.path.relpath(entry_path, prefix),
                    source="heuristics"
                ))

        for prefix in candidate_prefixes:
            if not os.path.isdir(prefix):
                continue

            drive_c = os.path.join(prefix, "drive_c")
            if not os.path.isdir(drive_c):
                continue

            users_dir = os.path.join(drive_c, "users")
            if not os.path.isdir(users_dir):
                continue

            # Check every user directory created by Wine/Proton/UMU
            for user_name in os.listdir(users_dir):
                user_root = os.path.join(users_dir, user_name)
                if not os.path.isdir(user_root) or user_name.lower() in ("public", "all users"):
                    continue

                # Standard Windows Save Locations inside user folder
                search_targets = [
                    os.path.join(user_root, "Saved Games"),
                    os.path.join(user_root, "Documents", "My Games"),
                    os.path.join(user_root, "Documents"),
                    os.path.join(user_root, "My Documents"),
                    os.path.join(user_root, "AppData", "Roaming"),
                    os.path.join(user_root, "AppData", "Local"),
                    os.path.join(user_root, "AppData", "LocalLow"),
                    os.path.join(user_root, "Local Settings", "Application Data"),
                ]

                for base_dir in search_targets:
                    if not os.path.isdir(base_dir):
                        continue

                    try:
                        entries = list(os.listdir(base_dir))
                    except OSError:
                        continue

                    for entry in entries:
                        entry_path = os.path.join(base_dir, entry)
                        if not os.path.isdir(entry_path):
                            continue
                        if name_matches(entry):
                            consider(entry_path, base_dir, prefix,
                                     f" ({os.path.basename(base_dir)})")
                            continue
                        # Publisher-nested layout: <root>/<Publisher>/<Game>.
                        # Look one level deeper when the top folder doesn't match.
                        try:
                            if os.path.isdir(entry_path) and not entry.startswith("."):
                                for sub in os.listdir(entry_path):
                                    if not name_matches(sub):
                                        continue
                                    sub_path = os.path.join(entry_path, sub)
                                    consider(sub_path, base_dir, prefix,
                                             f" ({entry})")
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
                        locations.append(SaveLocation(
                            display_name="Steam Emulated Cloud / Userdata",
                            path=s_cand,
                            is_directory=True,
                            file_count=count,
                            total_size_bytes=size,
                            last_modified=mtime,
                            relative_to_prefix=os.path.relpath(s_cand, prefix),
                            source="heuristics"
                        ))

        # 3. Check inside game directory itself for common standalone save
        # directories, including installs that nest one level deeper
        # (e.g. Kenshi stores saves in <install>/Kenshi/save).
        game_local_save_names = [
            "save", "saves", "savedata", "SaveData", "SaveGames", "saved_games",
            "Saves", "savegame", "SaveGame", "storage", "Profile", "profiles"
        ]
        lowered = {s.lower() for s in game_local_save_names}

        def game_dir_matches(dirname: str) -> bool:
            return dirname.lower() in lowered

        scanned_game_dirs = set()
        if os.path.isdir(game_path):
            try:
                top_level = sorted(os.listdir(game_path))
            except OSError:
                top_level = []

            for root_child in top_level:
                child_path = os.path.join(game_path, root_child)
                if not os.path.isdir(child_path) or root_child.startswith((".", "prefix")):
                    continue

                # Direct save folder, e.g. <install>/Saves
                candidates_here = [root_child] if game_dir_matches(root_child) else []
                if not candidates_here:
                    # Nested install layout, e.g. <install>/Kenshi/save
                    try:
                        candidates_here = [
                            s for s in os.listdir(child_path)
                            if s != "." and game_dir_matches(s)
                        ]
                        nested_parent = root_child
                    except OSError:
                        candidates_here = []
                        nested_parent = ""

                for sname in candidates_here:
                    if root_child == sname:
                        local_save = child_path
                    else:
                        local_save = os.path.join(child_path, sname)
                    if local_save in scanned_game_dirs or not os.path.isdir(local_save):
                        continue
                    count, size, mtime = _scan_folder_stats(local_save)
                    if count > 0:
                        scanned_game_dirs.add(local_save)
                        locations.append(SaveLocation(
                            display_name=f"Game Folder / {sname}",
                            path=local_save,
                            is_directory=True,
                            file_count=count,
                            total_size_bytes=size,
                            last_modified=mtime,
                            # Parity with the historical format: game-dir saves
                            # are keyed by their folder name in save manifests.
                            relative_to_prefix=sname,
                            source="heuristics"
                        ))

        # Fallback: if no specific directory matched, check whole drive_c/users
        # but ignore obvious noise subtrees (temp/cache/driver caches), so a
        # game with no saves on disk does not get them backed up as saves.
        if not locations:
            prefix_users = os.path.join(game_path, "prefix", "drive_c", "users")
            if os.path.isdir(prefix_users):
                def _filtered_stats(path: str) -> tuple[int, int, float]:
                    total_files, total_size, latest = 0, 0, 0.0
                    stack = [(path, False)]
                    while stack:
                        current, noisy = stack.pop()
                        try:
                            for child in os.listdir(current):
                                child_path = os.path.join(current, child)
                                if os.path.isdir(child_path):
                                    stack.append((child_path, noisy or any(
                                        t in child.lower() for t in (
                                            "temp", "cache", "crash", "logs",
                                            "openvr", "microsoft", "unrealengine",
                                            "nvidia", "amd", "dxvk", "vulkan",
                                        ))))
                                    continue
                                if noisy:
                                    continue
                                stt = os.stat(child_path)
                                total_files += 1
                                total_size += stt.st_size
                                latest = max(latest, stt.st_mtime)
                        except OSError:
                            continue
                    return total_files, total_size, latest

                count, size, mtime = _filtered_stats(prefix_users)
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


def json_loads_tolerant(text: str) -> dict:
    """Parse a JSON object possibly followed by trailing non-JSON output."""
    import json
    try:
        return json.loads(text)
    except json.JSONDecodeError as first_err:
        # Try trimming after the final closing brace of the first object.
        idx = text.find("}\n")
        if idx != -1:
            try:
                return json.loads(text[: idx + 1])
            except json.JSONDecodeError:
                pass
        raise first_err
