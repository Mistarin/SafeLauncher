"""Unit tests for Qt-free achievement request orchestration."""

from __future__ import annotations

import unittest

from core.achievement_resource_service import AchievementTarget
from core.library_achievement_coordinator import LibraryAchievementCoordinator
from core.request_contracts import RequestKey


class _AchievementService:
    @staticmethod
    def key_for(target):
        return RequestKey("achievement-status", f"{target.app_id}:{target.game_id}", "v1")


class LibraryAchievementCoordinatorTests(unittest.TestCase):
    def test_target_registers_stable_callback_and_binding_once(self):
        coordinator = LibraryAchievementCoordinator(_AchievementService())
        target = AchievementTarget(7, "480", "/games/example", "/prefix")
        first = coordinator.prepare(target)
        second = coordinator.prepare(target)

        self.assertTrue(first.new_binding)
        self.assertFalse(second.new_binding)
        self.assertEqual(coordinator.callback_data(first.key), (7, "480"))

        binding = object()
        coordinator.attach_binding(first, binding)
        self.assertIs(coordinator.binding(second.key), binding)

    def test_batch_state_and_close_are_owned_by_coordinator(self):
        coordinator = LibraryAchievementCoordinator(_AchievementService())
        coordinator.set_batch_in_flight(True)
        self.assertTrue(coordinator.batch_in_flight)
        target = AchievementTarget(8, "730")
        plan = coordinator.prepare(target)
        binding = object()
        coordinator.attach_binding(plan, binding)

        self.assertEqual(coordinator.close(), [binding])
        self.assertFalse(coordinator.batch_in_flight)
        self.assertIsNone(coordinator.callback_data(plan.key))


if __name__ == "__main__":
    unittest.main()
