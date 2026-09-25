"""Shared safety boundary for archive restores that replace local saves."""

from __future__ import annotations

import os
import time
import uuid
from typing import Iterable

from core.save_models import SaveOperationResult
from core.save_validation import (
    describe_validation_failures,
    snapshot_from_validation,
    validate_save_locations,
)
from core.zip_backup import _MANIFEST_NAME, ZipBackupManager


def restore_archive_with_safety_backup(
    import_zip_path: str,
    destination_path: str,
    *,
    game_name: str,
    game_path: str,
    current_locations: Iterable,
    backup_zip_path: str,
    operation: str = "Restore previous version",
    cancel_check=None,
) -> SaveOperationResult:
    """Restore one archive only after the current local state is protected.

    This is intentionally UI-neutral so Game Properties, Save Manager, and
    future restore surfaces cannot accidentally diverge on backup behavior.
    """
    if cancel_check and cancel_check():
        return SaveOperationResult(False, operation, game_name,
                                  error="Restore was cancelled.", category="cancelled")
    if not import_zip_path or not os.path.isfile(import_zip_path):
        return SaveOperationResult(
            False, operation, game_name,
            error="The selected save archive is no longer available.",
            category="local_save_missing",
        )

    locations = list(current_locations or ())
    manager = ZipBackupManager()
    if locations:
        validation = validate_save_locations(locations, cancel_check=cancel_check)
        if cancel_check and cancel_check():
            return SaveOperationResult(
                False, operation, game_name,
                error="Restore was cancelled.", category="cancelled",
            )
        failures = describe_validation_failures(validation)
        if failures:
            return SaveOperationResult(
                False, operation, game_name,
                error=failures,
                category="local_save_unreadable",
                guidance="Rescan Save Locations before restoring the selected archive.",
            )
        snapshot = snapshot_from_validation(
            game_name, game_path, validation, source="restore-safety-backup"
        )
        backup_dir = os.path.dirname(os.path.abspath(backup_zip_path))
        os.makedirs(backup_dir, mode=0o700, exist_ok=True)
        try:
            os.chmod(backup_dir, 0o700)
        except OSError:
            pass
        if not manager.export_save_locations(
            locations,
            backup_zip_path,
            game_name=game_name,
            game_path=game_path,
            snapshot=snapshot,
            cancel_check=cancel_check,
        ):
            if cancel_check and cancel_check():
                return SaveOperationResult(
                    False, operation, game_name,
                    error="Restore was cancelled.", category="cancelled",
                )
            return SaveOperationResult(
                False, operation, game_name,
                error=manager.last_error or "Could not create a safety backup of the current local save.",
                category="local_save_unreadable",
                guidance="The selected archive was not restored; your current save was left unchanged.",
                retry_safe=False,
            )

    if cancel_check and cancel_check():
        return SaveOperationResult(False, operation, game_name,
                                  error="Restore was cancelled.", category="cancelled")

    if not manager.import_save(
        import_zip_path,
        destination_path,
        game_path=game_path,
        cancel_check=cancel_check,
    ):
        if cancel_check and cancel_check():
            return SaveOperationResult(
                False, operation, game_name,
                error="Restore was cancelled.", category="cancelled",
            )
        return SaveOperationResult(
            False, operation, game_name,
            error="The selected save archive could not be restored.",
            category="local_save_unreadable",
            guidance="Your safety backup was preserved; check the archive and save locations, then retry.",
        )

    # Legacy raw archives have no manifest mapping and cannot be verified by
    # the manifest-aware verifier.  They are still imported safely, but new
    # SafeLauncher archives must pass an end-to-end content check.
    try:
        import zipfile
        with zipfile.ZipFile(import_zip_path, "r") as archive:
            has_manifest = _MANIFEST_NAME in archive.namelist()
    except (OSError, ValueError, zipfile.BadZipFile):
        has_manifest = False
    if has_manifest and not manager.verify_import(
        import_zip_path,
        destination_path,
        game_path=game_path,
        cancel_check=cancel_check,
    ):
        if cancel_check and cancel_check():
            return SaveOperationResult(
                False, operation, game_name,
                error="Restore was cancelled.", category="cancelled",
            )
        if locations and os.path.isfile(backup_zip_path):
            # Best-effort rollback from the safety archive.  The backup is
            # still retained even if rollback itself cannot complete.
            manager.import_save(
                backup_zip_path,
                destination_path,
                game_path=game_path,
                cancel_check=None,
            )
        return SaveOperationResult(
            False, operation, game_name,
            error="Restore verification failed after importing the save archive.",
            category="local_save_unreadable",
            guidance="Your safety backup was preserved. Do not delete it until the save is verified.",
            retry_safe=False,
        )

    return SaveOperationResult(
        True,
        operation,
        game_name,
        local_modified=True,
        payload={"safety_backup": backup_zip_path if locations else ""},
    )


def safety_backup_path(directory: str, game_name: str) -> str:
    """Return a collision-resistant private backup filename."""
    safe = "".join(char for char in str(game_name) if char.isalnum() or char in "-_ ").strip() or "game"
    return os.path.join(
        directory,
        f"{safe}_before_restore_{int(time.time())}_{uuid.uuid4().hex[:8]}.zip",
    )


__all__ = ["restore_archive_with_safety_backup", "safety_backup_path"]
