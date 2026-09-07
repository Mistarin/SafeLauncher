import os
import shutil
import threading
import time


from collections import OrderedDict
from typing import Optional, Tuple


class DirectorySizeLRUCache:
    """Thread-safe in-memory LRU cache for directory sizes with TTL expiry."""

    def __init__(self, maxsize: int = 512, ttl_seconds: float = 600.0):
        self.maxsize = maxsize
        self.ttl_seconds = ttl_seconds
        self._cache: OrderedDict[str, Tuple[float, int]] = OrderedDict()
        self._lock = threading.Lock()

    def _normalize_key(self, dir_path: str) -> str:
        try:
            return os.path.abspath(os.path.normpath(dir_path))
        except Exception:
            return str(dir_path)

    def get(self, dir_path: str) -> Optional[int]:
        if not dir_path:
            return None
        key = self._normalize_key(dir_path)
        now = time.monotonic()
        with self._lock:
            entry = self._cache.get(key)
            if entry is None:
                return None
            ts, size = entry
            if (now - ts) < self.ttl_seconds:
                self._cache.move_to_end(key)
                return size
            # Expired entry
            self._cache.pop(key, None)
            return None

    def put(self, dir_path: str, size_bytes: int) -> None:
        if not dir_path:
            return
        key = self._normalize_key(dir_path)
        now = time.monotonic()
        with self._lock:
            if key in self._cache:
                self._cache.move_to_end(key)
            self._cache[key] = (now, int(size_bytes))
            while len(self._cache) > self.maxsize:
                self._cache.popitem(last=False)

    def invalidate(self, dir_path: str) -> None:
        """Evict a specific directory path from cache."""
        if not dir_path:
            return
        key = self._normalize_key(dir_path)
        with self._lock:
            self._cache.pop(key, None)

    def clear(self) -> None:
        with self._lock:
            self._cache.clear()

    def __len__(self) -> int:
        with self._lock:
            return len(self._cache)


_DIR_SIZE_LRU = DirectorySizeLRUCache(maxsize=512, ttl_seconds=600.0)


def peek_dir_size(dir_path: str) -> Optional[int]:
    """Return the cached directory size if fresh, otherwise None.

    Never touches the disk, so GUI-thread callers can order or display sizes
    while the actual calculation runs in worker threads (see store_dir_size,
    used by DiskSizeFetcherThread).
    """
    return _DIR_SIZE_LRU.get(dir_path)


def has_fresh_dir_size(dir_path: str) -> bool:
    return peek_dir_size(dir_path) is not None


def store_dir_size(dir_path: str, size_bytes: int) -> None:
    """Publish a computed directory size to the LRU cache (thread-safe)."""
    _DIR_SIZE_LRU.put(dir_path, size_bytes)


def clear_dir_size_cache() -> None:
    """Clear all entries from the directory size LRU cache."""
    _DIR_SIZE_LRU.clear()


def invalidate_dir_size(dir_path: str) -> None:
    """Evict a specific directory from the LRU size cache."""
    _DIR_SIZE_LRU.invalidate(dir_path)


def dir_size_display(dir_path: str) -> str:
    """Human-readable size from cache, or an ellipsis while unknown."""
    size = peek_dir_size(dir_path)
    if size is None:
        return "…"
    return format_size(size)


def get_dir_size(dir_path: str, use_cache: bool = True) -> int:
    """Recursively calculate regular-file size without escaping via symlinks.

    Always updates the size cache with the computed result so that background
    threads and on-demand callers share the same cached value.  The result is
    only cached when the traversal completes without an outer exception — a
    partial traversal due to an error would otherwise cache ``0`` for a valid
    non-empty directory.
    """
    if not dir_path or not os.path.exists(dir_path):
        return 0
    if use_cache:
        cached = peek_dir_size(dir_path)
        if cached is not None:
            return cached

    total_size = 0
    traversal_ok = False
    try:
        if os.path.isfile(dir_path):
            total_size = os.path.getsize(dir_path)
            store_dir_size(dir_path, total_size)
            return total_size

        seen_inodes = set()
        pending = [os.path.realpath(dir_path)]
        while pending:
            current = pending.pop()
            try:
                current_stat = os.stat(current, follow_symlinks=False)
                current_key = (current_stat.st_dev, current_stat.st_ino)
                if current_key in seen_inodes:
                    continue
                seen_inodes.add(current_key)
                with os.scandir(current) as entries:
                    for entry in entries:
                        try:
                            # Never descend through directory symlinks. Game
                            # prefixes commonly contain a self-link (pfx -> .).
                            if entry.is_dir(follow_symlinks=False):
                                pending.append(entry.path)
                                continue
                            if entry.is_symlink() or not entry.is_file(follow_symlinks=False):
                                continue
                            st = entry.stat(follow_symlinks=False)
                            inode_key = (st.st_dev, st.st_ino)
                            if inode_key not in seen_inodes:
                                seen_inodes.add(inode_key)
                                total_size += st.st_size
                        except (OSError, FileNotFoundError):
                            continue
            except (OSError, FileNotFoundError, NotADirectoryError):
                continue
        traversal_ok = True
    except Exception:
        pass

    # Only cache when the traversal actually completed — a mid-traversal
    # exception leaves total_size at 0 or partial, which must not be cached
    # as the authoritative size for the directory.
    if traversal_ok:
        store_dir_size(dir_path, total_size)
    return total_size




def format_size(size_bytes: int) -> str:
    """Format bytes to human-readable size string (e.g. 4.2 GB, 450 MB)."""
    if size_bytes <= 0:
        return "0 B"
    units = ["B", "KB", "MB", "GB", "TB"]
    size = float(size_bytes)
    idx = 0
    while size >= 1024.0 and idx < len(units) - 1:
        size /= 1024.0
        idx += 1
    if idx == 0:
        return f"{int(size)} B"
    return f"{size:.1f} {units[idx]}"


def get_disk_usage(path: str) -> tuple:
    """Return (total_bytes, used_bytes, free_bytes) for the filesystem containing path."""
    try:
        usage = shutil.disk_usage(path)
        return usage.total, usage.used, usage.free
    except Exception:
        return 0, 0, 0
