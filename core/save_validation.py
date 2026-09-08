"""Fresh validation of save locations immediately before packaging.

The detector is intentionally allowed to cache discovery results.  Packaging
must not trust that cache, however: a game can remove or replace a save path
between the scan and the user's upload/export click.  This module turns a
detected location into a fresh snapshot and reports the exact paths that are
not usable.
"""

import os
from dataclasses import dataclass, replace
from typing import Callable, Iterable, Optional

from core.ludusavi_detector import SaveLocation
from core.save_models import GameSaveSnapshot, SaveFileState


@dataclass(frozen=True)
class SaveLocationValidation:
    """Result of checking one selected location right now."""

    location: SaveLocation
    valid: bool
    reason: str = ""
    file_count: int = 0
    total_size_bytes: int = 0
    last_modified: float = 0.0
    file_states: tuple[SaveFileState, ...] = ()


def _readable_file(path: str, check_readable: bool = True) -> tuple[bool, int, float, str]:
    try:
        stat = os.stat(path)
        if not os.path.isfile(path):
            return False, 0, 0.0, "is not a regular file"
        if check_readable:
            # Opening the file catches broken mounts and ACL/read failures that
            # os.access() can miss when the application runs with a different uid.
            with open(path, "rb"):
                pass
        return True, stat.st_size, stat.st_mtime, ""
    except OSError as exc:
        return False, 0, 0.0, str(exc)


def validate_save_location(
    location: SaveLocation,
    cancel_check: Optional[Callable[[], bool]] = None,
    check_readable: bool = True,
) -> SaveLocationValidation:
    """Validate a location and return refreshed metadata for packaging.

    Zero-byte files are valid save files.  Directory walks do not follow
    symlinked directories, avoiding recursive loops and keeping the archive
    source set deterministic.
    """
    path = os.path.abspath(os.path.expanduser(location.path))
    if not os.path.lexists(path):
        return SaveLocationValidation(location, False, "path no longer exists")

    if os.path.isfile(path):
        ok, size, mtime, reason = _readable_file(path, check_readable)
        if not ok:
            return SaveLocationValidation(location, False, f"file is not readable ({reason})")
        refreshed = replace(location, path=path, is_directory=False,
                            file_count=1, total_size_bytes=size,
                            last_modified=mtime)
        state = SaveFileState.from_path(path)
        return SaveLocationValidation(refreshed, True, file_count=1,
                                      total_size_bytes=size, last_modified=mtime,
                                      file_states=(state,))

    if not os.path.isdir(path):
        return SaveLocationValidation(location, False, "path is not a file or directory")

    count = 0
    total = 0
    latest = 0.0
    unreadable: list[str] = []
    walk_errors: list[str] = []
    file_states: list[SaveFileState] = []
    try:
        walker = os.walk(
            path,
            topdown=True,
            followlinks=False,
            onerror=lambda error: walk_errors.append(str(error)),
        )
        for root, dirs, files in walker:
            if cancel_check and cancel_check():
                return SaveLocationValidation(location, False, "save validation was cancelled")
            # Do not descend through symlinked directories.  A symlinked file
            # is still checked as a file by the normal stat/open path below.
            dirs[:] = [name for name in dirs
                       if not os.path.islink(os.path.join(root, name))]
            for name in files:
                if cancel_check and cancel_check():
                    return SaveLocationValidation(location, False, "save validation was cancelled")
                file_path = os.path.join(root, name)
                ok, size, mtime, reason = _readable_file(file_path, check_readable)
                if not ok:
                    unreadable.append(f"{file_path}: {reason}")
                    continue
                count += 1
                total += size
                latest = max(latest, mtime)
                file_states.append(SaveFileState.from_path(file_path))
    except OSError as exc:
        walk_errors.append(str(exc))

    if walk_errors:
        return SaveLocationValidation(location, False,
                                      f"directory could not be read ({walk_errors[0]})",
                                      count, total, latest, tuple(file_states))
    if unreadable:
        return SaveLocationValidation(location, False,
                                      f"file could not be read ({unreadable[0]})",
                                      count, total, latest, tuple(file_states))
    if count == 0:
        return SaveLocationValidation(location, False, "directory contains no readable files")

    refreshed = replace(location, path=path, is_directory=True,
                        file_count=count, total_size_bytes=total,
                        last_modified=latest)
    return SaveLocationValidation(refreshed, True, file_count=count,
                                  total_size_bytes=total, last_modified=latest,
                                  file_states=tuple(file_states))


def validate_save_locations(
    locations: Iterable[SaveLocation],
    cancel_check: Optional[Callable[[], bool]] = None,
    check_readable: bool = True,
) -> list[SaveLocationValidation]:
    """Validate every selected location without silently dropping failures."""
    return [
        validate_save_location(location, cancel_check, check_readable)
        for location in locations
    ]


def describe_validation_failures(results: Iterable[SaveLocationValidation]) -> str:
    failures = [result for result in results if not result.valid]
    if not failures:
        return ""
    lines = ["The selected save paths could not be read:"]
    lines.extend(f"• {result.location.path} — {result.reason}" for result in failures)
    return "\n".join(lines)


def snapshot_from_validation(
    game_name: str,
    game_path: str,
    results: Iterable[SaveLocationValidation],
    *,
    source: str = "detector",
) -> GameSaveSnapshot:
    """Build the shared snapshot from successful validation results."""
    result_list = list(results)
    locations = [result.location for result in result_list if result.valid]
    files = tuple(sorted(
        (state for result in result_list if result.valid for state in result.file_states),
        key=lambda state: state.path,
    ))
    return GameSaveSnapshot.from_locations(
        game_name, game_path, locations, source=source, files=files
    )


__all__ = [
    "SaveLocationValidation",
    "validate_save_location",
    "validate_save_locations",
    "describe_validation_failures",
    "snapshot_from_validation",
]
