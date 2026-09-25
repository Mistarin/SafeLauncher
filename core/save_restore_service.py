"""Shared safety boundary for archive restores that replace local saves."""

from __future__ import annotations

import os
import hashlib
import json
import time
import uuid
from dataclasses import dataclass
from typing import Iterable

from core.save_models import SaveOperationResult
from core.save_validation import (
    describe_validation_failures,
    snapshot_from_validation,
    validate_save_locations,
)
from core.zip_backup import _MANIFEST_NAME, ZipBackupManager


@dataclass(frozen=True, slots=True)
class RestorePlan:
    """Immutable preflight identity for an overwrite-capable restore.

    The UI may display the metadata and later pass this object back after the
    user confirms.  If the selected archive or target changes in between, the
    stable ``plan_id`` check refuses to overwrite a different save.
    """

    plan_id: str
    operation: str
    game_name: str
    game_path: str
    source_type: str
    source_version: int | None
    source_timestamp: float
    source_size_bytes: int
    source_file_count: int
    source_device: str
    source_key: str
    archive_path: str
    archive_size_bytes: int
    archive_mtime_ns: int
    destination_path: str
    target_locations: tuple[str, ...]
    safety_backup_required: bool
    created_at: float

    @classmethod
    def create(
        cls,
        archive_path: str,
        destination_path: str,
        *,
        game_name: str,
        game_path: str,
        current_locations: Iterable,
        operation: str = "Restore previous version",
        source_type: str = "archive",
        source_version: int | None = None,
        source_timestamp: float = 0.0,
        source_size_bytes: int | None = None,
        source_file_count: int = 0,
        source_device: str = "",
        source_key: str = "",
    ) -> "RestorePlan":
        archive_path = os.path.abspath(str(archive_path)) if archive_path else ""
        destination_path = os.path.abspath(str(destination_path))
        try:
            stat = os.stat(archive_path)
        except OSError:
            stat = None
        if source_size_bytes is None or int(source_size_bytes or 0) <= 0:
            source_size_bytes = int(stat.st_size) if stat is not None else 0
        if not source_timestamp and stat is not None:
            source_timestamp = float(stat.st_mtime)
        locations = tuple(
            os.path.abspath(os.path.expanduser(str(getattr(location, "path", location))))
            for location in (current_locations or ())
        )
        material = {
            "archive": archive_path,
            "archive_size": int(getattr(stat, "st_size", 0) if stat else 0),
            "source_size": int(source_size_bytes),
            "archive_mtime_ns": int(getattr(stat, "st_mtime_ns", 0) if stat else 0),
            "destination": destination_path,
            "game": str(game_name),
            "game_path": os.path.abspath(str(game_path or "")),
            "locations": locations,
            "source_type": str(source_type),
            "source_version": source_version,
            "source_timestamp": float(source_timestamp),
            "source_device": str(source_device),
            "source_key": str(source_key),
            "backup": bool(locations),
        }
        plan_id = hashlib.sha256(
            json.dumps(material, sort_keys=True, separators=(",", ":")).encode("utf-8")
        ).hexdigest()
        return cls(
            plan_id=plan_id,
            operation=str(operation),
            game_name=str(game_name),
            game_path=os.path.abspath(str(game_path or "")),
            source_type=str(source_type),
            source_version=source_version,
            source_timestamp=float(source_timestamp),
            source_size_bytes=int(source_size_bytes),
            source_file_count=max(0, int(source_file_count or 0)),
            source_device=str(source_device or ""),
            source_key=str(source_key or ""),
            archive_path=archive_path,
            archive_size_bytes=int(getattr(stat, "st_size", 0) if stat else 0),
            archive_mtime_ns=int(getattr(stat, "st_mtime_ns", 0) if stat else 0),
            destination_path=destination_path,
            target_locations=locations,
            safety_backup_required=bool(locations),
            created_at=time.time(),
        )

    def still_matches(self) -> bool:
        if not self.archive_path:
            return True
        try:
            stat = os.stat(self.archive_path)
        except OSError:
            return False
        return (
            int(stat.st_size) == self.archive_size_bytes
            and int(getattr(stat, "st_mtime_ns", 0)) == self.archive_mtime_ns
        )


def create_restore_plan(
    archive_path: str,
    destination_path: str,
    *,
    game_name: str,
    game_path: str,
    current_locations: Iterable,
    **metadata,
) -> RestorePlan:
    """Build a stable restore preflight object for UI or service callers."""
    return RestorePlan.create(
        archive_path,
        destination_path,
        game_name=game_name,
        game_path=game_path,
        current_locations=current_locations,
        **metadata,
    )


def create_remote_restore_plan(
    *,
    game_name: str,
    game_path: str,
    source_key: str,
    version: int | None,
    source_timestamp: float = 0.0,
    source_size_bytes: int = 0,
    source_file_count: int = 0,
    source_device: str = "",
    operation: str = "Restore cloud save",
) -> RestorePlan:
    """Create a stable preflight identity for a remote immutable version."""
    return RestorePlan.create(
        "",
        game_path,
        game_name=game_name,
        game_path=game_path,
        current_locations=(),
        operation=operation,
        source_type="cloud",
        source_key=source_key,
        source_version=version,
        source_timestamp=source_timestamp,
        source_size_bytes=source_size_bytes,
        source_file_count=source_file_count,
        source_device=source_device,
    )


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
    plan: RestorePlan | None = None,
    source_metadata: dict | None = None,
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
    if not locations:
        # An empty detector result is not proof that the destination is empty.
        # Refuse an unprotected overwrite when regular files are already
        # present; callers must rescan and provide managed locations so the
        # safety archive has an explicit scope.
        try:
            existing_files = (
                [destination_path]
                if os.path.isfile(destination_path) and not os.path.islink(destination_path)
                else (
                    os.path.join(root, name)
                    for root, dirs, files in os.walk(destination_path, followlinks=False)
                    for name in files
                    if not os.path.islink(os.path.join(root, name))
                )
            )
            if any(True for _ in existing_files):
                return SaveOperationResult(
                    False,
                    operation,
                    game_name,
                    error="The current save locations could not be verified before restore.",
                    category="local_save_missing",
                    guidance="Rescan Save Locations so a safety backup can be created before replacing files.",
                    retry_safe=False,
                )
        except OSError:
            return SaveOperationResult(
                False,
                operation,
                game_name,
                error="The restore destination could not be inspected safely.",
                category="local_save_unreadable",
                retry_safe=False,
            )
    if not manager.validate_archive(import_zip_path, destination_path, game_path=game_path):
        return SaveOperationResult(
            False,
            operation,
            game_name,
            error="The selected save archive failed safety validation and was not restored.",
            category="local_save_unreadable",
            guidance="Choose another save version or verify the archive before retrying.",
            retry_safe=False,
        )
    metadata = dict(source_metadata or {})
    if plan is None:
        plan = create_restore_plan(
            import_zip_path,
            destination_path,
            game_name=game_name,
            game_path=game_path,
            current_locations=locations,
            operation=operation,
            source_type=str(metadata.get("source_type", "archive")),
            source_version=metadata.get("source_version"),
            source_timestamp=float(metadata.get("source_timestamp", 0.0) or 0.0),
            source_size_bytes=metadata.get("source_size_bytes"),
            source_file_count=int(metadata.get("source_file_count", 0) or 0),
            source_device=str(metadata.get("source_device", "") or ""),
            source_key=str(metadata.get("source_key", "") or ""),
        )
    current_location_paths = tuple(
        os.path.abspath(os.path.expanduser(str(getattr(location, "path", location))))
        for location in locations
    )
    if plan is not None and (
        not plan.still_matches()
        or os.path.abspath(import_zip_path) != plan.archive_path
        or current_location_paths != plan.target_locations
        or os.path.abspath(destination_path) != plan.destination_path
        or str(game_name) != plan.game_name
        or os.path.abspath(str(game_path or "")) != plan.game_path
    ):
        return SaveOperationResult(
            False,
            operation,
            game_name,
            error="The selected save version changed before restore confirmation.",
            category="stale_selection",
            guidance="Refresh save history and select the version again.",
            retry_safe=False,
        )
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
        payload={
            "safety_backup": backup_zip_path if locations else "",
            "restore_plan_id": plan.plan_id,
            "restore_plan": plan,
        },
    )


def safety_backup_path(directory: str, game_name: str) -> str:
    """Return a collision-resistant private backup filename."""
    safe = "".join(char for char in str(game_name) if char.isalnum() or char in "-_ ").strip() or "game"
    return os.path.join(
        directory,
        f"{safe}_before_restore_{int(time.time())}_{uuid.uuid4().hex[:8]}.zip",
    )


__all__ = [
    "RestorePlan",
    "create_restore_plan",
    "create_remote_restore_plan",
    "restore_archive_with_safety_backup",
    "safety_backup_path",
]
