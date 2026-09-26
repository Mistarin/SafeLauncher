"""Transactional game-payload updates from an archive.

The normal archive installer intentionally merges files into a destination so
it can be used for first-time installs.  Updates need stronger semantics:
stale payload files must disappear, while Wine/UMU runtime state and detected
saves must survive.  This module builds a complete replacement tree beside
the live installation and swaps it in only after all validation and backups
have succeeded.
"""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import tempfile
import tarfile
import time
import uuid
import zipfile
from dataclasses import dataclass
from typing import Callable, Iterable, Optional

from core.archive_extractor import extract_archive_sandboxed, find_executables
from core.archive_installer import ArchiveInstaller
from core.ludusavi_detector import LudusaviDetector, SaveLocation
from core.save_models import SaveOperationCancelled, SaveOperationResult
from core.save_validation import describe_validation_failures, validate_save_locations
from core.zip_backup import ZipBackupManager, _is_within
from core.logger import get_logger

logger = get_logger("GameArchiveUpdater")

_UPDATE_PREFIX = ".safelauncher-update-"
_BACKUP_ROOT = os.path.expanduser("~/.local/share/safelauncher/update_backups")


@dataclass(frozen=True)
class GameUpdatePlan:
    archive_path: str
    game_path: str
    game_name: str
    executable: str
    save_locations: tuple[SaveLocation, ...]
    protected_paths: tuple[str, ...]
    required_bytes: int
    free_bytes: int

    @property
    def save_count(self) -> int:
        return len(self.save_locations)


def _cancelled(cancel_check: Optional[Callable[[], bool]]) -> bool:
    return bool(cancel_check and cancel_check())


def _regular_files(root: str) -> Iterable[str]:
    if not os.path.isdir(root):
        return
    for current, dirs, files in os.walk(root, topdown=True, followlinks=False):
        dirs[:] = sorted(dirs)
        for name in sorted(files):
            path = os.path.join(current, name)
            if os.path.islink(path):
                continue
            if os.path.isfile(path):
                yield path


def _tree_size(root: str) -> int:
    return sum(os.path.getsize(path) for path in _regular_files(root))


def _digest_files(paths: Iterable[str]) -> dict[str, str]:
    result = {}
    for path in sorted(set(os.path.abspath(path) for path in paths)):
        if os.path.isfile(path) and not os.path.islink(path):
            digest = hashlib.sha256()
            with open(path, "rb") as stream:
                for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                    digest.update(chunk)
            result[path] = digest.hexdigest()
    return result


def _location_files(locations: Iterable[SaveLocation]) -> list[str]:
    paths = []
    for location in locations:
        path = os.path.abspath(os.path.expanduser(location.path))
        if os.path.isfile(path) and not os.path.islink(path):
            paths.append(path)
        elif os.path.isdir(path) and not os.path.islink(path):
            paths.extend(_regular_files(path))
    return paths


def _copy_tree_without_links(source: str, destination: str, source_root: Optional[str] = None) -> None:
    """Copy protected data, allowing only links that stay inside its root."""
    source_root = source_root or source
    if os.path.islink(source):
        if not _is_within(source_root, os.path.realpath(source)):
            raise ValueError(f"Protected path escapes its root through a symlink: {source}")
        os.makedirs(os.path.dirname(destination), exist_ok=True)
        os.symlink(os.readlink(source), destination)
        return
    if os.path.isfile(source):
        os.makedirs(os.path.dirname(destination), exist_ok=True)
        shutil.copy2(source, destination)
        return
    if not os.path.isdir(source):
        return
    os.makedirs(destination, exist_ok=True)
    for name in sorted(os.listdir(source)):
        src = os.path.join(source, name)
        dst = os.path.join(destination, name)
        if os.path.islink(src):
            if not _is_within(source_root, os.path.realpath(src)):
                raise ValueError(f"Protected tree escapes its root through a symlink: {src}")
            os.symlink(os.readlink(src), dst)
            continue
        if os.path.isdir(src):
            _copy_tree_without_links(src, dst, source_root)
        elif os.path.isfile(src):
            shutil.copy2(src, dst)
        else:
            raise ValueError(f"Unsupported protected file type: {src}")


def _path_overlaps(first: str, second: str) -> bool:
    first = os.path.normcase(os.path.abspath(first))
    second = os.path.normcase(os.path.abspath(second))
    return first == second or first.startswith(second + os.sep) or second.startswith(first + os.sep)


def _safe_archive_member_name(name: str) -> bool:
    normalized = str(name or "").replace("\\", "/")
    if not normalized or normalized.startswith("/") or normalized.startswith("../"):
        return False
    parts = [part for part in normalized.split("/") if part not in ("", ".")]
    return bool(parts) and ".." not in parts


def _validate_archive_members(archive_path: str) -> Optional[str]:
    """Reject traversal, links, special members, and duplicate destinations."""
    lower = archive_path.lower()
    names = set()
    try:
        if lower.endswith(".zip"):
            with zipfile.ZipFile(archive_path) as archive:
                members = archive.infolist()
                for member in members:
                    if not _safe_archive_member_name(member.filename):
                        return f"Unsafe archive path: {member.filename}"
                    mode = (int(member.external_attr) >> 16) & 0o170000
                    if mode == 0o120000 or (mode and mode not in (0o100000, 0o040000)):
                        return f"Unsupported archive member type: {member.filename}"
                    normalized = os.path.normcase(os.path.normpath(member.filename.replace("\\", "/")))
                    if normalized in names:
                        return f"Duplicate archive path: {member.filename}"
                    names.add(normalized)
        elif lower.endswith((".tar", ".tar.gz", ".tgz")):
            with tarfile.open(archive_path, "r:*") as archive:
                for member in archive.getmembers():
                    if not _safe_archive_member_name(member.name):
                        return f"Unsafe archive path: {member.name}"
                    if member.issym() or member.islnk() or not (member.isfile() or member.isdir()):
                        return f"Unsupported archive member type: {member.name}"
                    normalized = os.path.normcase(os.path.normpath(member.name.replace("\\", "/")))
                    if normalized in names:
                        return f"Duplicate archive path: {member.name}"
                    names.add(normalized)
    except (OSError, ValueError, tarfile.TarError, zipfile.BadZipFile) as error:
        return f"Could not validate archive: {error}"
    return None


def _contains_regular_files(root: str) -> bool:
    return any(True for _ in _regular_files(root))


class GameArchiveUpdater:
    """Prepare and atomically apply a replacement game payload."""

    def __init__(self, backup_root: str = _BACKUP_ROOT):
        self.backup_root = os.path.abspath(os.path.expanduser(backup_root))

    def prepare(
        self,
        archive_path: str,
        game_path: str,
        *,
        game_name: str,
        executable: str = "",
        steam_id: str = "",
        cancel_check: Optional[Callable[[], bool]] = None,
    ) -> GameUpdatePlan | SaveOperationResult:
        archive_path = os.path.abspath(os.path.expanduser(str(archive_path or "")))
        game_path = os.path.abspath(os.path.expanduser(str(game_path or "")))
        if _cancelled(cancel_check):
            return SaveOperationResult(False, "Update game files", game_name, error="Update was cancelled.", category="cancelled")
        if not os.path.isfile(archive_path):
            return SaveOperationResult(False, "Update game files", game_name, error="The selected game archive is not available.", category="archive_missing")
        if not os.path.isdir(game_path) or os.path.islink(game_path):
            return SaveOperationResult(False, "Update game files", game_name, error="The selected game installation directory is not safe.", category="install_missing")

        try:
            inspection = ArchiveInstaller().inspect(archive_path, os.path.dirname(game_path) or game_path)
        except Exception as error:
            return SaveOperationResult(False, "Update game files", game_name, error=f"Archive preflight failed: {error}", category="archive_invalid")
        archive_error = _validate_archive_members(archive_path)
        if archive_error:
            return SaveOperationResult(False, "Update game files", game_name, error=archive_error, category="archive_invalid")

        # A fresh detection is required for an overwrite-capable operation.
        try:
            LudusaviDetector.clear_cache(game_name)
            locations = tuple(LudusaviDetector.detect_saves(game_name, game_path, str(steam_id or "")))
        except Exception as error:
            return SaveOperationResult(False, "Update game files", game_name, error=f"Save detection failed: {error}", category="save_detection_failed")

        if not locations and _contains_regular_files(game_path):
            return SaveOperationResult(
                False,
                "Update game files",
                game_name,
                error="The current installation contains files, but no save locations could be verified.",
                category="save_detection_failed",
                guidance="Rescan Save Locations before updating so the current saves can be protected.",
                retry_safe=False,
            )

        protected = []
        prefix = os.path.join(game_path, "prefix")
        if os.path.lexists(prefix):
            protected.append(prefix)
        config = os.path.join(game_path, ".sandbox-config")
        if os.path.lexists(config):
            protected.append(config)
        for location in locations:
            path = os.path.abspath(os.path.expanduser(location.path))
            if _is_within(game_path, path) and path not in protected:
                protected.append(path)

        protected_size = sum(_tree_size(path) if os.path.isdir(path) else os.path.getsize(path) for path in protected if os.path.isfile(path) or os.path.isdir(path))
        required = int(inspection.required_bytes + protected_size + max(1, protected_size * 0.10))
        free = shutil.disk_usage(os.path.dirname(game_path) or game_path).free
        if free < required:
            return SaveOperationResult(
                False,
                "Update game files",
                game_name,
                error=f"The update needs about {required / (1024 ** 3):.2f} GB, but only {free / (1024 ** 3):.2f} GB is free.",
                category="insufficient_disk_space",
            )
        return GameUpdatePlan(
            archive_path=archive_path,
            game_path=game_path,
            game_name=str(game_name),
            executable=str(executable or ""),
            save_locations=locations,
            protected_paths=tuple(protected),
            required_bytes=required,
            free_bytes=int(free),
        )

    def update(
        self,
        archive_path: str,
        game_path: str,
        *,
        game_name: str,
        executable: str = "",
        steam_id: str = "",
        cancel_check: Optional[Callable[[], bool]] = None,
        progress_callback: Optional[Callable[[str], None]] = None,
    ) -> SaveOperationResult:
        operation = "Update game files"
        plan_or_error = self.prepare(
            archive_path,
            game_path,
            game_name=game_name,
            executable=executable,
            steam_id=steam_id,
            cancel_check=cancel_check,
        )
        if isinstance(plan_or_error, SaveOperationResult):
            return plan_or_error
        plan = plan_or_error
        transaction_root = tempfile.mkdtemp(
            prefix=_UPDATE_PREFIX,
            dir=os.path.dirname(plan.game_path) or None,
        )
        extracted = os.path.join(transaction_root, "extracted")
        staged = os.path.join(transaction_root, "staged")
        rollback = os.path.join(transaction_root, "rollback")
        marker = os.path.join(transaction_root, "transaction.json")
        save_backup = ""
        old_save_digest = _digest_files(_location_files(plan.save_locations))
        swapped = False
        preserve_transaction = False

        def report(message: str) -> None:
            if progress_callback:
                progress_callback(message)

        try:
            with open(marker, "w", encoding="utf-8") as stream:
                json.dump({"game_path": plan.game_path, "phase": "preparing", "created_at": time.time()}, stream)

            if _cancelled(cancel_check):
                raise SaveOperationCancelled()
            report("Extracting archive into a protected staging area…")
            if not extract_archive_sandboxed(plan.archive_path, extracted, cancel_callback=cancel_check):
                return SaveOperationResult(False, operation, plan.game_name, error="The game archive could not be extracted safely.", category="archive_invalid")

            if any(os.path.islink(path) or not (os.path.isfile(path) or os.path.isdir(path)) for path in _walk_entries(extracted)):
                return SaveOperationResult(False, operation, plan.game_name, error="The archive contains symbolic links and was rejected.", category="archive_invalid")
            executable_path = os.path.join(extracted, plan.executable) if plan.executable else ""
            if plan.executable and not os.path.isfile(executable_path):
                return SaveOperationResult(False, operation, plan.game_name, error=f"The updated archive does not contain the configured executable: {plan.executable}", category="executable_missing")
            if not plan.executable and not find_executables(extracted):
                return SaveOperationResult(False, operation, plan.game_name, error="The updated archive contains no launchable executable.", category="executable_missing")

            for protected in plan.protected_paths:
                relative = os.path.relpath(protected, plan.game_path)
                incoming = os.path.join(extracted, relative)
                if os.path.lexists(incoming):
                    return SaveOperationResult(False, operation, plan.game_name, error=f"The archive attempts to overwrite protected runtime/save data: {relative}", category="protected_path_conflict")

            if _cancelled(cancel_check):
                raise SaveOperationCancelled()
            report("Creating a verified save safety backup…")
            if plan.save_locations:
                os.makedirs(self.backup_root, mode=0o700, exist_ok=True)
                save_backup = os.path.join(
                    self.backup_root,
                    f"{_safe_name(plan.game_name)}_before_update_{int(time.time())}_{uuid.uuid4().hex[:8]}.zip",
                )
                validation = validate_save_locations(plan.save_locations, cancel_check=cancel_check)
                failures = describe_validation_failures(validation)
                if failures:
                    return SaveOperationResult(False, operation, plan.game_name, error=failures, category="save_unreadable", guidance="Rescan Save Locations and retry.", retry_safe=False)
                if not ZipBackupManager().export_save_locations(plan.save_locations, save_backup, game_name=plan.game_name, game_path=plan.game_path, cancel_check=cancel_check):
                    return SaveOperationResult(False, operation, plan.game_name, error="Could not create the pre-update save safety backup.", category="save_unreadable", retry_safe=False)

            if _cancelled(cancel_check):
                raise SaveOperationCancelled()
            shutil.copytree(extracted, staged, symlinks=False)
            for protected in plan.protected_paths:
                relative = os.path.relpath(protected, plan.game_path)
                _copy_tree_without_links(protected, os.path.join(staged, relative))

            current_digest = _digest_files(_location_files(plan.save_locations))
            if current_digest != old_save_digest:
                return SaveOperationResult(False, operation, plan.game_name, error="A save changed while the update was being prepared.", category="save_changed", guidance="Close the game and retry.", retry_safe=False)

            report("Replacing the old game payload…")
            with open(marker, "w", encoding="utf-8") as stream:
                json.dump({"game_path": plan.game_path, "phase": "swapping", "created_at": time.time()}, stream)
            os.rename(plan.game_path, rollback)
            try:
                os.rename(staged, plan.game_path)
                swapped = True
            except Exception:
                os.rename(rollback, plan.game_path)
                raise

            new_executable = os.path.join(plan.game_path, plan.executable) if plan.executable else ""
            if plan.executable and not os.path.isfile(new_executable):
                raise RuntimeError("The configured executable is missing after the update.")
            if _digest_files(_location_files(plan.save_locations)) != old_save_digest:
                raise RuntimeError("Protected save data changed during the update.")

            with open(marker, "w", encoding="utf-8") as stream:
                json.dump({"game_path": plan.game_path, "phase": "completed", "created_at": time.time()}, stream)
            shutil.rmtree(rollback, ignore_errors=True)
            return SaveOperationResult(
                True,
                operation,
                plan.game_name,
                local_modified=True,
                payload={"save_backup": save_backup, "protected_paths": plan.protected_paths},
            )
        except SaveOperationCancelled:
            return SaveOperationResult(False, operation, plan.game_name, error="Update was cancelled.", category="cancelled")
        except Exception as error:
            logger.error("Game update failed for %s: %s", plan.game_name, error)
            if swapped:
                try:
                    failed_path = plan.game_path + ".failed-" + uuid.uuid4().hex[:8]
                    os.rename(plan.game_path, failed_path)
                    os.rename(rollback, plan.game_path)
                    shutil.rmtree(failed_path, ignore_errors=True)
                except Exception as rollback_error:
                    logger.critical("Game update rollback failed for %s: %s", plan.game_path, rollback_error)
                    preserve_transaction = True
            return SaveOperationResult(False, operation, plan.game_name, error=str(error), category="update_failed", guidance="The previous installation was restored when possible. Check the retained save backup before retrying.", retry_safe=False, payload={"save_backup": save_backup})
        finally:
            if not preserve_transaction:
                shutil.rmtree(extracted, ignore_errors=True)
                shutil.rmtree(staged, ignore_errors=True)
                if os.path.isdir(transaction_root):
                    shutil.rmtree(transaction_root, ignore_errors=True)


def recover_incomplete_game_updates(parent_directory: str) -> list[str]:
    """Recover interrupted swaps left by a process crash.

    A transaction is normally removed by :meth:`GameArchiveUpdater.update`.
    If the process dies between the two directory renames, the marker and
    rollback tree remain beside the game and can be repaired at startup.
    """
    parent_directory = os.path.abspath(os.path.expanduser(parent_directory))
    recovered = []
    if not os.path.isdir(parent_directory):
        return recovered
    for name in sorted(os.listdir(parent_directory)):
        if not name.startswith(_UPDATE_PREFIX):
            continue
        transaction_root = os.path.join(parent_directory, name)
        marker = os.path.join(transaction_root, "transaction.json")
        if not os.path.isdir(transaction_root) or not os.path.isfile(marker):
            continue
        try:
            with open(marker, "r", encoding="utf-8") as stream:
                metadata = json.load(stream)
            game_path = os.path.abspath(str(metadata.get("game_path") or ""))
            phase = str(metadata.get("phase") or "preparing")
            rollback = os.path.join(transaction_root, "rollback")
            if phase == "completed":
                shutil.rmtree(transaction_root, ignore_errors=True)
                continue
            if phase == "swapping" and game_path and os.path.isdir(rollback):
                if os.path.lexists(game_path):
                    failed_path = game_path + ".interrupted-" + uuid.uuid4().hex[:8]
                    os.rename(game_path, failed_path)
                    shutil.rmtree(failed_path, ignore_errors=True)
                os.rename(rollback, game_path)
                recovered.append(game_path)
            shutil.rmtree(transaction_root, ignore_errors=True)
        except Exception as error:
            logger.critical("Could not recover interrupted game update %s: %s", transaction_root, error)
    return recovered


def _walk_entries(root: str) -> Iterable[str]:
    for current, dirs, files in os.walk(root, topdown=True, followlinks=False):
        for name in list(dirs) + list(files):
            yield os.path.join(current, name)


def _safe_name(value: str) -> str:
    clean = "".join(char if char.isalnum() or char in "-_ ." else "_" for char in str(value or "game"))
    return clean.strip(" .") or "game"


__all__ = ["GameArchiveUpdater", "GameUpdatePlan", "recover_incomplete_game_updates"]
