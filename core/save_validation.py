"""Fresh validation of save locations immediately before packaging.

The detector is intentionally allowed to cache discovery results.  Packaging
must not trust that cache, however: a game can remove or replace a save path
between the scan and the user's upload/export click.  This module turns a
detected location into a fresh snapshot and reports the exact paths that are
not usable.
"""

import os
from dataclasses import dataclass, replace
from typing import Iterable

from core.ludusavi_detector import SaveLocation


@dataclass(frozen=True)
class SaveLocationValidation:
    """Result of checking one selected location right now."""

    location: SaveLocation
    valid: bool
    reason: str = ""
    file_count: int = 0
    total_size_bytes: int = 0
    last_modified: float = 0.0


def _readable_file(path: str) -> tuple[bool, int, float, str]:
    try:
        stat = os.stat(path)
        if not os.path.isfile(path):
            return False, 0, 0.0, "is not a regular file"
        # Opening the file catches broken mounts and ACL/read failures that
        # os.access() can miss when the application runs with a different uid.
        with open(path, "rb"):
            pass
        return True, stat.st_size, stat.st_mtime, ""
    except OSError as exc:
        return False, 0, 0.0, str(exc)


def validate_save_location(location: SaveLocation) -> SaveLocationValidation:
    """Validate a location and return refreshed metadata for packaging.

    Zero-byte files are valid save files.  Directory walks do not follow
    symlinked directories, avoiding recursive loops and keeping the archive
    source set deterministic.
    """
    path = os.path.abspath(os.path.expanduser(location.path))
    if not os.path.lexists(path):
        return SaveLocationValidation(location, False, "path no longer exists")

    if os.path.isfile(path):
        ok, size, mtime, reason = _readable_file(path)
        if not ok:
            return SaveLocationValidation(location, False, f"file is not readable ({reason})")
        refreshed = replace(location, path=path, is_directory=False,
                            file_count=1, total_size_bytes=size,
                            last_modified=mtime)
        return SaveLocationValidation(refreshed, True, file_count=1,
                                      total_size_bytes=size, last_modified=mtime)

    if not os.path.isdir(path):
        return SaveLocationValidation(location, False, "path is not a file or directory")

    count = 0
    total = 0
    latest = 0.0
    unreadable: list[str] = []
    walk_errors: list[str] = []
    try:
        walker = os.walk(path, topdown=True, followlinks=False)
        for root, dirs, files in walker:
            # Do not descend through symlinked directories.  A symlinked file
            # is still checked as a file by the normal stat/open path below.
            dirs[:] = [name for name in dirs
                       if not os.path.islink(os.path.join(root, name))]
            for name in files:
                file_path = os.path.join(root, name)
                ok, size, mtime, reason = _readable_file(file_path)
                if not ok:
                    unreadable.append(f"{file_path}: {reason}")
                    continue
                count += 1
                total += size
                latest = max(latest, mtime)
    except OSError as exc:
        walk_errors.append(str(exc))

    if walk_errors:
        return SaveLocationValidation(location, False,
                                      f"directory could not be read ({walk_errors[0]})",
                                      count, total, latest)
    if unreadable:
        return SaveLocationValidation(location, False,
                                      f"file could not be read ({unreadable[0]})",
                                      count, total, latest)
    if count == 0:
        return SaveLocationValidation(location, False, "directory contains no readable files")

    refreshed = replace(location, path=path, is_directory=True,
                        file_count=count, total_size_bytes=total,
                        last_modified=latest)
    return SaveLocationValidation(refreshed, True, file_count=count,
                                  total_size_bytes=total, last_modified=latest)


def validate_save_locations(locations: Iterable[SaveLocation]) -> list[SaveLocationValidation]:
    """Validate every selected location without silently dropping failures."""
    return [validate_save_location(location) for location in locations]


def describe_validation_failures(results: Iterable[SaveLocationValidation]) -> str:
    failures = [result for result in results if not result.valid]
    if not failures:
        return ""
    lines = ["The selected save paths could not be read:"]
    lines.extend(f"• {result.location.path} — {result.reason}" for result in failures)
    return "\n".join(lines)


__all__ = [
    "SaveLocationValidation",
    "validate_save_location",
    "validate_save_locations",
    "describe_validation_failures",
]
