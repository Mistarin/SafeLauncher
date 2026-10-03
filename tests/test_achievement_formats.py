"""Golden-fixture coverage for supported local achievement file formats."""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from core.achievement_watcher import (
    locate_achievements_file_with_reason,
    parse_achievements_state_detailed,
)


FIXTURES = Path(__file__).parent / "fixtures" / "achievements"


class AchievementFormatFixtureTests(unittest.TestCase):
    def test_rune_timestamp_ini_is_detected_and_count_is_not_an_unlock(self):
        result = parse_achievements_state_detailed(FIXTURES / "rune_achievements.ini")
        self.assertTrue(result.valid)
        self.assertEqual(result.format, "ini")
        self.assertEqual(result.adapter, "codex-rune-steamachievements-ini")
        self.assertEqual(result.state, {
            "ACH_FIRST_BLOOD": 1712345678.0,
            "ACH_NO_DAMAGE": 1712345680.0,
        })

    def test_codex_boolean_ini_is_detected(self):
        result = parse_achievements_state_detailed(FIXTURES / "codex_achievements.ini")
        self.assertTrue(result.valid)
        self.assertEqual(result.adapter, "codex-rune-steamachievements-ini")
        self.assertEqual(result.state, {"ACH_FIRST_BLOOD": 1.0})

    def test_goldberg_map_json_is_detected(self):
        result = parse_achievements_state_detailed(FIXTURES / "goldberg_achievements.json")
        self.assertTrue(result.valid)
        self.assertEqual(result.adapter, "goldberg-gse-map-json")
        self.assertEqual(result.state, {"ACH_FIRST_BLOOD": 1712345678.0})

    def test_gse_container_json_ignores_unrelated_counters(self):
        result = parse_achievements_state_detailed(FIXTURES / "gse_container_achievements.json")
        self.assertTrue(result.valid)
        self.assertEqual(result.adapter, "goldberg-gse-container-json")
        self.assertEqual(result.state, {"ACH_FIRST_BLOOD": 1712345678.0})

    def test_ini_adapter_rejects_arbitrary_sections_instead_of_guessing(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "achievements.ini"
            path.write_text("[AchievementTelemetry]\nACH_FALSE_POSITIVE=1\n", encoding="utf-8")
            result = parse_achievements_state_detailed(path)
        self.assertFalse(result.valid)
        self.assertEqual(result.adapter, "unsupported-ini")
        self.assertIn("no supported", result.reason)
        self.assertEqual(result.state, {})

    def test_json_adapter_rejects_unrelated_game_statistics(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "stats.json"
            path.write_text('{"stats":{"KILLS":42,"PLAYTIME":3600}}', encoding="utf-8")
            result = parse_achievements_state_detailed(path)
        self.assertFalse(result.valid)
        self.assertEqual(result.adapter, "unsupported-json")
        self.assertEqual(result.state, {})

    def test_json_array_adapter_requires_explicit_achievement_fields(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "stats.json"
            path.write_text('[{"name":"KILLS","value":42}]', encoding="utf-8")
            result = parse_achievements_state_detailed(path)
        self.assertFalse(result.valid)
        self.assertEqual(result.adapter, "unsupported-json")
        self.assertEqual(result.state, {})

    def test_malformed_input_is_reported_not_reinterpreted(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "achievements.json"
            path.write_text('{"achievements": ', encoding="utf-8")
            result = parse_achievements_state_detailed(path)
        self.assertFalse(result.valid)
        self.assertIn("invalid JSON", result.reason)

    def test_selection_returns_a_human_readable_reason_without_creating_files(self):
        with tempfile.TemporaryDirectory() as directory:
            game = Path(directory) / "game"
            prefix = game / "prefix"
            candidate = prefix / "drive_c/users/Public/Documents/Steam/RUNE/123/achievements.ini"
            candidate.parent.mkdir(parents=True)
            candidate.write_text("[SteamAchievements]\nCount=0\n", encoding="utf-8")
            selected, reason = locate_achievements_file_with_reason(str(prefix), str(game), "123")
        self.assertEqual(selected, candidate)
        self.assertIn("No candidate contains parsed unlocks", reason)


if __name__ == "__main__":
    unittest.main()
