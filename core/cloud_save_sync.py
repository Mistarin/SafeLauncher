"""
Automatic Cloud / Local Save Synchronization Engine for SafeLauncher.

Two interchangeable backends sit behind this single API:

* "convex"  — private cloud storage on personal Convex instance (core.cloud_backend),
              active when QSettings cloud_mode == "convex" and a site URL is configured.
* "local"   — the legacy watched-folder engine (Syncthing/Nextcloud/whatever).

Every public method degrades gracefully: a cloud failure logs and falls back
to the local folder behaviour rather than losing data.
"""

import os
import time
import json
import zipfile
import tempfile
from enum import Enum
from dataclasses import dataclass
from typing import List, Tuple, Optional
from PyQt6.QtCore import QSettings

from core.ludusavi_detector import LudusaviDetector, SaveLocation
from core.zip_backup import ZipBackupManager, _MANIFEST_NAME
from database import _APP_DATA_DIR
from core.logger import get_logger

logger = get_logger("CloudSaveSync")

DEFAULT_CLOUD_SAVES_DIR = os.path.join(_APP_DATA_DIR, "cloud_saves")


class SyncStatus(Enum):
    IN_SYNC = "in_sync"
    LOCAL_NEWER = "local_newer"
    CLOUD_ONLY = "cloud_only"
    CLOUD_NEWER = "cloud_newer"
    NO_SAVES = "no_saves"
    CLOUD_OFFLINE = "cloud_offline"


@dataclass
class SaveStats:
    exists: bool
    last_modified: float = 0.0
    size_bytes: int = 0
    file_count: int = 0
    display_path: str = ""


# --------------------------------------------------------------------------- #
# Backend dispatch                                                            #
# --------------------------------------------------------------------------- #

import threading

def cloud_mode() -> str:
    settings = QSettings("SafeLauncher", "SafeLauncher")
    mode = settings.value("cloud_mode", None)
    if mode is not None and str(mode).strip():
        return str(mode).strip()
    from core.cloud_backend import get_site_url
    url = get_site_url()
    # Only auto-activate when the stored URL looks complete: must start with
    # "https://" and have at least one dot after the scheme (i.e. a real host).
    # This prevents a leftover placeholder like "https://" or "https://example"
    # from silently enabling cloud sync and causing 6-second timeout storms.
    if url and url.startswith("https://") and "." in url[8:]:
        return "convex"
    return "local"


def set_cloud_mode(mode: str) -> None:
    if mode not in ("local", "convex"):
        raise ValueError(f"Unknown cloud mode: {mode}")
    QSettings("SafeLauncher", "SafeLauncher").setValue("cloud_mode", mode)


def backend_active() -> bool:
    """True when the Convex backend should serve sync operations."""
    if cloud_mode() != "convex":
        return False
    from core.cloud_backend import get_site_url
    return bool(get_site_url())


_backend_singleton = None
_LISTING_CACHE = {"ts": 0.0, "data": None}
_LISTING_LOCK = threading.Lock()


def _backend():
    global _backend_singleton
    if _backend_singleton is None:
        from core.cloud_backend import ConvexSaveBackend
        _backend_singleton = ConvexSaveBackend()
    return _backend_singleton


def _get_cloud_listing(force_refresh: bool = False, max_age_seconds: float = 30.0) -> dict:
    """Shared cloud listing with an explicit freshness policy.

    Callers declare the staleness they tolerate: launch-path decisions use the
    30 s default, while force_refresh=True (polls, diffs) always re-fetch. The
    clock is time.monotonic() so wall-clock changes (NTP jumps, suspend) can
    neither extend nor truncate the freshness window.
    """
    global _LISTING_CACHE
    import time
    now = time.monotonic()
    with _LISTING_LOCK:
        if not force_refresh and _LISTING_CACHE["data"] and (now - _LISTING_CACHE["ts"] < max_age_seconds):
            return _LISTING_CACHE["data"]
    try:
        data = _backend().list_games()
        with _LISTING_LOCK:
            _LISTING_CACHE = {"ts": now, "data": data}
        return data
    except Exception as e:
        logger.debug(f"Failed to fetch cloud game listing: {e}")
        # A stale listing must never masquerade as current cloud state —
        # serving one made offline clients report sync statuses from data
        # that could be hours old. Within the freshness window the cache is
        # still the best-known state; beyond it, the cloud is unreachable.
        with _LISTING_LOCK:
            if _LISTING_CACHE["data"] and (time.monotonic() - _LISTING_CACHE["ts"] < max_age_seconds):
                return _LISTING_CACHE["data"]
        raise


def _invalidate_cloud_listing():
    global _LISTING_CACHE, _backend_singleton
    with _LISTING_LOCK:
        _LISTING_CACHE = {"ts": 0.0, "data": None}
    if _backend_singleton is not None:
        _backend_singleton.invalidate_key_cache()


def reset_cloud_backend() -> None:
    """Reset the cloud backend singleton and cache (e.g. after credential updates)."""
    global _backend_singleton, _LISTING_CACHE
    with _LISTING_LOCK:
        _LISTING_CACHE = {"ts": 0.0, "data": None}
    _backend_singleton = None


def _clean_game_slug(name: str) -> str:
    """Normalize game title to alphanumeric slug, stripping release tags and symbols."""
    import re
    # Strip trailing release group tags and version strings.
    # Pattern covers: -GroupName, _GroupName, (GroupName), [GroupName] style tags
    # and common suffixes like vX.Y, Build.XXXXXXX, Early Access, etc.
    s = re.sub(
        r"[-_ ]+(?:"
        r"AnkerGames|SteamRIP|FitGirl|DODI|"
        r"Razor1911|CODEX|RUNE|FLT|TENOKE|"
        r"SKIDROW|EMPRESS|PLAZA|CPY|HOODLUM|"
        r"TiNYiSO|P2P|SiMPLEX|RELOADED|PROPER|"
        r"GOG|Repack|Portable|"
        r"Early[. ]Access|Build[.\d]+|"
        r"v\d+[\d.]*"
        r")$",
        "",
        name.strip(),
        flags=re.IGNORECASE,
    )
    return "".join(c for c in s.lower() if c.isalnum())


def resolve_name_key(game_name: str) -> str:
    """Pick the cloud key a game's saves live under.

    New uploads use the collision-proof key; games whose saves were uploaded
    before that scheme existed are still found under their legacy key until
    the first post-migration upload re-homes them. Also matches games across
    minor naming variations (release tags, punctuation differences, casing).
    """
    from core.cloud_backend import normalize_name_key, legacy_name_key
    key = normalize_name_key(game_name)
    try:
        listing = _get_cloud_listing()
        cloud_games = listing.get("games", [])
        keys = {g.get("nameKey") for g in cloud_games}
        if key in keys:
            return key

        legacy = legacy_name_key(game_name)
        if legacy and legacy != key and legacy in keys:
            return legacy

        # 1. Exact displayName match
        for g in cloud_games:
            if g.get("displayName") == game_name:
                return g.get("nameKey")

        # 2. Case-insensitive key or display name match
        gn_lower = game_name.lower().strip()
        key_lower = key.lower()
        for g in cloud_games:
            if g.get("nameKey", "").lower() == key_lower or g.get("displayName", "").lower() == gn_lower:
                return g.get("nameKey")

        # 3. Slug-based match (ignores dashes, spaces, release groups, and punctuation)
        target_slug = _clean_game_slug(game_name)
        if target_slug:
            for g in cloud_games:
                cand_slug_key = _clean_game_slug(g.get("nameKey", ""))
                cand_slug_disp = _clean_game_slug(g.get("displayName", ""))
                # Exact slug match against either nameKey or displayName slug
                if target_slug == cand_slug_key or target_slug == cand_slug_disp:
                    return g.get("nameKey")
                # Substring match only when both slugs are long AND the shorter
                # one covers at least 85% of the longer one — prevents
                # "doom" matching "doomsday", "battlefield" matching "battlefield2042",
                # etc.  Checked against both nameKey slug and displayName slug.
                if len(target_slug) >= 8:
                    for cand_slug in (cand_slug_key, cand_slug_disp):
                        if not cand_slug:
                            continue
                        shorter = min(len(target_slug), len(cand_slug))
                        longer = max(len(target_slug), len(cand_slug))
                        if shorter / longer >= 0.85 and (
                            target_slug in cand_slug or cand_slug in target_slug
                        ):
                            return g.get("nameKey")

    except Exception as e:
        logger.debug(f"Cloud listing unavailable for key resolution, using '{key}': {e}")
    return key


def match_cloud_game_to_library(name_key: str, display_name: str, all_games: list):
    """Find a library game record matching a cloud game's nameKey or displayName."""
    from core.cloud_backend import normalize_name_key, legacy_name_key
    target_slug = _clean_game_slug(display_name or name_key)
    target_key_slug = _clean_game_slug(name_key)

    for g in all_games:
        # 1. Exact name or key
        if g.name == name_key or g.name == display_name:
            return g
        # 2. Normalized key or legacy key
        norm_g = normalize_name_key(g.name)
        leg_g = legacy_name_key(g.name)
        if norm_g == name_key or leg_g == name_key:
            return g
        # 3. Case-insensitive
        if g.name.lower() == name_key.lower() or g.name.lower() == display_name.lower():
            return g

    # 4. Slug-based match
    if target_slug or target_key_slug:
        for g in all_games:
            g_slug = _clean_game_slug(g.name)
            if not g_slug:
                continue
            if g_slug == target_slug or g_slug == target_key_slug:
                return g
            # Substring match only when both slugs are long AND the shorter
            # one covers at least 85% of the longer one.
            for ts in (target_slug, target_key_slug):
                if not ts or len(ts) < 8:
                    continue
                shorter = min(len(ts), len(g_slug))
                longer = max(len(ts), len(g_slug))
                if shorter / longer >= 0.85 and (ts in g_slug or g_slug in ts):
                    return g
    return None



def _candidate_save_keys(game_name: str) -> list[str]:
    """Generate all possible key aliases for a game name to guarantee robust lookups.

    Intentionally does NOT call ``resolve_name_key`` here because that function
    may trigger a live cloud listing fetch (network I/O).  QSettings version-
    persistence functions are called from many contexts, including the Qt main
    thread; a hidden network call would block the UI for up to 6 seconds on a
    cache miss.  The cloud nameKey alias is handled at the call sites that
    already hold the resolved key (e.g. ``set_active_save_version`` is always
    called right after an upload/restore that has already resolved the key).
    """
    candidates = []
    if game_name:
        candidates.append(game_name)
        if game_name.lower() not in candidates:
            candidates.append(game_name.lower())
        slug = _clean_game_slug(game_name)
        if slug and slug not in candidates:
            candidates.append(slug)
        hyphenated = "".join(c if c.isalnum() else "-" for c in game_name.lower()).strip("-")
        while "--" in hyphenated:
            hyphenated = hyphenated.replace("--", "-")
        if hyphenated and hyphenated not in candidates:
            candidates.append(hyphenated)
    return candidates


def get_active_save_version(game_name: str) -> Optional[int]:
    """Retrieve locally activated cloud save generation for this game, if set.

    Checks candidates in priority order — the resolved cloud nameKey is tried
    first because it is always written by ``set_active_save_version``.  Other
    aliases exist for backwards compatibility with saves written before the
    canonical key scheme was introduced.
    """
    settings = QSettings("SafeLauncher", "SafeLauncher")
    for k_name in _candidate_save_keys(game_name):
        val = settings.value(f"active_save_ver_{k_name}", None)
        if val is not None:
            try:
                return int(val)
            except (ValueError, TypeError):
                pass
    return None


def get_active_cloud_top_version(game_name: str) -> Optional[int]:
    """Retrieve the highest cloud version known when the active save version was set."""
    settings = QSettings("SafeLauncher", "SafeLauncher")
    for k_name in _candidate_save_keys(game_name):
        val = settings.value(f"active_save_top_{k_name}", None)
        if val is not None:
            try:
                return int(val)
            except (ValueError, TypeError):
                pass
    return None


def set_active_save_version(game_name: str, version: Optional[int],
                            cloud_top_version: Optional[int] = None,
                            name_key: str = "") -> None:
    """Store or clear locally activated cloud save generation for this game.

    ``name_key`` is the resolved cloud nameKey (e.g. "the-witcher-3").  Pass
    it when the caller already holds the key to avoid a duplicate network
    lookup.  The key will be included in the written aliases so lookups via
    the cloud key always succeed.

    Writes under every candidate alias so lookups always succeed regardless
    of which key variant was used historically.  On clear (``version=None``),
    all candidate aliases are removed so no stale shadow entries remain that
    could be read as a phantom active version on subsequent checks.
    """
    settings = QSettings("SafeLauncher", "SafeLauncher")
    candidates = _candidate_save_keys(game_name)
    if name_key and name_key not in candidates:
        candidates.append(name_key)
    for k_name in candidates:
        k = f"active_save_ver_{k_name}"
        k_top = f"active_save_top_{k_name}"
        if version is None:
            settings.remove(k)
            settings.remove(k_top)
        else:
            settings.setValue(k, int(version))
            if cloud_top_version is not None:
                settings.setValue(k_top, int(cloud_top_version))

    if version is None:
        # Sweep for any lingering keys that might have been written under
        # candidate aliases that are not generated by the *current* game name
        # (e.g. after the game was renamed in the library).  This prevents
        # phantom "active version" reads from a previous naming scheme.
        # We only remove keys whose suffix exactly matches a candidate so we
        # do not accidentally clear unrelated games.
        candidates_set = set(candidates)
        all_keys = settings.allKeys()
        for qk in all_keys:
            for prefix in ("active_save_ver_", "active_save_top_"):
                if qk.startswith(prefix):
                    suffix = qk[len(prefix):]
                    if suffix in candidates_set:
                        settings.remove(qk)






class CloudSaveSyncEngine:
    """Manages comparison and bi-directional synchronization between local and cloud save files."""

    @staticmethod
    def get_cloud_root() -> str:
        """Get configured cloud saves root folder from QSettings or default."""
        settings = QSettings("SafeLauncher", "SafeLauncher")
        root = settings.value("cloud_saves_dir", DEFAULT_CLOUD_SAVES_DIR, type=str).strip()
        if not root:
            root = DEFAULT_CLOUD_SAVES_DIR
        os.makedirs(root, exist_ok=True)
        return root

    @classmethod
    def get_cloud_save_path(cls, game_name: str) -> str:
        """Get the destination zip archive path for a given game in the cloud root."""
        clean_name = "".join(c for c in game_name if c.isalnum() or c in "-_ ").strip() or "game"
        game_folder = os.path.join(cls.get_cloud_root(), clean_name)
        os.makedirs(game_folder, exist_ok=True)
        return os.path.join(game_folder, "save_cloud.zip")

    # ------------------------------------------------------------------ #
    # Stats / conflict detection                                         #
    # ------------------------------------------------------------------ #

    @classmethod
    def get_local_save_stats(cls, game_name: str, game_path: str, steam_id: str = "") -> Tuple[SaveStats, List[SaveLocation]]:
        """Scan and calculate aggregate stats for all detected local save locations."""
        locations = LudusaviDetector.detect_saves(game_name, game_path, steam_id)
        if not locations:
            return SaveStats(exists=False), []

        total_files = sum(loc.file_count for loc in locations)
        total_bytes = sum(loc.total_size_bytes for loc in locations)
        non_reg_locs = [loc for loc in locations if not loc.path.lower().endswith((".reg", ".reg.old"))]
        target_locs = non_reg_locs if non_reg_locs else locations
        max_mtime = max((loc.last_modified for loc in target_locs), default=0.0)
        primary_path = locations[0].path if locations else ""

        if total_files == 0 or max_mtime == 0.0:
            return SaveStats(exists=False, display_path=primary_path), locations

        stats = SaveStats(
            exists=True,
            last_modified=max_mtime,
            size_bytes=total_bytes,
            file_count=total_files,
            display_path=primary_path
        )
        return stats, locations

    @staticmethod
    def _remote_game_snapshot(name_key: str) -> Optional[dict]:
        """Cloud metadata for one game via the Convex backend, or None."""
        listing = _get_cloud_listing()
        for game in listing.get("games", []):
            if game.get("nameKey") == name_key:
                return game
        return None
    @classmethod
    def _remote_stats(cls, name_key: str, local_mtime: float = 0.0,
                      game_name: str = "") -> Tuple[SaveStats, Optional[dict]]:
        """Best-effort cloud stats; returns None on any backend failure.

        ``game_name`` should be the raw library title (e.g. "The Witcher 3").
        ``name_key`` is the normalised cloud key (e.g. "the-witcher-3").
        Version-persistence QSettings entries are written under the raw title
        via ``set_active_save_version``, so we must query them with the same
        raw title — not with the already-normalised key.
        """
        try:
            snapshot = cls._remote_game_snapshot(name_key)
        except Exception as e:
            logger.warning(f"Cloud stats unavailable for '{name_key}': {e}")
            return None, None
        if not snapshot:
            return SaveStats(exists=False), None
        versions = snapshot.get("versions") or []
        if not versions:
            return SaveStats(exists=False), snapshot

        # Prefer game_name for version-persistence lookups; fall back to
        # name_key only when the caller did not supply the raw library title.
        lookup_name = game_name or name_key

        # Check if local mtime or explicitly activated version matches an existing generation
        top_version = versions[0].get("version", 0)
        active_ver = get_active_save_version(lookup_name)
        known_top = get_active_cloud_top_version(lookup_name)

        matched = None
        # If another device uploaded a newer generation (top_version > known_top),
        # respect the newly uploaded generation (versions[0]) instead of matching the older rolled-back version.
        if known_top is None or top_version <= known_top:
            if active_ver is not None:
                matched = next((v for v in versions if v.get("version") == active_ver), None)

            if matched is None and local_mtime > 0.0:
                matched = next((v for v in versions if abs(local_mtime - float(v.get("sourceMaxMtime", 0.0))) <= 2.0), None)

        target = matched if matched is not None else versions[0]
        stats = SaveStats(
            exists=True,
            # Content clock: manifest source_max_mtime recorded at upload,
            # directly comparable with local file mtimes across machines.
            last_modified=float(target.get("sourceMaxMtime") or 0.0),
            size_bytes=int(target.get("sizeBytes") or 0),
            file_count=len(versions),
            display_path=f"{snapshot.get('displayName', name_key)} (v{target.get('version', 0)})",
        )
        return stats, snapshot

    @classmethod
    def get_cloud_save_stats(cls, game_name: str) -> Tuple[SaveStats, str]:
        """Read metadata and stats of the game's cloud save archive (local-folder engine)."""
        cloud_zip = cls.get_cloud_save_path(game_name)
        if not os.path.isfile(cloud_zip) or os.path.getsize(cloud_zip) == 0:
            return SaveStats(exists=False, display_path=cloud_zip), cloud_zip

        try:
            stat = os.stat(cloud_zip)
            mtime = stat.st_mtime
            total_size = stat.st_size
            file_count = 0

            with zipfile.ZipFile(cloud_zip, 'r') as zipf:
                # Prefer the manifest's recorded newest-content mtime so local and
                # cloud snapshots are compared on the same clock domain. Archives
                # produced before this field existed fall back to the zip's own
                # filesystem mtime.
                if _MANIFEST_NAME in zipf.namelist():
                    try:
                        manifest = json.loads(zipf.read(_MANIFEST_NAME).decode("utf-8"))
                        content_mtime = manifest.get("source_max_mtime")
                        if content_mtime:
                            mtime = float(content_mtime)
                        else:
                            mtime = float(manifest.get("created_at", mtime))
                    except Exception as e:
                        logger.debug(f"Legacy manifest mtime fallback failed: {e}")
                file_count = len([m for m in zipf.infolist() if not m.is_dir() and m.filename != "safelauncher_manifest.json"])

            stats = SaveStats(
                exists=True,
                last_modified=mtime,
                size_bytes=total_size,
                file_count=file_count,
                display_path=cloud_zip
            )
            return stats, cloud_zip
        except Exception as e:
            logger.warning(f"Failed to read cloud save stats for '{game_name}': {e}")
            return SaveStats(exists=False, display_path=cloud_zip), cloud_zip

    @classmethod
    def check_sync_status(cls, game_name: str, game_path: str, steam_id: str = "") -> Tuple[SyncStatus, SaveStats, SaveStats]:
        """Compare local and cloud save timestamps to determine sync action required."""
        local_stats, _ = cls.get_local_save_stats(game_name, game_path, steam_id)

        if backend_active():
            key = resolve_name_key(game_name)
            cloud_stats, _snap = cls._remote_stats(key, local_mtime=local_stats.last_modified,
                                                   game_name=game_name)
            if cloud_stats is not None:
                return cls._decide(local_stats, cloud_stats)
            # Cloud unreachable (network or auth failure): say so instead of
            # guessing a sync state from the local-folder engine's disk cache.
            return SyncStatus.CLOUD_OFFLINE, local_stats, SaveStats(exists=False)

        cloud_stats, _zip = cls.get_cloud_save_stats(game_name)
        return cls._decide(local_stats, cloud_stats)

    @staticmethod
    def _decide(local_stats: SaveStats, cloud_stats: SaveStats) -> Tuple[SyncStatus, SaveStats, SaveStats]:
        if not local_stats.exists and not cloud_stats.exists:
            return SyncStatus.NO_SAVES, local_stats, cloud_stats
        if not local_stats.exists and cloud_stats.exists:
            return SyncStatus.CLOUD_ONLY, local_stats, cloud_stats
        if local_stats.exists and not cloud_stats.exists:
            return SyncStatus.LOCAL_NEWER, local_stats, cloud_stats

        diff = local_stats.last_modified - cloud_stats.last_modified
        if abs(diff) <= 2.0:
            return SyncStatus.IN_SYNC, local_stats, cloud_stats
        elif diff > 2.0:
            return SyncStatus.LOCAL_NEWER, local_stats, cloud_stats
        else:
            return SyncStatus.CLOUD_NEWER, local_stats, cloud_stats

    # ------------------------------------------------------------------ #
    # Transfers                                                          #
    # ------------------------------------------------------------------ #

    @classmethod
    def sync_local_to_cloud(cls, game_name: str, game_path: str, steam_id: str = "") -> bool:
        """Archive latest local save state directly into cloud save repository."""
        local_stats, locations = cls.get_local_save_stats(game_name, game_path, steam_id)
        if not local_stats.exists or not locations:
            logger.info(f"No local save files to upload for '{game_name}'")
            return False

        if backend_active():
            from core.cloud_backend import normalize_name_key, CloudBackendError
            backup_mgr = ZipBackupManager()
            tmp_fd, tmp_zip = tempfile.mkstemp(prefix=".sl-up-", suffix=".zip",
                                               dir=cls.get_cloud_root())
            os.close(tmp_fd)
            try:
                if not backup_mgr.export_save_locations(
                        locations, tmp_zip, game_name=game_name, game_path=game_path):
                    return False
                result = _backend().upload_plaintext_zip(
                    normalize_name_key(game_name), game_name,
                    tmp_zip, source_max_mtime=local_stats.last_modified)
                if result.get("skipped"):
                    logger.info(f"Cloud already up-to-date for '{game_name}'.")
                    skipped_ver = result.get("version")
                    if skipped_ver is not None:
                        norm_key = normalize_name_key(game_name)
                        snapshot = cls._remote_game_snapshot(norm_key)
                        top_v = snapshot["versions"][0].get("version") if (snapshot and snapshot.get("versions")) else skipped_ver
                        set_active_save_version(game_name, int(skipped_ver), cloud_top_version=top_v,
                                                name_key=norm_key)
                    return True
                evicted = result.get("evictedVersions") or []
                if evicted:
                    logger.info(f"Pruned old cloud generations {evicted} for '{game_name}'.")
                _invalidate_cloud_listing()
                uploaded_ver = result.get("version")
                if uploaded_ver is not None:
                    set_active_save_version(game_name, int(uploaded_ver), cloud_top_version=int(uploaded_ver),
                                            name_key=normalize_name_key(game_name))

                logger.info(
                    f"Uploaded encrypted save to cloud for '{game_name}' "
                    f"(v{result.get('version')})."
                )
                return True
            except CloudBackendError as e:
                logger.warning(f"Cloud upload failed ({e.code}); save kept locally.")
                return False
            finally:
                try:
                    os.unlink(tmp_zip)
                except OSError:
                    pass

        cloud_zip = cls.get_cloud_save_path(game_name)
        backup_mgr = ZipBackupManager()
        success = backup_mgr.export_save_locations(
            locations,
            cloud_zip,
            game_name=game_name,
            game_path=game_path
        )
        if success:
            logger.info(f"Uploaded local save to cloud archive: {cloud_zip} ({local_stats.file_count} files, {local_stats.size_bytes} bytes)")
        else:
            logger.error(f"Failed to upload local save to cloud for '{game_name}'")
        return success

    @classmethod
    def sync_cloud_to_local(cls, game_name: str, game_path: str,
                            steam_id: str = "", preserve_local_fork: bool = True,
                            target_version: Optional[int] = None) -> bool:
        """Extract and restore cloud save archive into local game/prefix.

        With preserve_local_fork (the default), the current local save is kept
        as a local backup archive before it is overwritten, so accepting
        the cloud side of a conflict never destroys local progress.
        """
        target_dest = os.path.join(game_path, "prefix")

        if backend_active():
            key = resolve_name_key(game_name)
            if preserve_local_fork:
                local_stats, locations = cls.get_local_save_stats(game_name, game_path, steam_id)
                if local_stats.exists and locations:
                    fork_dir = os.path.join(os.path.dirname(cls.get_cloud_root()), "save_forks")
                    os.makedirs(fork_dir, exist_ok=True)
                    clean_name = "".join(c for c in game_name if c.isalnum() or c in "-_ ").strip() or "game"
                    prefix_key = key or clean_name
                    already_backed_up = False
                    try:
                        for fname in os.listdir(fork_dir):
                            if (fname.startswith(f"{prefix_key}_fork_") or fname.startswith(f"{clean_name}_fork_")) and fname.endswith(".zip"):
                                ef = os.path.join(fork_dir, fname)
                                try:
                                    with zipfile.ZipFile(ef, "r") as z:
                                        if _MANIFEST_NAME in z.namelist():
                                            mf = json.loads(z.read(_MANIFEST_NAME).decode("utf-8"))
                                            # Use 2.0s tolerance (matching sync-status threshold)
                                            # because source_max_mtime is stored as int(), truncating
                                            # sub-second precision; a live mtime of 1234.999 vs
                                            # stored 1234 produces a diff up to ~1.999s.
                                            if abs(float(mf.get("source_max_mtime", 0.0)) - local_stats.last_modified) <= 2.0:
                                                already_backed_up = True
                                                logger.info(f"Local save for '{game_name}' already backed up in {fname}; skipping duplicate fork.")
                                                break
                                except Exception:
                                    pass
                    except Exception:
                        pass
                    if not already_backed_up:
                        fork_zip = os.path.join(fork_dir, f"{prefix_key}_fork_{int(time.time())}.zip")
                        backup_mgr = ZipBackupManager()
                        if backup_mgr.export_save_locations(locations, fork_zip,
                                                            game_name=game_name, game_path=game_path):
                            logger.info(f"Preserved local save fork for '{game_name}' at {fork_zip}")
                        else:
                            logger.warning(
                                f"Could not back up the local save for '{game_name}' to {fork_zip}; "
                                f"refusing to overwrite it with the cloud copy."
                            )
                            return False
                    cls._prune_safety_forks(fork_dir, prefix_key, clean_name, keep=10)
            try:
                plain_zip, meta = _backend().download_to_temp(key, version=target_version)
            except Exception as e:
                logger.warning(f"Cloud download failed for '{game_name}': {e}")
                return False
            try:
                backup_mgr = ZipBackupManager()
                success = backup_mgr.import_save(plain_zip, target_dest, game_path=game_path)
                if success and not backup_mgr.verify_import(plain_zip, target_dest, game_path=game_path):
                    logger.warning(f"Restored files for '{game_name}' do not match the cloud archive.")
                    success = False
            finally:
                try:
                    os.unlink(plain_zip)
                except OSError:
                    pass
            if success:
                restored_ver = meta.get("version")
                if restored_ver is not None:
                    snapshot = cls._remote_game_snapshot(key)
                    top_v = snapshot["versions"][0].get("version") if (snapshot and snapshot.get("versions")) else restored_ver
                    set_active_save_version(game_name, int(restored_ver), cloud_top_version=top_v,
                                            name_key=key)

                logger.info(
                    f"Restored cloud save v{restored_ver} for '{game_name}' "
                    f"into {target_dest}"
                )
            else:
                logger.error(f"Failed to restore cloud save for '{game_name}'")
            return success

        cloud_stats, cloud_zip = cls.get_cloud_save_stats(game_name)
        if not cloud_stats.exists:
            logger.warning(f"No cloud save available to restore for '{game_name}'")
            return False

        if preserve_local_fork:
            local_stats, locations = cls.get_local_save_stats(game_name, game_path, steam_id)
            if local_stats.exists and locations:
                fork_zip = os.path.join(os.path.dirname(cloud_zip), "save_local_fork.zip")
                backup_mgr = ZipBackupManager()
                if backup_mgr.export_save_locations(locations, fork_zip,
                                                    game_name=game_name, game_path=game_path):
                    logger.info(f"Kept local save fork for '{game_name}' at {fork_zip}")
                else:
                    logger.warning(
                        f"Could not back up the local save for '{game_name}' to "
                        f"{fork_zip}; refusing to overwrite it with the cloud copy."
                    )
                    return False

        backup_mgr = ZipBackupManager()
        success = backup_mgr.import_save(cloud_zip, target_dest, game_path=game_path)
        if success and not backup_mgr.verify_import(cloud_zip, target_dest, game_path=game_path):
            logger.warning(f"Restored files for '{game_name}' do not match the cloud archive.")
            success = False
        if success:
            logger.info(f"Successfully restored cloud save archive for '{game_name}' into {target_dest}")
        else:
            logger.error(f"Failed to restore cloud save for '{game_name}'")
        return success

    @classmethod
    def _prune_safety_forks(cls, fork_dir: str, prefix_key: str, clean_name: str, keep: int = 10) -> int:
        """Keep only the newest `keep` fork archives for a game, removing older ones."""
        pruned_count = 0
        try:
            if not os.path.isdir(fork_dir):
                return 0
            game_forks = []
            for fname in os.listdir(fork_dir):
                if (fname.startswith(f"{prefix_key}_fork_") or fname.startswith(f"{clean_name}_fork_")) and fname.endswith(".zip"):
                    game_forks.append(os.path.join(fork_dir, fname))
            def _fork_sort_key(p: str) -> int:
                """Extract the Unix timestamp from the fork filename for stable ordering.

                Fork filenames are ``{prefix}_fork_{unix_ts}.zip``.  The embedded
                timestamp is the authoritative creation order — filesystem mtime
                is fragile after rsync/backup copies.  Falls back to 0 (oldest)
                so corrupt or mis-named forks are pruned first.
                """
                try:
                    stem = os.path.basename(p).replace(".zip", "")
                    ts_str = stem.rsplit("_fork_", 1)[-1]
                    return int(ts_str)
                except (ValueError, IndexError):
                    return 0

            game_forks.sort(key=_fork_sort_key, reverse=True)

            for old_fork in game_forks[keep:]:
                try:
                    os.unlink(old_fork)
                    pruned_count += 1
                    logger.info(f"Pruned older safety fork: {os.path.basename(old_fork)}")
                except OSError:
                    pass
        except Exception as e:
            logger.debug(f"Error pruning forks in {fork_dir}: {e}")
        return pruned_count

    @classmethod
    def restore_cloud_generation(cls, game_name: str, game_path: str,
                                 steam_id: str = "", version: Optional[int] = None) -> bool:
        """Roll the game back to a retained cloud generation without minting duplicate versions."""
        if not backend_active():
            logger.warning("Generation restore requires the Convex cloud backend.")
            return False
        return cls.sync_cloud_to_local(
            game_name, game_path, steam_id=steam_id, preserve_local_fork=True, target_version=version
        )

    @classmethod
    def get_available_versions(cls, game_name: str, game_path: str = "", steam_id: str = "") -> list[dict]:
        """Return all available cloud generations and local safety forks for a game."""
        results = []
        key = resolve_name_key(game_name)
        active_ver = get_active_save_version(game_name)
        local_stats = SaveStats(exists=False)
        if game_path:
            local_stats, _ = cls.get_local_save_stats(game_name, game_path, steam_id)

        # 1. Cloud versions
        if backend_active():
            snapshot = cls._remote_game_snapshot(key)
            if snapshot and snapshot.get("versions"):
                for idx, v in enumerate(snapshot["versions"]):
                    v_num = v.get("version", 0)
                    v_mtime = float(v.get("sourceMaxMtime", 0.0))
                    is_active = False
                    if local_stats.exists:
                        if active_ver is not None and v_num == active_ver:
                            is_active = True
                        elif active_ver is None and abs(local_stats.last_modified - v_mtime) <= 2.0:
                            is_active = True

                    results.append({
                        "version": v_num,
                        "source": "cloud",
                        "display_name": f"Cloud Generation v{v_num}",
                        "mtime": v_mtime,
                        "size_bytes": int(v.get("sizeBytes", 0)),
                        "is_active": is_active,
                        "raw": v,
                    })

        # 2. Local forks in save_forks/
        fork_dir = os.path.join(os.path.dirname(cls.get_cloud_root()), "save_forks")
        clean_name = "".join(c for c in game_name if c.isalnum() or c in "-_ ").strip() or "game"
        prefix_key = key or clean_name
        if os.path.isdir(fork_dir):
            try:
                for fname in sorted(os.listdir(fork_dir), reverse=True):
                    if (fname.startswith(f"{prefix_key}_fork_") or fname.startswith(f"{clean_name}_fork_")) and fname.endswith(".zip"):
                        fpath = os.path.join(fork_dir, fname)
                        try:
                            f_size = os.path.getsize(fpath)
                            f_mtime = os.path.getmtime(fpath)
                            with zipfile.ZipFile(fpath, "r") as z:
                                if _MANIFEST_NAME in z.namelist():
                                    mf = json.loads(z.read(_MANIFEST_NAME).decode("utf-8"))
                                    f_mtime = float(mf.get("source_max_mtime", f_mtime))
                            is_active = local_stats.exists and abs(local_stats.last_modified - f_mtime) <= 2.0
                            results.append({
                                "version": fname,
                                "source": "fork",
                                "display_name": f"Local Backup ({fname.split('_fork_')[-1].replace('.zip', '')})",
                                "mtime": f_mtime,
                                "size_bytes": f_size,
                                "is_active": is_active,
                                "path": fpath,
                            })
                        except Exception:
                            pass
            except Exception:
                pass

        # Sort by mtime descending
        results.sort(key=lambda x: x.get("mtime", 0.0), reverse=True)
        return results
