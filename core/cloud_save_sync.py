"""
Automatic Cloud / Local Save Synchronization Engine for SafeLauncher.

Two interchangeable backends sit behind this single API:

* "convex"  — real accounts + encrypted storage on Convex (core.cloud_backend),
              active when QSettings cloud_mode == "convex" and a Clerk session
              exists. Archives are byte-compatible with…
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

def cloud_mode() -> str:
    settings = QSettings("SafeLauncher", "SafeLauncher")
    mode = settings.value("cloud_mode", None)
    if mode is not None and str(mode).strip():
        return str(mode).strip()
    try:
        from core import clerk_auth
        if clerk_auth.get_status().get("signed_in"):
            return "convex"
    except Exception:
        pass
    return "local"


def set_cloud_mode(mode: str) -> None:
    if mode not in ("local", "convex"):
        raise ValueError(f"Unknown cloud mode: {mode}")
    QSettings("SafeLauncher", "SafeLauncher").setValue("cloud_mode", mode)


def backend_active() -> bool:
    """True when the Convex backend should serve sync operations."""
    if cloud_mode() != "convex":
        return False
    settings = QSettings("SafeLauncher", "SafeLauncher")
    if settings.value("cloud_secret_key", "", type=str).strip():
        return True
    try:
        from core import clerk_auth
        if clerk_auth.get_status().get("signed_in"):
            return True
    except Exception:
        pass
    # If a custom convex endpoint is configured, activate it
    site = settings.value("convex_site_url", "", type=str).strip()
    return bool(site)


_backend_singleton = None
_LISTING_CACHE = {"ts": 0.0, "data": None}


def _backend():
    global _backend_singleton
    if _backend_singleton is None:
        from core.cloud_backend import ConvexSaveBackend
        _backend_singleton = ConvexSaveBackend()
    return _backend_singleton


def _get_cloud_listing(force_refresh: bool = False) -> dict:
    global _LISTING_CACHE
    import time
    now = time.time()
    if not force_refresh and _LISTING_CACHE["data"] and (now - _LISTING_CACHE["ts"] < 30.0):
        return _LISTING_CACHE["data"]
    try:
        data = _backend().list_games()
        _LISTING_CACHE = {"ts": now, "data": data}
        return data
    except Exception as e:
        logger.debug(f"Failed to fetch cloud game listing: {e}")
        return _LISTING_CACHE["data"] or {"games": []}


def _invalidate_cloud_listing():
    global _LISTING_CACHE
    _LISTING_CACHE = {"ts": 0.0, "data": None}


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
        max_mtime = max((loc.last_modified for loc in locations), default=0.0)
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
    def _remote_stats(cls, name_key: str) -> Tuple[SaveStats, Optional[dict]]:
        """Best-effort cloud stats; returns None on any backend failure."""
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
        top = versions[0]
        stats = SaveStats(
            exists=True,
            # Content clock: manifest source_max_mtime recorded at upload,
            # directly comparable with local file mtimes across machines.
            last_modified=float(top["sourceMaxMtime"]),
            size_bytes=int(top["sizeBytes"]),
            file_count=len(versions),
            display_path=f"{snapshot.get('displayName', name_key)} (v{top['version']})",
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
                    except Exception:
                        pass
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
            from core.cloud_backend import normalize_name_key
            key = normalize_name_key(game_name)
            cloud_stats, _snap = cls._remote_stats(key)
            if cloud_stats is not None:
                return cls._decide(local_stats, cloud_stats)
            # Cloud unreachable → fall through to local-folder semantics.

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
                    return True
                evicted = result.get("evictedVersions") or []
                if evicted:
                    logger.info(f"Pruned old cloud generations {evicted} for '{game_name}'.")
                _invalidate_cloud_listing()
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
    def sync_cloud_to_local(cls, game_name: str, game_path: str) -> bool:
        """Extract and restore cloud save archive into local game/prefix."""
        target_dest = os.path.join(game_path, "prefix")

        if backend_active():
            from core.cloud_backend import normalize_name_key
            key = normalize_name_key(game_name)
            try:
                plain_zip, meta = _backend().download_to_temp(key)
            except Exception as e:
                logger.warning(f"Cloud download failed for '{game_name}': {e}")
                return False
            try:
                backup_mgr = ZipBackupManager()
                success = backup_mgr.import_save(plain_zip, target_dest)
            finally:
                try:
                    os.unlink(plain_zip)
                except OSError:
                    pass
            if success:
                logger.info(
                    f"Restored cloud save v{meta['version']} for '{game_name}' "
                    f"into {target_dest}"
                )
            else:
                logger.error(f"Failed to restore cloud save for '{game_name}'")
            return success

        cloud_stats, cloud_zip = cls.get_cloud_save_stats(game_name)
        if not cloud_stats.exists:
            logger.warning(f"No cloud save available to restore for '{game_name}'")
            return False

        os.makedirs(target_dest, exist_ok=True)

        backup_mgr = ZipBackupManager()
        success = backup_mgr.import_save(cloud_zip, target_dest)
        if success:
            logger.info(f"Successfully restored cloud save archive for '{game_name}' into {target_dest}")
        else:
            logger.error(f"Failed to restore cloud save for '{game_name}'")
        return success
