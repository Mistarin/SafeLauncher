"""Regression tests for persistent achievement-schema fallback behavior."""

from __future__ import annotations

import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from core.achievement_schema import fetch_steam_achievements_schema
from core.cache_policy import cache_policy
from core.request_contracts import RequestKey
from core.resource_cache import ResourceCache


class AchievementSchemaCacheTests(unittest.TestCase):
    def test_stale_shared_schema_remains_usable_offline(self):
        with tempfile.TemporaryDirectory() as directory:
            cache = ResourceCache(Path(directory) / "resources")
            key = RequestKey("achievement-schema", "123", "v1")
            records = [{
                "api_name": "ACH_WIN",
                "display_name": "Win",
                "description": "",
                "hidden": 0,
            }]
            cache.put(
                key,
                records,
                stored_at=time.time() - cache_policy("achievement-schema").max_age_seconds - 1,
            )
            schema_file = Path(directory) / "legacy-schema.json"
            icon_dir = Path(directory) / "icons"

            with patch(
                "core.achievement_schema._ensure_cache_dirs",
                return_value=(schema_file, icon_dir),
            ), patch(
                "core.achievement_schema.find_local_achievement_schema",
                return_value=[],
            ), patch(
                "core.achievement_schema.automatic_network_allowed",
                return_value=False,
            ):
                result = fetch_steam_achievements_schema("123", schema_cache=cache)

            self.assertEqual(result, records)


if __name__ == "__main__":
    unittest.main()
