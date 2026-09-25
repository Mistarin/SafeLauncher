"""Small bounded memory/disk cache for request-manager resources."""

from __future__ import annotations

import base64
import hashlib
import json
import os
import shutil
import tempfile
import threading
import time
from collections import OrderedDict
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from core.request_contracts import RequestKey


@dataclass(frozen=True, slots=True)
class CacheEntry:
    key: RequestKey
    value: Any
    stored_at: float
    content_type: str = "application/json"
    source: str = "memory"

    def age(self, now: float | None = None) -> float:
        return max(0.0, (time.time() if now is None else now) - self.stored_at)

    def is_fresh(self, max_age_seconds: float, now: float | None = None) -> bool:
        return self.age(now) <= max(0.0, float(max_age_seconds))


class ResourceCache:
    """LRU memory cache backed by bounded JSON/bytes files on disk."""

    def __init__(
        self,
        directory: str | os.PathLike | None = None,
        *,
        max_entries: int = 256,
        max_disk_bytes: int = 64 * 1024 * 1024,
        legacy_directories: tuple[str | os.PathLike, ...] = (),
    ):
        if max_entries < 1 or max_disk_bytes < 1:
            raise ValueError("Cache limits must be positive")
        self.directory = Path(directory) if directory else None
        self.max_entries = int(max_entries)
        self.max_disk_bytes = int(max_disk_bytes)
        self._lock = threading.RLock()
        self._memory: OrderedDict[str, CacheEntry] = OrderedDict()
        if self.directory:
            try:
                self.directory.mkdir(parents=True, exist_ok=True)
                self.directory.chmod(0o700)
            except OSError:
                # Disk persistence is an optimization, not a startup
                # requirement. Sandboxed/read-only environments still get a
                # fully functional bounded memory cache.
                self.directory = None
            else:
                self._migrate_legacy_directories(legacy_directories)

    def get(self, key: RequestKey) -> CacheEntry | None:
        with self._lock:
            cache_key = key.cache_key()
            entry = self._memory.get(cache_key)
            if entry is not None:
                self._memory.move_to_end(cache_key)
                return entry
            if not self.directory:
                return None
            path = self._path_for(key)
            try:
                if path.stat().st_size > self.max_disk_bytes:
                    return None
                with path.open("r", encoding="utf-8") as handle:
                    raw = json.load(handle)
                if raw.get("cache_key") != cache_key:
                    return None
                value = self._decode_value(raw.get("encoding"), raw.get("value"))
                entry = CacheEntry(
                    key,
                    value,
                    float(raw.get("stored_at", 0) or 0),
                    str(raw.get("content_type", "application/json")),
                    "disk",
                )
                self._remember(entry)
                return entry
            except (OSError, ValueError, TypeError, json.JSONDecodeError):
                return None

    def put(self, key: RequestKey, value: Any, *, content_type: str = "application/json", stored_at: float | None = None) -> CacheEntry:
        with self._lock:
            entry = CacheEntry(key, value, float(time.time() if stored_at is None else stored_at), content_type)
            self._remember(entry)
            if self.directory:
                encoded, payload = self._encode_value(value)
                if encoded is not None:
                    document = {
                        "cache_key": key.cache_key(),
                        "stored_at": entry.stored_at,
                        "content_type": content_type,
                        "encoding": encoded,
                        "value": payload,
                    }
                    try:
                        self._write_atomic(self._path_for(key), document)
                        self._trim_disk()
                    except (OSError, TypeError, ValueError):
                        # A cache write must never turn a successful network
                        # result into a failed resource. Keep the bounded
                        # memory entry and degrade this cache instance to
                        # memory-only if its disk has become unavailable.
                        self.directory = None
            return entry

    def remove(self, key: RequestKey) -> None:
        with self._lock:
            cache_key = key.cache_key()
            self._memory.pop(cache_key, None)
            if self.directory:
                try:
                    self._path_for(key).unlink()
                except OSError:
                    pass

    def invalidate(self, key: RequestKey) -> None:
        """Remove one resource from both memory and disk.

        ``invalidate`` is the resource-manager vocabulary. ``remove`` remains
        available for callers that treat the cache as a plain mapping.
        """
        self.remove(key)

    def clear(self) -> None:
        with self._lock:
            self._memory.clear()
            if self.directory:
                for path in self.directory.glob("*.json"):
                    try:
                        path.unlink()
                    except OSError:
                        pass

    def _remember(self, entry: CacheEntry) -> None:
        cache_key = entry.key.cache_key()
        self._memory[cache_key] = entry
        self._memory.move_to_end(cache_key)
        while len(self._memory) > self.max_entries:
            self._memory.popitem(last=False)

    def _path_for(self, key: RequestKey) -> Path:
        digest = hashlib.sha256(key.cache_key().encode("utf-8")).hexdigest()
        return self.directory / f"{digest}.json"

    def _migrate_legacy_directories(
        self,
        legacy_directories: tuple[str | os.PathLike, ...],
    ) -> None:
        """Import old hashed resource files without trusting their contents."""
        if not self.directory:
            return
        for raw_directory in legacy_directories:
            source_directory = Path(raw_directory)
            if source_directory == self.directory or not source_directory.is_dir():
                continue
            try:
                for source in source_directory.glob("*.json"):
                    target = self.directory / source.name
                    source_valid = self._is_cache_document(source)
                    target_valid = target.exists() and self._is_cache_document(target)
                    if source_valid and not target_valid:
                        temporary = self.directory / f".migrate-{source.name}.tmp"
                        try:
                            shutil.copyfile(source, temporary)
                            # ``copyfile`` creates the destination using the
                            # process umask, which can turn a private legacy
                            # cache entry into a world-readable file. Cache
                            # envelopes may contain account-specific data, so
                            # preserve the cache's private-file boundary.
                            os.chmod(temporary, 0o600)
                            os.replace(temporary, target)
                        finally:
                            try:
                                temporary.unlink()
                            except OSError:
                                pass
                        target_valid = True
                    if source_valid and target_valid:
                        try:
                            os.chmod(target, 0o600)
                        except OSError:
                            pass
                        try:
                            source.unlink()
                        except OSError:
                            pass
                try:
                    if not any(source_directory.iterdir()):
                        source_directory.rmdir()
                except OSError:
                    pass
            except OSError:
                # Cache migration is best effort and must never block startup.
                continue

    @staticmethod
    def _is_cache_document(path: Path) -> bool:
        """Return whether a legacy file has the shared cache envelope shape."""
        try:
            with path.open("r", encoding="utf-8") as handle:
                document = json.load(handle)
            return (
                isinstance(document, dict)
                and isinstance(document.get("cache_key"), str)
                and document.get("encoding") in {"json", "base64"}
                and "value" in document
            )
        except (OSError, TypeError, ValueError, json.JSONDecodeError):
            return False

    @staticmethod
    def _encode_value(value: Any) -> tuple[str | None, Any]:
        if isinstance(value, bytes):
            return "base64", base64.b64encode(value).decode("ascii")
        try:
            json.dumps(value, separators=(",", ":"))
        except (TypeError, ValueError):
            return None, None
        return "json", value

    @staticmethod
    def _decode_value(encoding: str, value: Any) -> Any:
        if encoding == "base64":
            return base64.b64decode(str(value).encode("ascii"), validate=True)
        if encoding == "json":
            return value
        raise ValueError("Unknown cache encoding")

    @staticmethod
    def _write_atomic(path: Path, document: dict) -> None:
        fd, temp_path = tempfile.mkstemp(prefix=".resource-", suffix=".tmp", dir=str(path.parent))
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                json.dump(document, handle, separators=(",", ":"), sort_keys=True)
                handle.flush()
                os.fsync(handle.fileno())
            os.chmod(temp_path, 0o600)
            os.replace(temp_path, path)
            os.chmod(path, 0o600)
        finally:
            try:
                os.unlink(temp_path)
            except OSError:
                pass

    def _trim_disk(self) -> None:
        if not self.directory:
            return
        files = []
        total = 0
        for path in self.directory.glob("*.json"):
            try:
                stat = path.stat()
                files.append((stat.st_mtime, stat.st_size, path))
                total += stat.st_size
            except OSError:
                continue
        for _mtime, size, path in sorted(files):
            if total <= self.max_disk_bytes:
                break
            try:
                path.unlink()
                total -= size
            except OSError:
                pass


__all__ = ["CacheEntry", "ResourceCache"]
