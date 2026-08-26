import os
import json
import time
import zipfile
import inspect
from typing import List, Optional
from core.interfaces import IBackupManager
from core.logger import get_logger

logger = get_logger("ZipBackup")


class ZipBackupManager(IBackupManager):
    """Manages game save snapshot compression, export, and secure restoration."""

    def export_save(self, save_path: str, export_zip_path: str) -> bool:
        """Legacy direct directory export."""
        if not os.path.exists(save_path):
            return False

        try:
            with zipfile.ZipFile(export_zip_path, 'w', zipfile.ZIP_DEFLATED) as zipf:
                for root, _, files in os.walk(save_path):
                    for file in files:
                        full_path = os.path.join(root, file)
                        arcname = os.path.relpath(full_path, start=save_path)
                        zipf.write(full_path, arcname)
            return True
        except Exception as e:
            logger.error(f"Save export failed: {e}")
            return False

    def export_save_locations(self, locations: list, export_zip_path: str, game_name: str = "", game_path: str = "") -> bool:
        """Export multiple detected save locations with metadata manifest."""
        if not locations:
            return False

        try:
            manifest = {
                "format_version": 1,
                "created_at": int(time.time()),
                "game_name": game_name,
                "items": []
            }

            with zipfile.ZipFile(export_zip_path, 'w', zipfile.ZIP_DEFLATED) as zipf:
                for idx, loc in enumerate(locations):
                    src_path = loc.path if hasattr(loc, "path") else str(loc)
                    rel_prefix = loc.relative_to_prefix if hasattr(loc, "relative_to_prefix") else ""
                    disp_name = loc.display_name if hasattr(loc, "display_name") else os.path.basename(src_path)

                    if not os.path.exists(src_path):
                        continue

                    item_meta = {
                        "id": idx,
                        "display_name": disp_name,
                        "relative_to_prefix": rel_prefix,
                        "is_directory": os.path.isdir(src_path),
                        "archive_prefix": f"data/{idx}"
                    }
                    manifest["items"].append(item_meta)

                    if os.path.isfile(src_path):
                        zipf.write(src_path, f"data/{idx}/{os.path.basename(src_path)}")
                    else:
                        for root, _, files in os.walk(src_path):
                            for file in files:
                                full_path = os.path.join(root, file)
                                rel = os.path.relpath(full_path, start=src_path)
                                zipf.write(full_path, f"data/{idx}/{rel}")

                # Write manifest file into root of ZIP
                zipf.writestr("safelauncher_manifest.json", json.dumps(manifest, indent=2))
            return True
        except Exception as e:
            logger.error(f"Multi-location save export failed: {e}")
            return False

    def import_save(self, import_zip_path: str, destination_path: str) -> bool:
        """Import save archive into destination path (supports both manifest & raw ZIP)."""
        if not os.path.exists(import_zip_path):
            return False

        dest_abs = os.path.abspath(destination_path)
        os.makedirs(dest_abs, exist_ok=True)

        try:
            with zipfile.ZipFile(import_zip_path, 'r') as zipf:
                namelist = zipf.namelist()

                # 1. Manifest-aware extraction
                if "safelauncher_manifest.json" in namelist:
                    try:
                        manifest_data = json.loads(zipf.read("safelauncher_manifest.json").decode("utf-8"))
                        items = manifest_data.get("items", [])
                        for item in items:
                            rel_prefix = item.get("relative_to_prefix", "")
                            arc_prefix = item.get("archive_prefix", "")
                            if not arc_prefix:
                                continue

                            # Resolve target extraction path
                            if rel_prefix:
                                target_dir = os.path.normpath(os.path.join(dest_abs, rel_prefix))
                            else:
                                target_dir = dest_abs

                            # Ensure safety
                            if not target_dir.startswith(dest_abs):
                                target_dir = dest_abs

                            os.makedirs(target_dir, exist_ok=True)

                            for member in zipf.infolist():
                                if member.filename.startswith(arc_prefix + "/") and not member.is_dir():
                                    sub_rel = os.path.relpath(member.filename, arc_prefix)
                                    final_out = os.path.abspath(os.path.join(target_dir, sub_rel))
                                    if final_out.startswith(dest_abs):
                                        os.makedirs(os.path.dirname(final_out), exist_ok=True)
                                        with zipf.open(member) as src_file, open(final_out, "wb") as out_f:
                                            out_f.write(src_file.read())
                        return True
                    except Exception as merr:
                        logger.warning(f"Manifest parse/restore encountered error, falling back to direct extraction: {merr}")

                # 2. Standard / Legacy safe extraction
                for member in zipf.infolist():
                    target_path = os.path.abspath(os.path.join(dest_abs, member.filename))
                    if not target_path.startswith(dest_abs + os.sep) and target_path != dest_abs:
                        logger.warning(f"Refusing to extract unsafe file: {member.filename}")
                        return False

                extractall_params = inspect.signature(zipfile.ZipFile.extractall).parameters
                if "filter" in extractall_params:
                    zipf.extractall(dest_abs, filter='data')
                else:
                    zipf.extractall(dest_abs)
            return True
        except Exception as e:
            logger.error(f"Save import failed: {e}")
            return False

