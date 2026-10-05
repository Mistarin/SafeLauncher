"""Regression tests for persistent achievement-schema fallback behavior."""

from __future__ import annotations

import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from core.achievement_schema import (
    _community_api_name,
    _fetch_steam_community_html,
    _valid_icon_path_map,
    fetch_steam_achievements_schema,
)
from core.cache_policy import cache_policy
from core.request_contracts import RequestKey
from core.resource_cache import ResourceCache


class AchievementSchemaCacheTests(unittest.TestCase):
    def test_stale_shared_schema_survives_online_refresh_failure(self):
        with tempfile.TemporaryDirectory() as directory:
            cache = ResourceCache(Path(directory) / "resources")
            key = RequestKey("achievement-schema", "123", "v1")
            records = [{"api_name": "ACH_WIN", "display_name": "Win", "description": "", "hidden": 0}]
            cache.put(
                key,
                records,
                stored_at=time.time() - cache_policy("achievement-schema").max_age_seconds - 1,
            )
            response = type("Response", (), {"status_code": 503, "content": b"", "text": "", "close": lambda self: None})()
            session = type("Session", (), {"get": lambda self, *args, **kwargs: response})()
            with patch("core.achievement_schema._ensure_cache_dirs", return_value=(Path(directory) / "legacy.json", Path(directory) / "icons")), \
                 patch("core.achievement_schema.find_local_achievement_schema", return_value=[]), \
                 patch("core.achievement_schema.automatic_network_allowed", return_value=True), \
                 patch("core.achievement_schema._get_http_session", return_value=session), \
                 patch("core.achievement_schema._COMMUNITY_RATE_LIMITER.acquire", return_value=True):
                result = fetch_steam_achievements_schema("123", schema_cache=cache)
            self.assertEqual(result, records)

    def test_community_names_remain_unique_for_duplicate_titles(self):
        used = set()
        first = _community_api_name("Same Title", 0, used)
        second = _community_api_name("Same Title", 1, used)
        self.assertNotEqual(first, second)
        self.assertEqual({first, second}, {"SAME_TITLE", "SAME_TITLE_2"})

    def test_html_fallback_keeps_duplicate_titles_and_marks_single_icon(self):
        row = (
            '<div class="achieveImgHolder"><img src="https://example.test/icon.png"></div>'
            '<div class="achieveTxtHolder"><div class="achieveTxt">'
            '<h3>Same Title</h3><h5>Do the same thing</h5></div></div>'
        )
        html = row + row
        response = type("Response", (), {
            "status_code": 200,
            "content": html.encode(),
            "text": html,
            "close": lambda self: None,
        })()
        session = type("Session", (), {"get": lambda self, *args, **kwargs: response})()
        with patch("core.achievement_schema._HAS_BS4", False), \
             patch("core.achievement_schema._get_http_session", return_value=session), \
             patch("core.achievement_schema._COMMUNITY_RATE_LIMITER.acquire", return_value=True):
            result = _fetch_steam_community_html("123")
        self.assertEqual(len(result), 2)
        self.assertNotEqual(result[0]["api_name"], result[1]["api_name"])
        self.assertTrue(all(not item["icongray_url"] for item in result))

    def test_icon_cache_validation_rejects_empty_and_partial_maps(self):
        with tempfile.TemporaryDirectory() as directory:
            icon = Path(directory) / "icon.png"
            icon.write_bytes(b"image")
            achievement = {
                "icon_url": "https://example.test/color.png",
                "icongray_url": "https://example.test/gray.png",
            }
            self.assertFalse(_valid_icon_path_map({}, achievement))
            self.assertFalse(_valid_icon_path_map({"icon_path": str(icon)}, achievement))
            self.assertTrue(_valid_icon_path_map({"icon_path": str(icon), "icongray_path": str(icon)}, achievement))

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
