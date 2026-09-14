"""Tests for bounded, symlink-safe materialized asset cache pruning."""

from __future__ import annotations

import os
from pathlib import Path
import tempfile
import unittest

from core.asset_cache import AssetCacheBudget, prune_asset_cache


class AssetCacheTests(unittest.TestCase):
    def test_pruner_enforces_file_and_byte_budget_oldest_first(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            files = []
            for index in range(4):
                path = root / f"asset-{index}.bin"
                path.write_bytes(bytes([index]) * 4)
                os.utime(path, (index + 1, index + 1))
                files.append(path)
            removed = prune_asset_cache(root, AssetCacheBudget(max_files=2, max_bytes=8))
            self.assertEqual(removed, 2)
            self.assertFalse(files[0].exists())
            self.assertFalse(files[1].exists())
            self.assertTrue(files[2].exists())
            self.assertTrue(files[3].exists())

    def test_pruner_does_not_follow_or_remove_symlinks(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            outside = Path(temporary).parent / f"asset-cache-outside-{os.getpid()}"
            outside.write_bytes(b"keep")
            link = root / "link.bin"
            try:
                link.symlink_to(outside)
                prune_asset_cache(root, AssetCacheBudget(max_files=1, max_bytes=1))
                self.assertTrue(link.is_symlink())
                self.assertTrue(outside.exists())
            finally:
                link.unlink(missing_ok=True)
                outside.unlink(missing_ok=True)


if __name__ == "__main__":
    unittest.main()
