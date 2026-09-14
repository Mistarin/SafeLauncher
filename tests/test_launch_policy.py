"""Tests for the Qt-free launch-entry policy."""

from __future__ import annotations

import unittest

from core.launch_policy import LaunchAction, LaunchPolicy


def _game(*, mode="", archived=False):
    row = [1, "Example", "/games/example", "run.sh", mode]
    row.extend([""] * 12)
    row.append(1 if archived else 0)
    return tuple(row)


class LaunchPolicyTests(unittest.TestCase):
    def test_archived_row_wins_over_other_launch_conditions(self):
        decision = LaunchPolicy.decide(
            _game(mode="sandbox", archived=True),
            is_running=True,
            shift_pressed=True,
        )
        self.assertEqual(decision.action, LaunchAction.RESTORE)

    def test_running_row_requests_stop(self):
        decision = LaunchPolicy.decide(_game(mode="sandbox"), is_running=True, shift_pressed=False)
        self.assertEqual(decision.action, LaunchAction.STOP)

    def test_configured_mode_launches_without_dialog(self):
        decision = LaunchPolicy.decide(_game(mode="sandbox"), is_running=False, shift_pressed=False)
        self.assertEqual(decision.action, LaunchAction.LAUNCH)
        self.assertEqual(decision.mode, "sandbox")

    def test_shift_requests_mode_selection(self):
        decision = LaunchPolicy.decide(_game(mode="sandbox"), is_running=False, shift_pressed=True)
        self.assertEqual(decision.action, LaunchAction.CHOOSE_MODE)

    def test_empty_mode_requests_mode_selection(self):
        decision = LaunchPolicy.decide(_game(), is_running=False, shift_pressed=False)
        self.assertEqual(decision.action, LaunchAction.CHOOSE_MODE)


if __name__ == "__main__":
    unittest.main()
