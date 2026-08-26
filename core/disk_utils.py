import os
import shutil
import threading
import time


_DIR_SIZE_CACHE = {}
_DIR_SIZE_LOCK = threading.Lock()
_DIR_SIZE_TTL_SECONDS = 600.0


def peek_dir_size(dir_path: str):
    """Return the cached directory size if fresh, otherwise None.

    Never touches the disk, so GUI-thread callers can order or display sizes
    while the actual calculation runs in worker threads (see store_dir_size,
    used by DiskSizeFetcherThread).
    """
    if not dir_path:
        return None
    key = os.path.abspath(dir_path)
    now = time.monotonic()
    with _DIR_SIZE_LOCK:
        entry = _DIR_SIZE_CACHE.get(key)
        if entry and (now - entry[0]) < _DIR_SIZE_TTL_SECONDS:
            return entry[1]
    return None


def has_fresh_dir_size(dir_path: str) -> bool:
    return peek_dir_size(dir_path) is not None


def store_dir_size(dir_path: str, size_bytes: int) -> None:
    """Publish a computed directory size for reuse across the UI (thread-safe)."""
    if not dir_path:
        return
    key = os.path.abspath(dir_path)
    with _DIR_SIZE_LOCK:
        _DIR_SIZE_CACHE[key] = (time.monotonic(), int(size_bytes))


def dir_size_display(dir_path: str) -> str:
    """Human-readable size from cache, or an ellipsis while unknown."""
    size = peek_dir_size(dir_path)
    if size is None:
        return "…"
    return format_size(size)


def get_dir_size(dir_path: str) -> int:
    """Recursively calculate regular-file size without escaping via symlinks."""
    if not dir_path or not os.path.exists(dir_path):
        return 0
    total_size = 0
    try:
        if os.path.isfile(dir_path):
            return os.path.getsize(dir_path)
        
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
    except Exception:
        pass
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
