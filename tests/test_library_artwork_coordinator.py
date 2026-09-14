"""Unit tests for Qt-free library artwork orchestration."""

from __future__ import annotations

import unittest

from core.artwork_resource_service import ArtworkTarget
from core.library_artwork_coordinator import LibraryArtworkCoordinator
from core.request_contracts import RequestKey, RequestPriority, RequestSpec


class _ArtworkService:
    def __init__(self):
        self.requests = []

    def _spec(self, kind, target, priority):
        return RequestSpec(
            RequestKey("artwork-" + kind, target.identity, "v1"),
            lambda _token: None,
            priority=priority,
        )

    def auto_spec(self, target, *, priority):
        return self._spec("auto", target, priority)

    def hero_spec(self, target, *, priority):
        return self._spec("hero", target, priority)

    def icon_spec(self, target, *, priority):
        return self._spec("icon", target, priority)

    def request_auto(self, target, *, priority):
        self.requests.append(("auto", target, priority))

    def request_hero(self, target, *, priority):
        self.requests.append(("hero", target, priority))

    def request_icon(self, target, *, priority):
        self.requests.append(("icon", target, priority))


class LibraryArtworkCoordinatorTests(unittest.TestCase):
    def test_shared_identity_joins_one_group_and_one_binding(self):
        service = _ArtworkService()
        coordinator = LibraryArtworkCoordinator(service)
        first = coordinator.prepare(
            "hero",
            ArtworkTarget(1, "Example", "480"),
            priority=RequestPriority.NORMAL,
            mark_attempted=True,
        )
        second = coordinator.prepare(
            "hero",
            ArtworkTarget(2, "Example", "480"),
            priority=RequestPriority.BACKGROUND,
            mark_attempted=True,
        )

        self.assertIsNotNone(first)
        self.assertIsNotNone(second)
        self.assertTrue(first.new_binding)
        self.assertFalse(second.new_binding)
        self.assertEqual(first.key, second.key)
        self.assertEqual(coordinator.game_ids("hero", first.key), (1, 2))

        binding = object()
        coordinator.attach_binding(first, binding)
        self.assertIs(coordinator.binding("hero", second.key), binding)
        coordinator.request(first)
        self.assertEqual(service.requests[0][0], "hero")

    def test_attempted_game_is_not_resubmitted_but_different_kind_is_allowed(self):
        coordinator = LibraryArtworkCoordinator(_ArtworkService())
        target = ArtworkTarget(7, "Example", "480")
        first = coordinator.prepare(
            "hero", target, priority=RequestPriority.NORMAL, mark_attempted=True
        )
        duplicate = coordinator.prepare(
            "hero", target, priority=RequestPriority.CRITICAL, mark_attempted=True
        )
        icon = coordinator.prepare(
            "icon", target, priority=RequestPriority.NORMAL, mark_attempted=True
        )

        self.assertIsNotNone(first)
        self.assertIsNone(duplicate)
        self.assertIsNotNone(icon)

    def test_automatic_artwork_attempts_are_owned_by_coordinator(self):
        coordinator = LibraryArtworkCoordinator(_ArtworkService())
        target = ArtworkTarget(11, "Example", "480")
        first = coordinator.prepare(
            "auto", target, priority=RequestPriority.BACKGROUND, mark_attempted=True
        )
        duplicate = coordinator.prepare(
            "auto", target, priority=RequestPriority.BACKGROUND, mark_attempted=True
        )
        self.assertIsNotNone(first)
        self.assertIsNone(duplicate)

    def test_discard_and_close_return_opaque_bindings_and_clear_groups(self):
        coordinator = LibraryArtworkCoordinator(_ArtworkService())
        plan = coordinator.prepare(
            "auto",
            ArtworkTarget(3, "Example", "480"),
            priority=RequestPriority.BACKGROUND,
        )
        binding = object()
        coordinator.attach_binding(plan, binding)
        self.assertIs(coordinator.discard("auto", plan.key), binding)
        self.assertEqual(coordinator.game_ids("auto", plan.key), ())

        second = coordinator.prepare(
            "auto",
            ArtworkTarget(4, "Another", "730"),
            priority=RequestPriority.BACKGROUND,
        )
        second_binding = object()
        coordinator.attach_binding(second, second_binding)
        self.assertEqual(coordinator.close(), [second_binding])
        self.assertEqual(coordinator.game_ids("auto", second.key), ())


if __name__ == "__main__":
    unittest.main()
