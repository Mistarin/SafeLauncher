"""Tests for the application boundary around durable achievement writes."""

from __future__ import annotations

import unittest

from core.achievement_persistence_service import AchievementPersistenceService
from core.achievement_providers import AchievementAvailability, AchievementResolution
from database import GameDatabase


class AchievementPersistenceServiceTests(unittest.TestCase):
    def test_unlock_and_notification_claim_are_atomic_from_the_callers_view(self):
        database = GameDatabase(":memory:")
        try:
            game_id = database.add_game("Achievement Service Game", "/missing", "game", "linux")
            service = AchievementPersistenceService(database)
            service.save_schema(game_id, "480", [
                {"api_name": "ACH_FIRST", "display_name": "First"},
                {"api_name": "ACH_SECOND", "display_name": "Second"},
            ])

            first = service.unlock(
                game_id,
                "ACH_FIRST",
                123,
                provenance="steam_verified",
                verified=True,
                source_format="json",
            )
            second = service.unlock(
                game_id,
                "ACH_FIRST",
                456,
                provenance="steam_verified",
                verified=True,
                source_format="json",
            )

            self.assertTrue(first.changed)
            self.assertTrue(first.claimed)
            self.assertEqual(first.projection.unlocked_count, 1)
            self.assertEqual(first.projection.total_count, 2)
            self.assertFalse(second.changed)
            self.assertFalse(second.claimed)
            self.assertEqual(service.schema(game_id)[0]["api_name"], "ACH_FIRST")
        finally:
            database.close()

    def test_state_snapshots_and_resolver_results_share_one_persistence_boundary(self):
        database = GameDatabase(":memory:")
        try:
            game_id = database.add_game("Snapshot Service Game", "/missing", "game", "linux")
            service = AchievementPersistenceService(database)

            snapshot = service.record_state(
                game_id,
                "730",
                {"ACH_SNAPSHOT": 321},
                provenance="steam_verified",
                verified=True,
                source_format="json",
            )
            self.assertTrue(snapshot.changed)
            self.assertEqual(snapshot.projection.total_count, 0)

            resolution = AchievementResolution(
                [{"api_name": "ACH_SNAPSHOT", "display_name": "Snapshot"}],
                {"ACH_SNAPSHOT": 321},
                None,
                "local",
                "local",
                AchievementAvailability.AVAILABLE,
                state_provenance="steam_verified",
                state_verified=True,
                state_format="json",
            )
            changed = service.persist_resolution(game_id, "730", resolution)

            self.assertEqual(changed, 1)
            projection = service.projection(game_id)
            self.assertEqual(projection.unlocked_count, 1)
            self.assertEqual(projection.total_count, 1)
            self.assertEqual(projection.recent[0]["api_name"], "ACH_SNAPSHOT")
        finally:
            database.close()


if __name__ == "__main__":
    unittest.main()
