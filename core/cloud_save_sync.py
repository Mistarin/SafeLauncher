"""
Automatic Cloud / Local Save Synchronization Engine for SafeLauncher.
Syncs game save states between local Wine/UMU prefixes and a centralized cloud root directory
(e.g., Syncthing, Nextcloud, Dropbox, Rclone, or local backup directory).
"""

import os
import time
import json
import zipfile
from enum import Enum
from dataclasses import dataclass
from typing import List, Tuple, Optional
from PyQt6.QtCore import QSettings

from core.ludusavi_detector import LudusaviDetector, SaveLocation
from core.zip_backup import ZipBackupManager
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

    @classmethod
    def get_cloud_save_stats(cls, game_name: str) -> Tuple[SaveStats, str]:
        """Read metadata and stats of the game's cloud save archive."""
        cloud_zip = cls.get_cloud_save_path(game_name)
        if not os.path.isfile(cloud_zip) or os.path.getsize(cloud_zip) == 0:
            return SaveStats(exists=False, display_path=cloud_zip), cloud_zip

        try:
            stat = os.stat(cloud_zip)
            mtime = stat.st_mtime
            total_size = stat.st_size
            file_count = 0

            with zipfile.ZipFile(cloud_zip, 'r') as zipf:
                # If safelauncher_manifest.json exists, parse original creation timestamp
                if "safelauncher_manifest.json" in zipf.namelist():
                    try:
                        manifest = json.loads(zipf.read("safelauncher_manifest.json").decode("utf-8"))
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
        cloud_stats, _ = cls.get_cloud_save_stats(game_name)

        if not local_stats.exists and not cloud_stats.exists:
            return SyncStatus.NO_SAVES, local_stats, cloud_stats

        if not local_stats.exists and cloud_stats.exists:
            return SyncStatus.CLOUD_ONLY, local_stats, cloud_stats

        if local_stats.exists and not cloud_stats.exists:
            return SyncStatus.LOCAL_NEWER, local_stats, cloud_stats

        # Both exist: compare timestamps (with a 2-second tolerance for filesystem differences)
        diff = local_stats.last_modified - cloud_stats.last_modified
        if abs(diff) <= 2.0:
            return SyncStatus.IN_SYNC, local_stats, cloud_stats
        elif diff > 2.0:
            return SyncStatus.LOCAL_NEWER, local_stats, cloud_stats
        else:
            return SyncStatus.CLOUD_NEWER, local_stats, cloud_stats

    @classmethod
    def sync_local_to_cloud(cls, game_name: str, game_path: str, steam_id: str = "") -> bool:
        """Archive latest local save state directly into cloud save repository."""
        local_stats, locations = cls.get_local_save_stats(game_name, game_path, steam_id)
        if not local_stats.exists or not locations:
            logger.info(f"No local save files to upload for '{game_name}'")
            return False

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
        cloud_stats, cloud_zip = cls.get_cloud_save_stats(game_name)
        if not cloud_stats.exists:
            logger.warning(f"No cloud save available to restore for '{game_name}'")
            return False

        target_dest = os.path.join(game_path, "prefix")
        os.makedirs(target_dest, exist_ok=True)

        backup_mgr = ZipBackupManager()
        success = backup_mgr.import_save(cloud_zip, target_dest)
        if success:
            logger.info(f"Successfully restored cloud save archive for '{game_name}' into {target_dest}")
        else:
            logger.error(f"Failed to restore cloud save for '{game_name}'")
        return success
