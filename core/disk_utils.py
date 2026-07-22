import os
import shutil


def get_dir_size(dir_path: str) -> int:
    """Recursively calculate total disk size of a directory in bytes."""
    if not dir_path or not os.path.exists(dir_path):
        return 0
    total_size = 0
    try:
        if os.path.isfile(dir_path):
            return os.path.getsize(dir_path)
        for root, _, files in os.walk(dir_path):
            for f in files:
                fp = os.path.join(root, f)
                if not os.path.islink(fp):
                    total_size += os.path.getsize(fp)
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
