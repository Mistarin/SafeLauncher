"""Local-first achievement resolution regression tests."""

from __future__ import annotations

import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from core.achievement_providers import AchievementAvailability, AchievementProviderRegistry


class AchievementProviderTests(unittest.TestCase):
    def test_rune_zero_count_file_is_valid_empty_local_state(self):
        with tempfile.TemporaryDirectory() as directory:
            game = Path(directory) / "game"
            prefix = game / "prefix"
            state_file = (
                prefix / "drive_c/users/Public/Documents/Steam/RUNE/2725260/achievements.ini"
            )
            state_file.parent.mkdir(parents=True)
            state_file.write_text("[SteamAchievements]\nCount=0\n", encoding="utf-8")

            class Definitions:
                name = "test-local-definitions"

                @staticmethod
                def get_schema(*_args, **_kwargs):
                    return [{"api_name": "ACH_FIRST", "display_name": "First"}]

            with patch.object(AchievementProviderRegistry, "schema_providers", (Definitions(),)), patch.dict(
                os.environ,
                {"STEAM_WEB_API_KEY": "must-not-be-used", "STEAM_USER_ID": "76561198000000000"},
            ):
                result = AchievementProviderRegistry.resolve("2725260", str(game), "")

            self.assertEqual(result.availability, AchievementAvailability.AVAILABLE)
            self.assertTrue(result.state_available)
            self.assertEqual(result.state, {})
            self.assertEqual(result.state_source, "local-state")
            self.assertEqual(result.state_provenance, "local_emulator")
            self.assertFalse(result.state_verified)
            self.assertEqual(result.state_path, state_file)
            self.assertEqual(result.state_adapter, "codex-rune-steamachievements-ini")
            self.assertTrue(result.state_selection_reason)
            self.assertEqual(result.state_contributing_paths, [str(state_file)])

    def test_rune_local_unlock_is_resolved_without_steam_player_state(self):
        with tempfile.TemporaryDirectory() as directory:
            game = Path(directory) / "game"
            prefix = game / "prefix"
            state_file = (
                prefix / "drive_c/users/Public/Documents/Steam/RUNE/2725260/achievements.ini"
            )
            state_file.parent.mkdir(parents=True)
            state_file.write_text(
                "[SteamAchievements]\nCount=1\nACH_FIRST=1712345678\n",
                encoding="utf-8",
            )

            class Definitions:
                name = "test-local-definitions"

                @staticmethod
                def get_schema(*_args, **_kwargs):
                    return [{"api_name": "ACH_FIRST", "display_name": "First"}]

            with patch.object(AchievementProviderRegistry, "schema_providers", (Definitions(),)), patch.dict(
                os.environ,
                {"STEAM_WEB_API_KEY": "must-not-be-used", "STEAM_USER_ID": "76561198000000000"},
            ):
                result = AchievementProviderRegistry.resolve("2725260", str(game), "")

            self.assertEqual(result.state, {"ACH_FIRST": 1712345678.0})
            self.assertEqual(result.state_source, "local-state")
            self.assertEqual(result.state_provenance, "local_emulator")
            self.assertFalse(result.state_verified)


if __name__ == "__main__":
    unittest.main()
