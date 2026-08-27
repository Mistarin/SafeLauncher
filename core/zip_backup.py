import os
import json
import time
import shutil
import zipfile
import tempfile
import inspect
from typing import List, Optional
from core.interfaces import IBackupManager
from core.logger import get_logger

logger = get_logger("ZipBackup")

_MANIFEST_NAME = "safelauncher_manifest.json"


def _is_within(parent: str, candidate: str) -> bool:
    """True if candidate is parent itself or lives under it (separator-safe)."""
    parent_abs = os.path.abspath(parent)
    cand_abs = os.path.abspath(candidate)
    return cand_abs == parent_abs or cand_abs.startswith(parent_abs + os.sep)


def _write_zip_atomically(export_zip_path: str, writer) -> bool:
    """Build the archive at a temp path beside the target, then swap it in.

    A crash mid-compression leaves any previous good archive untouched.
    """
    out_dir = os.path.dirname(os.path.abspath(export_zip_path)) or "."
    try:
        os.makedirs(out_dir, exist_ok=True)
        fd, tmp_path = tempfile.mkstemp(prefix=".safelauncher-zip-", suffix=".tmp", dir=out_dir)
        os.close(fd)
    except Exception as e:
        logger.error(f"Could not prepare temp file for {export_zip_path}: {e}")
        return False

    try:
        writer(tmp_path)
        os.replace(tmp_path, export_zip_path)
        return True
    except Exception as e:
        logger.error(f"Save export failed: {e}")
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        return False


class ZipBackupManager(IBackupManager):
    """Manages game save snapshot compression, export, and secure restoration."""

    def export_save(self, save_path: str, export_zip_path: str) -> bool:
        """Legacy direct directory export."""
        if not os.path.exists(save_path):
            return False

        def writer(tmp_path: str) -> None:
            with zipfile.ZipFile(tmp_path, 'w', zipfile.ZIP_DEFLATED, compresslevel=9) as zipf:
                for root, _, files in os.walk(save_path):
                    for file in files:
                        full_path = os.path.join(root, file)
                        arcname = os.path.relpath(full_path, start=save_path)
                        zipf.write(full_path, arcname)

        return _write_zip_atomically(export_zip_path, writer)

    def export_save_locations(self, locations: list, export_zip_path: str, game_name: str = "", game_path: str = "") -> bool:
        """Export multiple detected save locations with metadata manifest."""
        if not locations:
            return False

        max_source_mtime = 0.0

        def archive_member(src_file: str) -> str:
            nonlocal max_source_mtime
            try:
                max_source_mtime = max(max_source_mtime, os.stat(src_file).st_mtime)
            except OSError:
                pass
            return src_file

        def walk_files(src_path: str):
            if os.path.isfile(src_path):
                yield archive_member(src_path), os.path.basename(src_path)
            else:
                for root, _, files in os.walk(src_path):
                    for file in files:
                        full_path = os.path.join(root, file)
                        yield archive_member(full_path), os.path.relpath(full_path, start=src_path)

        try:
            def writer(tmp_path: str) -> None:
                nonlocal max_source_mtime
                written_any = False
                with zipfile.ZipFile(tmp_path, 'w', zipfile.ZIP_DEFLATED, compresslevel=9) as zipf:
                    items_meta = []
                    for idx, loc in enumerate(locations):
                        src_path = loc.path if hasattr(loc, "path") else str(loc)
                        rel_prefix = loc.relative_to_prefix if hasattr(loc, "relative_to_prefix") else ""
                        disp_name = loc.display_name if hasattr(loc, "display_name") else os.path.basename(src_path)

                        if not os.path.exists(src_path):
                            continue

                        items_meta.append({
                            "id": idx,
                            "display_name": disp_name,
                            "relative_to_prefix": rel_prefix,
                            "is_directory": os.path.isdir(src_path),
                            "archive_prefix": f"data/{idx}"
                        })

                        for full_path, rel in walk_files(src_path):
                            zipf.write(full_path, f"data/{idx}/{rel}")
                            written_any = True

                    if not written_any:
                        raise ValueError("No save files found to archive")

                    manifest = {
                        "format_version": 1,
                        "created_at": int(time.time()),
                        # Content timestamp: newest mtime among archived files. Unlike a
                        # wall-clock upload stamp this stays comparable to local save
                        # mtimes across machines (see SyncStatus comparison).
                        "source_max_mtime": int(max_source_mtime),
                        "game_name": game_name,
                        "items": items_meta
                    }
                    zipf.writestr(_MANIFEST_NAME, json.dumps(manifest, indent=2))

            return _write_zip_atomically(export_zip_path, writer)
        except Exception as e:
            logger.error(f"Multi-location save export failed: {e}")
            return False

    def import_save(self, import_zip_path: str, destination_path: str) -> bool:
        """Import save archive into destination path (supports both manifest & raw ZIP).

        Every member is unpacked into a staging directory first; live save data
        is only touched after the whole archive has been safely extracted and
        its paths validated, so an interrupted import cannot corrupt saves.
        """
        if not os.path.exists(import_zip_path):
            return False

        dest_abs = os.path.abspath(destination_path)
        os.makedirs(dest_abs, exist_ok=True)

        staging_root = None
        try:
            with zipfile.ZipFile(import_zip_path, 'r') as zipf:
                namelist = zipf.namelist()

                # Planned transfers: list of (ZipInfo, final destination path).
                planned = None
                if _MANIFEST_NAME in namelist:
                    planned = self._plan_manifest_import(zipf, dest_abs)

                if planned is None:
                    # Standard / Legacy safe extraction of the whole archive.
                    for member in zipf.infolist():
                        target_path = os.path.join(dest_abs, member.filename)
                        if not _is_within(dest_abs, target_path):
                            logger.warning(f"Refusing to extract unsafe file: {member.filename}")
                            return False

                    staging_root = tempfile.mkdtemp(prefix=".safelauncher-import-", dir=os.path.dirname(dest_abs))
                    extractall_params = inspect.signature(zipfile.ZipFile.extractall).parameters
                    if "filter" in extractall_params:
                        zipf.extractall(staging_root, filter='data')
                    else:
                        zipf.extractall(staging_root)
                else:
                    staging_root = tempfile.mkdtemp(prefix=".safelauncher-import-", dir=os.path.dirname(dest_abs))
                    for member, final_path in planned:
                        rel_dest = os.path.relpath(final_path, dest_abs)
                        staged_path = os.path.normpath(os.path.join(staging_root, rel_dest))
                        if not _is_within(staging_root, staged_path):
                            logger.warning(f"Skipping unsafe archive member: {member.filename}")
                            continue
                        os.makedirs(os.path.dirname(staged_path), exist_ok=True)
                        with zipf.open(member) as src_file, open(staged_path, "wb") as out_f:
                            shutil.copyfileobj(src_file, out_f)

            # Archive fully materialized in staging; merge onto live data now.
            moved_any = False
            for root, _, files in os.walk(staging_root):
                for file in files:
                    staged_file = os.path.join(root, file)
                    rel = os.path.relpath(staged_file, staging_root)
                    final_path = os.path.normpath(os.path.join(dest_abs, rel))
                    if not _is_within(dest_abs, final_path):
                        logger.warning(f"Refusing unsafe staged path: {rel}")
                        continue
                    os.makedirs(os.path.dirname(final_path), exist_ok=True)
                    shutil.move(staged_file, final_path)
                    moved_any = True

            if not moved_any:
                logger.warning(f"Save archive contained no restorable files: {import_zip_path}")
            return True
        except Exception as e:
            logger.error(f"Save import failed: {e}")
            return False
        finally:
            if staging_root and os.path.isdir(staging_root):
                shutil.rmtree(staging_root, ignore_errors=True)

    def _plan_manifest_import(self, zipf, dest_abs: str) -> Optional[list]:
        """Resolve manifest items into (ZipInfo, final destination path) pairs.

        Returns None when the manifest is unusable so callers can fall back to
        plain whole-archive extraction.
        """
        try:
            manifest_data = json.loads(zipf.read(_MANIFEST_NAME).decode("utf-8"))
            items = manifest_data.get("items", [])
            transfers = []
            for item in items:
                rel_prefix = item.get("relative_to_prefix", "")
                arc_prefix = item.get("archive_prefix", "")
                if not arc_prefix:
                    continue

                if rel_prefix:
                    target_dir = os.path.normpath(os.path.join(dest_abs, rel_prefix))
                else:
                    target_dir = dest_abs

                # Escape attempts fall back to the destination root instead of executing.
                if not _is_within(dest_abs, target_dir):
                    logger.warning(f"Manifest item escapes destination ({rel_prefix}); clamping to {dest_abs}")
                    target_dir = dest_abs

                matched = 0
                for member in zipf.infolist():
                    if member.filename.startswith(arc_prefix + "/") and not member.is_dir():
                        sub_rel = os.path.relpath(member.filename, arc_prefix)
                        final_out = os.path.normpath(os.path.join(target_dir, sub_rel))
                        if not _is_within(dest_abs, final_out):
                            logger.warning(f"Skipping unsafe archive member: {member.filename}")
                            continue
                        transfers.append((member, final_out))
                        matched += 1
                if matched == 0:
                    logger.warning(f"No archive members found for manifest item '{arc_prefix}'")
            return transfers
        except Exception as merr:
            logger.warning(f"Manifest parse encountered error, falling back to raw extraction: {merr}")
            return None
