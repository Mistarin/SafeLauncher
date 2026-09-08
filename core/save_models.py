"""Shared save-domain value objects.

These objects deliberately contain no UI or backend code.  They define the
state that crosses detection, validation, packaging, cloud, and restore
boundaries so each layer does not invent its own representation.
"""

from __future__ import annotations

import hashlib
import os
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from enum import Enum
from typing import Callable, Iterable, Optional

from core.ludusavi_detector import SaveLocation


class SaveOperationCancelled(Exception):
    """Raised when a cooperative save operation is cancelled."""


class SaveSnapshotPhase(str, Enum):
    DETECTED = "detected"
    VALIDATED = "validated"
    PACKAGING = "packaging"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


@dataclass(frozen=True)
class SaveFileState:
    path: str
    size_bytes: int
    modified_ns: int
    inode: int

    @classmethod
    def from_path(cls, path: str) -> "SaveFileState":
        stat = os.stat(path)
        return cls(os.path.abspath(path), stat.st_size, stat.st_mtime_ns, stat.st_ino)


def iter_save_files(locations: Iterable[SaveLocation], cancel_check: Optional[Callable[[], bool]] = None):
    """Yield regular files deterministically without following directory links."""
    for location in locations:
        if cancel_check and cancel_check():
            raise SaveOperationCancelled()
        path = os.path.abspath(os.path.expanduser(location.path))
        if os.path.isfile(path):
            yield location, path
            continue
        if not os.path.isdir(path):
            continue
        for root, dirs, files in os.walk(path, topdown=True, followlinks=False):
            if cancel_check and cancel_check():
                raise SaveOperationCancelled()
            dirs[:] = sorted(name for name in dirs if not os.path.islink(os.path.join(root, name)))
            for name in sorted(files):
                if cancel_check and cancel_check():
                    raise SaveOperationCancelled()
                file_path = os.path.join(root, name)
                if os.path.isfile(file_path):
                    yield location, file_path


def collect_file_states(locations: Iterable[SaveLocation], cancel_check: Optional[Callable[[], bool]] = None) -> tuple[SaveFileState, ...]:
    states = []
    for _location, path in iter_save_files(locations, cancel_check):
        states.append(SaveFileState.from_path(path))
    return tuple(sorted(states, key=lambda state: state.path))


def fingerprint_states(states: Iterable[SaveFileState]) -> str:
    digest = hashlib.sha256()
    for state in sorted(states, key=lambda item: item.path):
        digest.update(
            f"{state.path}\0{state.size_bytes}\0{state.modified_ns}\0{state.inode}\n".encode("utf-8", "surrogateescape")
        )
    return digest.hexdigest()


@dataclass(frozen=True)
class GameSaveSnapshot:
    """Validated save state handed from detection to packaging/backend code."""

    game_name: str
    game_path: str
    locations: tuple[SaveLocation, ...]
    files: tuple[SaveFileState, ...]
    source: str = "detector"
    validated_at: str = ""
    identity: str = ""
    phase: SaveSnapshotPhase = SaveSnapshotPhase.VALIDATED

    @classmethod
    def from_locations(
        cls,
        game_name: str,
        game_path: str,
        locations: Iterable[SaveLocation],
        *,
        source: str = "detector",
        cancel_check: Optional[Callable[[], bool]] = None,
        files: Optional[tuple[SaveFileState, ...]] = None,
    ) -> "GameSaveSnapshot":
        normalized = tuple(locations)
        collected_files = files if files is not None else collect_file_states(normalized, cancel_check)
        identity_digest = hashlib.sha256()
        identity_digest.update(os.path.abspath(game_path).encode("utf-8", "surrogateescape"))
        identity_digest.update(game_name.encode("utf-8", "surrogateescape"))
        for location in normalized:
            identity_digest.update(os.path.abspath(location.path).encode("utf-8", "surrogateescape"))
        return cls(
            game_name=game_name,
            game_path=os.path.abspath(game_path),
            locations=normalized,
            files=collected_files,
            source=source,
            validated_at=datetime.now(timezone.utc).isoformat(),
            identity=identity_digest.hexdigest(),
        )

    @property
    def file_count(self) -> int:
        return len(self.files)

    @property
    def total_size_bytes(self) -> int:
        return sum(state.size_bytes for state in self.files)

    @property
    def last_modified(self) -> float:
        return max((state.modified_ns / 1_000_000_000 for state in self.files), default=0.0)

    @property
    def fingerprint(self) -> str:
        return fingerprint_states(self.files)

    def with_phase(self, phase: SaveSnapshotPhase) -> "GameSaveSnapshot":
        return replace(self, phase=phase)

    def verify_current(self, cancel_check: Optional[Callable[[], bool]] = None) -> tuple[bool, str]:
        """Detect deletion, replacement, metadata changes, and new files."""
        try:
            current = collect_file_states(self.locations, cancel_check)
        except (OSError, SaveOperationCancelled) as exc:
            if isinstance(exc, SaveOperationCancelled):
                raise
            return False, f"save files could not be rechecked ({exc})"
        if current == self.files:
            return True, ""
        expected = {state.path: state for state in self.files}
        actual = {state.path: state for state in current}
        removed = sorted(set(expected) - set(actual))
        added = sorted(set(actual) - set(expected))
        changed = sorted(path for path in set(expected) & set(actual) if expected[path] != actual[path])
        if removed:
            return False, f"file removed during packaging: {removed[0]}"
        if added:
            return False, f"new file appeared during packaging: {added[0]}"
        if changed:
            return False, f"file changed during packaging: {changed[0]}"
        return False, "save files changed during packaging"

    def verify_file(self, path: str) -> tuple[bool, str]:
        """Check one file immediately before and after it is archived."""
        expected = {state.path: state for state in self.files}.get(os.path.abspath(path))
        if expected is None:
            return False, f"new file appeared during packaging: {path}"
        try:
            current = SaveFileState.from_path(path)
        except OSError as exc:
            return False, f"file became unreadable during packaging ({path}: {exc})"
        if current != expected:
            return False, f"file changed during packaging: {path}"
        return True, ""


@dataclass(frozen=True)
class SaveOperationResult:
    """One result type for upload, restore, history, and export workflows."""

    success: bool
    operation: str
    game_name: str = ""
    error: str = ""
    category: str = "unknown"
    guidance: str = ""
    retry_safe: bool = True
    local_modified: bool = False
    log_path: str = ""
    payload: object = None

    def __bool__(self) -> bool:
        """Keep compatibility with legacy callers that expected a bool."""
        return self.success

    @property
    def title(self) -> str:
        """Compatibility name used by older Save Manager UI code."""
        return self.game_name


__all__ = [
    "GameSaveSnapshot",
    "SaveFileState",
    "SaveOperationCancelled",
    "SaveOperationResult",
    "SaveSnapshotPhase",
    "collect_file_states",
    "fingerprint_states",
    "iter_save_files",
]
