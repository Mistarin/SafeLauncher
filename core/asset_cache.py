"""Bounded pruning for validated legacy/materialized asset directories.

These helpers intentionally do not know request keys, freshness, transport,
or account state. They only enforce a finite local disk budget for asset files
that are also represented by the shared resource path when available.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True, slots=True)
class AssetCacheBudget:
    max_files: int
    max_bytes: int

    def __post_init__(self) -> None:
        if int(self.max_files) < 1 or int(self.max_bytes) < 1:
            raise ValueError("Asset cache budgets must be positive")


def prune_asset_cache(root: str | Path, budget: AssetCacheBudget, *, recursive: bool = True) -> int:
    """Prune oldest regular files until ``budget`` is satisfied.

    Symlinks and directories are never followed or removed. A failure to
    inspect/remove one file is ignored because cache eviction is an
    optimization and must never turn a successful asset load into an error.
    Returns the number of files removed.
    """
    directory = Path(root)
    try:
        paths = directory.rglob("*") if recursive else directory.glob("*")
        entries = []
        for path in paths:
            try:
                if path.is_symlink() or not path.is_file():
                    continue
                stat = path.stat()
                entries.append((float(stat.st_mtime), int(stat.st_size), path))
            except OSError:
                continue
    except OSError:
        return 0

    entries.sort(key=lambda item: (item[0], str(item[2])))
    total = sum(size for _mtime, size, _path in entries)
    keep_count = len(entries)
    removed = 0
    for _mtime, size, path in entries:
        if keep_count <= budget.max_files and total <= budget.max_bytes:
            break
        try:
            path.unlink()
        except OSError:
            continue
        keep_count -= 1
        total -= size
        removed += 1
    return removed


__all__ = ["AssetCacheBudget", "prune_asset_cache"]
