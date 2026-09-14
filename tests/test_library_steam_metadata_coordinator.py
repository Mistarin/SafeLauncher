"""Unit tests for Qt-free Steam metadata grouping."""

from __future__ import annotations

import unittest

from core.library_steam_metadata_coordinator import LibrarySteamMetadataCoordinator
from core.request_contracts import RequestKey, RequestPriority


class _SteamService:
    @staticmethod
    def build_key(app_id):
        return RequestKey("steam-build", str(app_id), "v1")

    @staticmethod
    def tags_key(name):
        return RequestKey("steam-tags", str(name).casefold(), "v1")


class LibrarySteamMetadataCoordinatorTests(unittest.TestCase):
    def test_build_rows_share_app_id_group_and_preserve_local_references(self):
        coordinator = LibrarySteamMetadataCoordinator(_SteamService())
        first = coordinator.prepare_build(
            1, "480", "build-a", 100, priority=RequestPriority.NORMAL
        )
        second = coordinator.prepare_build(
            2, "480", "build-b", 200, priority=RequestPriority.BACKGROUND
        )

        self.assertTrue(first.new_binding)
        self.assertFalse(second.new_binding)
        self.assertEqual(coordinator.build_games(first.key), {
            1: ("build-a", 100),
            2: ("build-b", 200),
        })

    def test_tags_group_case_insensitively_and_cleanup_bindings(self):
        coordinator = LibrarySteamMetadataCoordinator(_SteamService())
        first = coordinator.prepare_tags(3, "Example", priority=RequestPriority.NORMAL)
        second = coordinator.prepare_tags(4, "example", priority=RequestPriority.NORMAL)
        self.assertEqual(first.key, second.key)
        self.assertEqual(coordinator.tag_game_ids(first.key), (3, 4))

        binding = object()
        coordinator.attach_binding("tag", first, binding)
        self.assertIs(coordinator.binding("tag", second.key), binding)
        self.assertEqual(coordinator.close(), [binding])


if __name__ == "__main__":
    unittest.main()
